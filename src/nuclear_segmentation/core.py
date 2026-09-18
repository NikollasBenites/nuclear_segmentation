from __future__ import annotations
import hashlib
import importlib.metadata
import json
import platform
import re
from pathlib import Path
from time import perf_counter
from xml.etree import ElementTree as ET
import numpy as np
import pandas as pd
import tifffile
import skimage as ski
from scipy import ndimage as ndi
from skimage.measure import marching_cubes, mesh_surface_area, regionprops

NOMINAL_SECTION_THICKNESS_UM = 60.0
UNIT_TO_UM = {
    "µm": 1.0,
    "um": 1.0,
    "micron": 1.0,
    "microns": 1.0,
    "micrometer": 1.0,
    "micrometers": 1.0,
    "nm": 1e-3,
    "nanometer": 1e-3,
    "nanometers": 1e-3,
    "mm": 1e3,
    "millimeter": 1e3,
    "millimeters": 1e3,
    "cm": 1e4,
    "centimeter": 1e4,
    "centimeters": 1e4,
    "m": 1e6,
    "meter": 1e6,
    "meters": 1e6,
}


def safe_key(value: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_")
    if not key:
        raise ValueError(f"Cannot create a safe name from {value!r}.")
    return key


def safe_filename(value: str, maximum_length: int = 100) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "_", str(value)).strip(" ._")
    name = re.sub(r"\s+", "_", name)
    return (name or "sample")[:maximum_length]


def validate_channel_configuration(channels, segmentation_channel):
    if not channels:
        raise ValueError("CHANNELS cannot be empty.")

    names = [str(item["name"]) for item in channels]
    indices = [int(item["index"]) for item in channels]
    keys = [safe_key(name).lower() for name in names]

    if len(names) != len(set(names)):
        raise ValueError("Every channel name must be unique.")
    if len(indices) != len(set(indices)):
        raise ValueError("Every configured channel index must be unique.")
    if len(keys) != len(set(keys)):
        raise ValueError(
            "Channel names must remain unique after conversion to CSV-safe names."
        )
    if any(index < 0 for index in indices):
        raise ValueError("Channel indices cannot be negative.")
    if segmentation_channel not in names:
        raise ValueError(
            f"SEGMENTATION_CHANNEL {segmentation_channel!r} is not in CHANNELS."
        )

    valid_roles = {"segmentation", "measurement", "reference"}
    invalid_roles = [
        item.get("role")
        for item in channels
        if item.get("role") not in valid_roles
    ]
    if invalid_roles:
        raise ValueError(
            f"Invalid channel roles: {invalid_roles}. Use {sorted(valid_roles)}."
        )


def unit_factor_to_um(unit):
    key = str(unit or "").strip().replace("μ", "µ").lower()
    if key not in UNIT_TO_UM:
        raise ValueError(f"Unsupported or missing physical unit: {unit!r}")
    return UNIT_TO_UM[key]


def resolution_as_float(value):
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return float(value[0]) / float(value[1])
    return float(value)


def ome_voxel_spacing_um(ome_xml):
    if not ome_xml:
        return None

    root = ET.fromstring(ome_xml)
    pixels = next(
        (
            element
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "Pixels"
        ),
        None,
    )
    if pixels is None:
        return None

    spacing = {}
    for axis in ("X", "Y", "Z"):
        value = pixels.attrib.get(f"PhysicalSize{axis}")
        if value is None:
            return None
        unit = pixels.attrib.get(f"PhysicalSize{axis}Unit", "µm")
        spacing[axis] = float(value) * unit_factor_to_um(unit)

    return spacing["Z"], spacing["Y"], spacing["X"]


def imagej_voxel_spacing_um(tif):
    metadata = tif.imagej_metadata or {}
    if "spacing" not in metadata or "unit" not in metadata:
        return None

    unit = metadata["unit"]
    z_um = float(metadata["spacing"]) * unit_factor_to_um(unit)
    tags = tif.pages[0].tags
    x_tag = tags.get("XResolution")
    y_tag = tags.get("YResolution")
    if x_tag is None or y_tag is None:
        return None

    x_pixels_per_unit = resolution_as_float(x_tag.value)
    y_pixels_per_unit = resolution_as_float(y_tag.value)
    resolution_unit_tag = tags.get("ResolutionUnit")
    resolution_unit = int(resolution_unit_tag.value) if resolution_unit_tag else 1

    if resolution_unit == 2:
        x_um = 25400.0 / x_pixels_per_unit
        y_um = 25400.0 / y_pixels_per_unit
    elif resolution_unit == 3:
        x_um = 10000.0 / x_pixels_per_unit
        y_um = 10000.0 / y_pixels_per_unit
    else:
        factor = unit_factor_to_um(unit)
        x_um = factor / x_pixels_per_unit
        y_um = factor / y_pixels_per_unit

    return z_um, y_um, x_um


def read_voxel_spacing_um(path, manual_override=None):
    with tifffile.TiffFile(path) as tif:
        spacing = ome_voxel_spacing_um(tif.ome_metadata)
        source = "OME-XML"
        if spacing is None:
            spacing = imagej_voxel_spacing_um(tif)
            source = "ImageJ/Fiji TIFF metadata"

    if spacing is None:
        if manual_override is None:
            raise ValueError(
                "Complete physical Z/Y/X spacing was not found. Set "
                "VOXEL_SPACING_OVERRIDE_UM = (Z, Y, X) in the configuration."
            )
        spacing = manual_override
        source = "manual configuration"

    spacing = tuple(float(value) for value in spacing)
    if len(spacing) != 3:
        raise ValueError("Voxel spacing must contain exactly (Z, Y, X).")
    if not all(np.isfinite(value) and value > 0 for value in spacing):
        raise ValueError(f"Invalid physical voxel spacing: {spacing}")
    return spacing, source


def standardize_to_czyx(data, axes, axes_override=None, time_index=0):
    axes = str(axes_override or axes).upper()
    if len(axes) != data.ndim:
        raise ValueError(
            f"Axes {axes!r} has {len(axes)} characters but data has "
            f"{data.ndim} dimensions."
        )
    if len(set(axes)) != len(axes):
        raise ValueError(f"Repeated axes are not supported: {axes!r}")

    if "T" in axes:
        t_axis = axes.index("T")
        if not 0 <= time_index < data.shape[t_axis]:
            raise ValueError(
                f"TIME_INDEX {time_index} is outside 0..{data.shape[t_axis] - 1}."
            )
        data = np.take(data, time_index, axis=t_axis)
        axes = axes.replace("T", "")

    for axis_name in list(axes):
        if axis_name not in "CZYX":
            axis_position = axes.index(axis_name)
            if data.shape[axis_position] != 1:
                raise ValueError(
                    f"Unsupported non-singleton axis {axis_name!r} in {axes!r}. "
                    "Set AXES_OVERRIDE explicitly if this axis represents Z or C."
                )
            data = np.take(data, 0, axis=axis_position)
            axes = axes.replace(axis_name, "")

    if "Y" not in axes or "X" not in axes:
        raise ValueError(f"TIFF axes must include Y and X; received {axes!r}.")

    if "Z" not in axes:
        data = np.expand_dims(data, axis=0)
        axes = "Z" + axes
    if "C" not in axes:
        data = np.expand_dims(data, axis=0)
        axes = "C" + axes

    order = [axes.index(axis) for axis in "CZYX"]
    return np.ascontiguousarray(np.transpose(data, order)), axes


def load_tiff_czyx(path, axes_override=None, time_index=0):
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        reported_shape = tuple(series.shape)
        reported_axes = str(series.axes)
        dtype = str(series.dtype)
        data = series.asarray()

    czyx, axes_used = standardize_to_czyx(
        data,
        reported_axes,
        axes_override=axes_override,
        time_index=time_index,
    )
    information = {
        "reported_shape": reported_shape,
        "reported_axes": reported_axes,
        "axes_interpreted_before_reorder": axes_used,
        "standardized_axes": "CZYX",
        "standardized_shape": tuple(czyx.shape),
        "dtype": dtype,
    }
    return czyx, information



def source_file_signature(path):
    path = Path(path)
    stat = path.stat()
    return {
        "filename": path.name,
        "size_bytes": int(stat.st_size),
        "modified_time_ns": int(stat.st_mtime_ns),
    }


def require_ome_zarr_packages():
    try:
        import zarr
        from numcodecs import Blosc
    except ImportError as error:
        raise ImportError(
            "OME-Zarr caching is enabled, but the optional packages are not "
            "installed. In the cellposesam environment run: pip install "
            "\"zarr<3\" numcodecs, restart the kernel, and rerun the notebook."
        ) from error

    major_version = int(str(zarr.__version__).split(".", 1)[0])
    if major_version >= 3:
        raise RuntimeError(
            "This notebook currently uses the stable Zarr v2 OME-NGFF writer. "
            "Install a compatible version with: pip install \"zarr<3\" numcodecs"
        )
    return zarr, Blosc


def normalized_chunks_czyx(requested_chunks, image_shape):
    if len(requested_chunks) != 4:
        raise ValueError("OME_ZARR_CHUNKS_CZYX must contain (C, Z, Y, X).")

    chunks = tuple(int(value) for value in requested_chunks)
    if any(value <= 0 for value in chunks):
        raise ValueError("Every OME-Zarr chunk dimension must be positive.")
    return tuple(min(chunk, int(size)) for chunk, size in zip(chunks, image_shape))


def ome_zarr_cache_path(tiff_path, cache_directory=None):
    tiff_path = Path(tiff_path)
    if cache_directory is None:
        return tiff_path.parent / f"{tiff_path.stem}.ome.zarr"

    # A local cache folder may receive identically named TIFFs from different
    # source folders. Add a short path digest to prevent those collisions.
    parent = Path(cache_directory)
    path_digest = hashlib.sha256(
        str(tiff_path.absolute()).encode("utf-8")
    ).hexdigest()[:12]
    return parent / f"{tiff_path.stem}__{path_digest}.ome.zarr"


def write_single_scale_ome_zarr(
    image_czyx,
    output_path,
    voxel_spacing_zyx_um,
    channel_configuration,
    source_tiff,
    source_information,
    requested_chunks=(1, 8, 256, 256),
):
    zarr, Blosc = require_ome_zarr_packages()
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(
            f"OME-Zarr target already exists and will not be overwritten: {output_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    chunks = normalized_chunks_czyx(requested_chunks, image_czyx.shape)
    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)

    root = zarr.open_group(str(output_path), mode="w")
    level_zero = root.create_dataset(
        "0",
        shape=image_czyx.shape,
        chunks=chunks,
        dtype=image_czyx.dtype,
        compressor=compressor,
        overwrite=False,
    )
    level_zero[:] = image_czyx

    z_um, y_um, x_um = (float(value) for value in voxel_spacing_zyx_um)
    root.attrs["multiscales"] = [
        {
            "version": "0.4",
            "name": Path(source_tiff).stem,
            "axes": [
                {"name": "c", "type": "channel"},
                {"name": "z", "type": "space", "unit": "micrometer"},
                {"name": "y", "type": "space", "unit": "micrometer"},
                {"name": "x", "type": "space", "unit": "micrometer"},
            ],
            "datasets": [
                {
                    "path": "0",
                    "coordinateTransformations": [
                        {"type": "scale", "scale": [1.0, z_um, y_um, x_um]}
                    ],
                }
            ],
            "type": "image",
        }
    ]
    root.attrs["nuclei_segmentation_cache"] = {
        "schema_version": 1,
        "standardized_axes": "CZYX",
        "source_signature": source_file_signature(source_tiff),
        "source_image_information": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in source_information.items()
        },
        "voxel_spacing_zyx_um": [z_um, y_um, x_um],
        "chunks_czyx": list(chunks),
        "configured_channels": [
            {
                "name": str(item["name"]),
                "index": int(item["index"]),
            }
            for item in channel_configuration
        ],
    }
    return chunks


def load_tiff_with_optional_ome_zarr_cache(
    tiff_path,
    voxel_spacing_zyx_um,
    channel_configuration,
    use_cache=False,
    cache_directory=None,
    requested_chunks=(1, 8, 256, 256),
    axes_override=None,
    time_index=0,
):
    tiff_path = Path(tiff_path)
    cache_path = ome_zarr_cache_path(tiff_path, cache_directory)
    cache_record = {
        "enabled": bool(use_cache),
        "path_at_analysis": str(cache_path) if use_cache else None,
        "reused_existing_cache": False,
        "storage_backend": "TIFF",
        "tiff_load_seconds": None,
        "ome_zarr_load_seconds": None,
        "ome_zarr_conversion_seconds": None,
        "chunks_czyx": None,
    }

    if use_cache and cache_path.exists():
        zarr, _ = require_ome_zarr_packages()
        load_start = perf_counter()
        root = zarr.open_group(str(cache_path), mode="r")
        cache_metadata = dict(root.attrs.get("nuclei_segmentation_cache", {}))
        expected_signature = cache_metadata.get("source_signature")
        observed_signature = source_file_signature(tiff_path)

        if expected_signature != observed_signature:
            raise ValueError(
                "The existing OME-Zarr cache does not match the TIFF size and "
                "modification time. Rename or remove the stale cache, or choose "
                "a different OME_ZARR_CACHE_DIRECTORY before continuing.\n"
                f"Cache: {cache_path}"
            )
        if cache_metadata.get("standardized_axes") != "CZYX" or "0" not in root:
            raise ValueError(
                f"The selected cache is not a compatible CZYX OME-Zarr store: {cache_path}"
            )

        image_czyx = np.asarray(root["0"])
        source_information = dict(cache_metadata.get("source_image_information", {}))
        for key in ("reported_shape", "standardized_shape"):
            if key in source_information:
                source_information[key] = tuple(source_information[key])

        elapsed = perf_counter() - load_start
        cache_record.update(
            {
                "reused_existing_cache": True,
                "storage_backend": "OME-Zarr cache",
                "ome_zarr_load_seconds": float(elapsed),
                "chunks_czyx": list(root["0"].chunks),
            }
        )
        return image_czyx, source_information, cache_record

    tiff_start = perf_counter()
    image_czyx, source_information = load_tiff_czyx(
        tiff_path,
        axes_override=axes_override,
        time_index=time_index,
    )
    cache_record["tiff_load_seconds"] = float(perf_counter() - tiff_start)

    if use_cache:
        conversion_start = perf_counter()
        chunks = write_single_scale_ome_zarr(
            image_czyx=image_czyx,
            output_path=cache_path,
            voxel_spacing_zyx_um=voxel_spacing_zyx_um,
            channel_configuration=channel_configuration,
            source_tiff=tiff_path,
            source_information=source_information,
            requested_chunks=requested_chunks,
        )
        cache_record.update(
            {
                "storage_backend": "TIFF (OME-Zarr cache created for later runs)",
                "ome_zarr_conversion_seconds": float(perf_counter() - conversion_start),
                "chunks_czyx": list(chunks),
            }
        )

    return image_czyx, source_information, cache_record


def file_sha256(path, chunk_size=8 * 1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def software_versions():
    def package_version(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "tifffile": tifffile.__version__,
        "scikit_image": ski.__version__,
        "torch": package_version("torch"),
        "cellpose": package_version("cellpose"),
        "napari": package_version("napari"),
        "magicgui": package_version("magicgui"),
        "zarr": package_version("zarr"),
        "numcodecs": package_version("numcodecs"),
    }


def calculate_surface_metrics(binary_mask, voxel_spacing):
    volume_um3 = float(binary_mask.sum() * np.prod(voxel_spacing))
    try:
        padded = np.pad(binary_mask.astype(np.uint8), 1)
        vertices, faces, _, _ = marching_cubes(
            padded,
            level=0.5,
            spacing=voxel_spacing,
        )
        surface_area_um2 = float(mesh_surface_area(vertices, faces))
        sphericity = float(
            np.pi ** (1 / 3)
            * (6 * volume_um3) ** (2 / 3)
            / surface_area_um2
        )
    except (ValueError, RuntimeError):
        surface_area_um2 = np.nan
        sphericity = np.nan
    return surface_area_um2, sphericity


def calculate_mask_properties(
    masks,
    channel_volumes,
    channel_configuration,
    voxel_spacing,
    background_percentile=50.0,
):
    voxel_volume_um3 = float(np.prod(voxel_spacing))
    rows = []
    measured_channels = [
        item for item in channel_configuration if item.get("measure", True)
    ]

    outside_masks = masks == 0
    background_by_channel = {}
    for item in measured_channels:
        name = item["name"]
        values = channel_volumes[name][outside_masks]
        if values.size == 0:
            values = channel_volumes[name].ravel()
        background_by_channel[name] = float(
            np.percentile(values, background_percentile)
        )

    for region in regionprops(masks):
        min_z, min_y, min_x, max_z_exclusive, max_y_exclusive, max_x_exclusive = (
            region.bbox
        )
        max_z = int(max_z_exclusive - 1)
        max_y = int(max_y_exclusive - 1)
        max_x = int(max_x_exclusive - 1)
        volume_um3 = float(region.area * voxel_volume_um3)
        surface_area_um2, sphericity = calculate_surface_metrics(
            region.image,
            voxel_spacing,
        )
        equivalent_diameter_um = float((6 * volume_um3 / np.pi) ** (1 / 3))
        centroid_z, centroid_y, centroid_x = region.centroid

        row = {
            "label": int(region.label),
            "size_voxels": int(region.area),
            "volume_um3": volume_um3,
            "surface_area_um2": surface_area_um2,
            "sphericity": sphericity,
            "equivalent_sphere_diameter_um": equivalent_diameter_um,
            "centroid_z_px": float(centroid_z),
            "centroid_y_px": float(centroid_y),
            "centroid_x_px": float(centroid_x),
            "centroid_z_um": float(centroid_z * voxel_spacing[0]),
            "centroid_y_um": float(centroid_y * voxel_spacing[1]),
            "centroid_x_um": float(centroid_x * voxel_spacing[2]),
            "min_z": int(min_z),
            "max_z": max_z,
            "min_y": int(min_y),
            "max_y": max_y,
            "min_x": int(min_x),
            "max_x": max_x,
            "z_extent_planes": int(max_z_exclusive - min_z),
            "z_extent_um": float((max_z_exclusive - min_z) * voxel_spacing[0]),
        }

        for item in measured_channels:
            name = item["name"]
            key = safe_key(name)
            local_volume = channel_volumes[name][region.slice]
            values = local_volume[region.image].astype(np.float64, copy=False)
            mean_value = float(values.mean())
            background = background_by_channel[name]

            row[f"mean_intensity_{key}"] = mean_value
            row[f"median_intensity_{key}"] = float(np.median(values))
            row[f"max_intensity_{key}"] = float(values.max())
            row[f"p95_intensity_{key}"] = float(np.percentile(values, 95))
            row[f"integrated_intensity_{key}"] = float(values.sum())
            row[f"background_corrected_mean_{key}"] = float(
                mean_value - background
            )

        rows.append(row)

    if not rows:
        raise RuntimeError("Cellpose did not produce any masks to measure.")

    table = pd.DataFrame(rows).set_index("label").sort_index()
    table.index = table.index.astype(int)
    table.index.name = "label"
    background_table = pd.DataFrame(
        [
            {
                "channel": name,
                "background_percentile": float(background_percentile),
                "background_intensity": value,
            }
            for name, value in background_by_channel.items()
        ]
    )
    return table, background_table




def resolve_marker_threshold(intensity_volume, configured_threshold=None):
    if configured_threshold is not None:
        threshold = float(configured_threshold)
        if not np.isfinite(threshold):
            raise ValueError("The configured marker threshold must be finite.")
        return threshold, "manual configuration"

    finite_values = np.asarray(intensity_volume)[
        np.isfinite(intensity_volume)
    ].ravel()
    if finite_values.size == 0:
        raise ValueError("The marker volume contains no finite intensity values.")

    # Cap the deterministic sample to keep threshold estimation inexpensive.
    stride = max(1, int(np.ceil(finite_values.size / 2_000_000)))
    sample = finite_values[::stride]
    if float(sample.min()) == float(sample.max()):
        return float(sample.min()), "constant image"

    return float(ski.filters.threshold_otsu(sample)), "Otsu starting estimate"


def calculate_perinuclear_marker_metrics(
    nucleus_masks,
    intensity_volume,
    voxel_spacing,
    channel_name,
    inner_distance_um=0.5,
    outer_distance_um=3.0,
    pixel_threshold=None,
):
    nucleus_masks = np.asarray(nucleus_masks)
    intensity_volume = np.asarray(intensity_volume)
    voxel_spacing = tuple(float(value) for value in voxel_spacing)
    inner_distance_um = float(inner_distance_um)
    outer_distance_um = float(outer_distance_um)

    if nucleus_masks.ndim != 3:
        raise ValueError("Perinuclear analysis requires a 3D ZYX label image.")
    if intensity_volume.shape != nucleus_masks.shape:
        raise ValueError(
            "The marker volume and DAPI masks must have identical ZYX shapes."
        )
    if len(voxel_spacing) != 3 or any(value <= 0 for value in voxel_spacing):
        raise ValueError("voxel_spacing must contain positive (Z, Y, X) values.")
    if inner_distance_um < 0 or outer_distance_um <= inner_distance_um:
        raise ValueError(
            "Perinuclear ring distances must satisfy 0 <= inner < outer."
        )

    resolved_threshold, threshold_source = resolve_marker_threshold(
        intensity_volume,
        configured_threshold=pixel_threshold,
    )
    prefix = safe_key(channel_name).lower()
    ring_labels = np.zeros_like(nucleus_masks)
    pad_zyx = tuple(
        int(np.ceil(outer_distance_um / spacing))
        for spacing in voxel_spacing
    )

    rows = []
    for region in regionprops(nucleus_masks):
        min_z, min_y, min_x, max_z, max_y, max_x = region.bbox
        starts = (
            max(0, min_z - pad_zyx[0]),
            max(0, min_y - pad_zyx[1]),
            max(0, min_x - pad_zyx[2]),
        )
        stops = (
            min(nucleus_masks.shape[0], max_z + pad_zyx[0]),
            min(nucleus_masks.shape[1], max_y + pad_zyx[1]),
            min(nucleus_masks.shape[2], max_x + pad_zyx[2]),
        )
        local_slice = tuple(
            slice(start, stop) for start, stop in zip(starts, stops)
        )
        local_labels = nucleus_masks[local_slice]
        local_nucleus = local_labels == int(region.label)

        distance_um = ndi.distance_transform_edt(
            ~local_nucleus,
            sampling=voxel_spacing,
        )
        local_ring = (
            (distance_um > inner_distance_um)
            & (distance_um <= outer_distance_um)
            & (local_labels == 0)
        )
        values = intensity_volume[local_slice][local_ring].astype(
            np.float64,
            copy=False,
        )

        local_ring_labels = ring_labels[local_slice]
        display_ring = local_ring & (local_ring_labels == 0)
        local_ring_labels[display_ring] = int(region.label)

        if values.size == 0:
            mean_value = np.nan
            median_value = np.nan
            p75_value = np.nan
            positive_fraction = 0.0
        else:
            mean_value = float(values.mean())
            median_value = float(np.median(values))
            p75_value = float(np.percentile(values, 75))
            positive_fraction = float(np.mean(values >= resolved_threshold))

        rows.append(
            {
                "label": int(region.label),
                f"{prefix}_ring_voxels": int(values.size),
                f"{prefix}_ring_mean": mean_value,
                f"{prefix}_ring_median": median_value,
                f"{prefix}_ring_p75": p75_value,
                f"{prefix}_ring_positive_fraction": positive_fraction,
            }
        )

    if not rows:
        raise RuntimeError("No DAPI masks were available for MAP2 association.")

    table = pd.DataFrame(rows).set_index("label").sort_index()
    table.index = table.index.astype(int)
    table.index.name = "label"
    return table, ring_labels, resolved_threshold, threshold_source


def calculate_z_guard_properties(
    properties,
    first_tissue_z,
    last_tissue_z,
    guard_um,
    guard_mode,
    z_spacing_um,
    max_z_index,
):
    first_tissue_z = int(first_tissue_z)
    last_tissue_z = int(last_tissue_z)
    guard_um = float(guard_um)
    guard_mode = str(guard_mode).lower()

    if not 0 <= first_tissue_z <= last_tissue_z <= max_z_index:
        raise ValueError(
            f"Tissue Z planes must satisfy 0 <= first <= last <= {max_z_index}."
        )
    if guard_um < 0:
        raise ValueError("Z guard thickness cannot be negative.")
    if guard_mode not in {"none", "first", "last", "both"}:
        raise ValueError("Z guard mode must be none, first, last, or both.")

    min_z = properties["min_z"].astype(int)
    max_z = properties["max_z"].astype(int)
    distance_first = ((min_z - first_tissue_z) * z_spacing_um).clip(lower=0)
    distance_last = ((last_tissue_z - max_z) * z_spacing_um).clip(lower=0)

    excluded_first = (min_z < first_tissue_z) | (distance_first <= guard_um)
    excluded_last = (max_z > last_tissue_z) | (distance_last <= guard_um)

    if guard_mode == "none":
        excluded = pd.Series(False, index=properties.index)
    elif guard_mode == "first":
        excluded = excluded_first
    elif guard_mode == "last":
        excluded = excluded_last
    else:
        excluded = excluded_first | excluded_last

    return pd.DataFrame(
        {
            "distance_from_first_z_surface_um": distance_first,
            "distance_from_last_z_surface_um": distance_last,
            "distance_from_nearest_z_surface_um": np.minimum(
                distance_first,
                distance_last,
            ),
            "excluded_near_first_z_surface": excluded_first.astype(bool),
            "excluded_near_last_z_surface": excluded_last.astype(bool),
            "excluded_by_z_guard": excluded.astype(bool),
        },
        index=properties.index,
    )


def calibrated_label_tiff(path, labels, voxel_spacing, require_uint16=False):
    maximum_label = int(labels.max())
    if maximum_label <= np.iinfo(np.uint16).max:
        output_dtype = np.uint16
    elif require_uint16:
        raise ValueError(
            "The mask contains more than 65,535 label IDs and cannot be "
            "saved as a uint16 SyGlass-compatible label TIFF."
        )
    else:
        output_dtype = np.uint32

    tifffile.imwrite(
        path,
        labels.astype(output_dtype, copy=False),
        imagej=True,
        metadata={
            "axes": "ZYX",
            "spacing": float(voxel_spacing[0]),
            "unit": "um",
        },
        resolution=(
            1.0 / float(voxel_spacing[2]),
            1.0 / float(voxel_spacing[1]),
        ),
        photometric="minisblack",
    )

# MNTB sampling volume: original image grid, never Cellpose-resampled data.
def reconstruct_mntb_roi(image_czyx, mode="per_slice"):
    if mode not in {"per_slice", "constant_xy"}:
        raise ValueError("ROI mode must be per_slice or constant_xy.")
    shape = tuple(image_czyx.shape[1:])
    roi = np.zeros(shape, dtype=bool)
    for z in range(shape[0]):
        support = np.zeros(shape[1:], dtype=bool)
        for c in range(image_czyx.shape[0]):
            plane = np.asarray(image_czyx[c, z])
            support |= np.isfinite(plane) & (plane != 0)
        roi[z] = ndi.binary_fill_holes(support)
    if mode == "constant_xy":
        # Use ONLY if the same Fiji contour was applied to every Z plane.
        footprint = ndi.binary_fill_holes(np.any(roi, axis=0))
        roi[:] = footprint
    return roi


def density_z_planes(settings, nz, dz):
    first, last = int(settings["first_tissue_z"]), int(settings["last_tissue_z"])
    guard, mode = float(settings["z_guard_um"]), settings["z_guard_mode"]
    if not 0 <= first <= last < nz or not np.isfinite(guard) or guard < 0:
        raise ValueError("Invalid tissue range or guard thickness.")
    if mode not in {"none", "first", "last", "both"}:
        raise ValueError("Invalid guard mode.")
    z = np.arange(nz)
    tissue = (z >= first) & (z <= last)
    effective = tissue.copy()
    # Match existing whole-object guards: distance <= guard is excluded.
    # Thus even guard=0 excludes an active surface plane.
    if mode in {"first", "both"}:
        effective &= (z - first) * dz > guard
    if mode in {"last", "both"}:
        effective &= (last - z) * dz > guard
    return tissue, effective


def roi_centroid_membership(properties, roi):
    coords = properties[["centroid_z_px", "centroid_y_px", "centroid_x_px"]].to_numpy()
    finite = np.isfinite(coords).all(axis=1)
    indices = np.floor(np.where(np.isfinite(coords), coords, -1) + 0.5).astype(int)
    valid = finite & (indices >= 0).all(axis=1) & (indices < np.array(roi.shape)).all(axis=1)
    inside = np.zeros(len(properties), dtype=bool)
    inside[valid] = roi[tuple(indices[valid].T)]
    return pd.Series(inside, index=properties.index), indices[:, 0]


def calculate_density_summary(
    plane_counts,
    settings,
    spacing,
    retained_count,
    reviewed,
):
    spacing = np.asarray(spacing, dtype=float)

    if (
        spacing.shape != (3,)
        or not np.isfinite(spacing).all()
        or (spacing <= 0).any()
    ):
        raise ValueError(
            "Voxel spacing must contain three positive finite values."
        )

    tissue, effective = density_z_planes(
        settings,
        len(plane_counts),
        spacing[0],
    )

    voxel_volume = float(np.prod(spacing))

    total = int(np.sum(plane_counts))
    tissue_n = int(np.sum(plane_counts[tissue]))
    effective_n = int(np.sum(plane_counts[effective]))

    effective_volume = effective_n * voxel_volume

    density_valid = bool(reviewed) and effective_volume > 0

    density_mm3 = (
        retained_count * 1e9 / effective_volume
        if density_valid
        else None
    )

    density_100um_cube = (
        retained_count * 1e6 / effective_volume
        if density_valid
        else None
    )

    # Optional correction to nominal section thickness.
    nominal = float(NOMINAL_SECTION_THICKNESS_UM)

    # Full tissue thickness BEFORE applying guards.
    # Inclusive plane count, consistent with voxel-based volume.
    first_z = int(settings["first_tissue_z"])
    last_z = int(settings["last_tissue_z"])

    number_of_tissue_planes = last_z - first_z + 1
    measured = float(number_of_tissue_planes * spacing[0])

    if not np.isfinite(nominal) or nominal <= 0:
        raise ValueError(
            "Nominal section thickness must be positive and finite."
        )

    volume_factor = None
    density_factor = None
    corrected_volume = None
    corrected_density = None

    if measured is not None:
        measured = float(measured)

        if not np.isfinite(measured) or measured <= 0:
            raise ValueError(
                "Measured full tissue thickness must be positive and finite."
            )

        volume_factor = nominal / measured
        density_factor = measured / nominal

        if reviewed:
            corrected_volume = effective_volume * volume_factor

        if density_valid:
            corrected_density = density_100um_cube * density_factor

    return {
        "roi_reviewed": bool(reviewed),

        "roi_stack_voxels": total,
        "roi_stack_volume_um3": total * voxel_volume,

        "roi_tissue_voxels": tissue_n,
        "roi_tissue_volume_um3": tissue_n * voxel_volume,

        "roi_effective_voxels": effective_n,
        "roi_effective_volume_um3": effective_volume,
        "roi_effective_volume_mm3": effective_volume / 1e9,

        "retained_nuclei_in_roi": int(retained_count),
        "retained_nuclei_per_mm3": density_mm3,
        "retained_nuclei_per_100um_cube": density_100um_cube,

        "nominal_section_thickness_um": nominal,
        "measured_full_tissue_thickness_um": measured,
        "thickness_volume_correction_factor": volume_factor,
        "thickness_density_correction_factor": density_factor,

        "nominal_corrected_effective_volume_um3": corrected_volume,
        "nominal_corrected_nuclei_per_100um_cube": corrected_density,

        "density_status": (
            "review ROI"
            if not reviewed
            else "empty sampling volume"
            if effective_n == 0
            else "valid"
        ),

        "counting_rule": (
            "filtered nuclei; nearest-voxel centroid in ROI "
            "and effective Z; existing whole-object guards"
        ),

        "interpretation": (
            "Descriptive retained-nucleus density; "
            "not an unbiased stereological estimator."
        ),

        "thickness_correction_assumption": (
            "Uniform Z scaling to nominal post-fixation section "
            "thickness; unchanged XY area; full tissue thickness "
            "measured before guards."
        ),
    }

def format_density_panel(result):
    raw_density = result["retained_nuclei_per_100um_cube"]
    corrected_density = result[
        "nominal_corrected_nuclei_per_100um_cube"
    ]
    corrected_volume = result[
        "nominal_corrected_effective_volume_um3"
    ]
    measured = result["measured_full_tissue_thickness_um"]
    nominal = result["nominal_section_thickness_um"]

    lines = [
        f"Counts (after filtering): {result['retained_nuclei_in_roi']:,}",
        (
            "Tissue volume (after Napari view refinement): "
            f"{result['roi_tissue_volume_um3']:,.1f} µm³"
        ),
        (
            "Tissue volume - Guard: "
            f"{result['roi_effective_volume_um3']:,.1f} µm³"
        ),
    ]

    if raw_density is None:
        lines.append(
            f"Density unavailable: {result['density_status']}"
        )
    else:
        lines.append(
            f"Tissue volume - Guard (density): {raw_density:,.1f} nuclei/(100 µm)³"
        )

    if measured is None:
        lines.append(
            "Thickness correction: full tissue thickness not provided"
        )
    else:
        lines.append(
            f"Scaled to original section thickness: {measured:g} µm → original {nominal:g} µm"
        )

        if corrected_volume is not None:
            lines.append(
                f"Scaled to tissue Volume - Guard: {corrected_volume:,.1f} µm³"
            )

        if corrected_density is not None:
            lines.append(
                f"Scaled to tissue Volume - Guard (density): {corrected_density:,.1f} "
                "nuclei/(100 µm)³"
            )

    return "\n".join(lines)
