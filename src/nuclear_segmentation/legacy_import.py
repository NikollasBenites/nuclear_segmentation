"""Read-only adapter from scientific exports to the native checkpoint contract."""
from dataclasses import asdict, replace
from functools import partial
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
import tifffile

from .config import validate_config
from .core import density_z_planes, read_voxel_spacing_um, safe_key
from .image_import import load_tiff_dataset, load_verified_image
from .lif_import import load_lif_dataset
from .manual_review import ManualReview
from .native_project import MissingProjectSource, _validate_controls, restored_state, snapshot_state
from .normalization import mask_digest
from .project_state import ProjectState
from .projection_crop import polygon_roi_mask, selection_bounding_box
from .scientific_input import scientific_input_provenance, validate_scientific_input
from .z_correction import DEFAULTS

TABLES = (
    "all_mask_properties", "retained_mask_properties", "excluded_mask_properties",
    "manual_mask_review", "filter_settings", "channel_background_estimates",
    "perinuclear_marker_metrics", "analysis_summary", "mntb_density_summary",
    "mntb_roi_by_z", "z_correction_profiles",
)
COUNTING_KEYS = ("z_counting_rule", "first_tissue_z", "last_tissue_z", "z_guard_um", "z_guard_mode")


def _source(folder, record, spacing, replacement, factory):
    location = record.get("path_at_analysis") or record.get("filename")
    path = Path(replacement or location or "")
    if not path.is_absolute() and replacement is None:
        path = folder.parent / path
    if not path.is_file():
        if replacement is not None:
            raise FileNotFoundError(f"Original microscopy image not found: {path}")
        raise MissingProjectSource(str(location or folder.parent))
    path = path.resolve()
    digest = record.get("sha256")
    if digest is not None and (not isinstance(digest, str) or not re.fullmatch("[0-9a-fA-F]{64}", digest)):
        raise ValueError("The saved source SHA-256 is malformed.")
    axes = record["reported_axes"]
    shape = record["reported_shape"]
    override = record.get("axes_override")
    # The legacy reader recorded axes after dropping T/auxiliary dimensions
    # and adding singleton C/Z. It cannot in general be used as an override.
    time_axes = override or axes
    if "T" in time_axes and shape[time_axes.index("T")] > 1 and "time_index" not in record:
        raise ValueError("The source has multiple timepoints and the analysis did not record which was used.")
    time = record.get("time_index", 0)
    is_lif = record.get("source_format") == "Leica LIF"
    series = record.get("series_index", 0)
    if is_lif:
        if "series_index" not in record or "series_identifier" not in record:
            raise ValueError("The Leica source series identity is unavailable.")
        read = partial(load_lif_dataset, path, series, time_index=time, bioimage_factory=factory,
                       spacing_override=spacing if digest else None)
    else:
        if series != 0:
            raise ValueError("The legacy TIFF reader supports source series 0 only.")
        read = partial(load_tiff_dataset, path, axes_override=override, time_index=time,
                       spacing_override=spacing if digest else None)
    dataset = load_verified_image(path, read, digest.lower() if digest else None)
    if (
        dataset.reported_axes != axes or dataset.reported_shape != tuple(shape)
        or dataset.standardized_shape != tuple(record["standardized_shape"])
        or dataset.dtype != record["dtype"] or record.get("standardized_axes") != "CZYX"
        or dataset.time_index != time or dataset.series_index != series
        or dataset.series_identifier != record.get("series_identifier", dataset.series_identifier)
        or record.get("source_format", dataset.source_format) != dataset.source_format
        or not np.allclose(dataset.voxel_spacing_zyx_um, spacing, rtol=1e-6, atol=0)
        or (not is_lif and dataset.acquisition_metadata["axes_interpreted_before_reorder"]
            != record.get("axes_interpreted_before_reorder"))
    ):
        raise ValueError("Source dimensions, axes, series, timepoint, or calibration differ from the analysis.")
    original_names = record.get("original_channel_names")
    if original_names is not None and original_names != [c.original_name for c in dataset.channels]:
        raise ValueError("The source channel identities differ from the recorded source channels.")
    return dataset, bool(digest)


def _read_labels(path, spacing):
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        if series.axes not in {"ZYX", "YX"}:
            raise ValueError(f"{path.name} must use a ZYX label grid.")
        pixels = series.asarray()
    if pixels.ndim == 2:
        pixels = pixels[None]
    if pixels.dtype.kind not in "iub" or pixels.min() < 0:
        raise ValueError(f"{path.name} must contain nonnegative integer labels.")
    if not np.allclose(read_voxel_spacing_um(path)[0], spacing, rtol=1e-5, atol=0):
        raise ValueError(f"{path.name} calibration differs from the analysis.")
    return pixels


def _working_grid(state, grid, masks):
    crop = grid.get("crop")
    if crop is not None:
        vertices = crop["selection_vertices_yx"]
        shape = state.original_dataset.standardized_shape[-2:]
        kind = crop["selection_type"]
        if kind == "polygon":
            bounds, inclusion = polygon_roi_mask(vertices, shape)
        elif kind == "rectangle":
            bounds, inclusion = selection_bounding_box(vertices, shape), None
        else:
            raise ValueError("Unsupported legacy crop geometry.")
        state.apply_crop(bounds, vertices, inclusion, kind)
        reproduced = json.loads(json.dumps(asdict(state.crop_provenance)))
        if reproduced.keys() - crop.keys() or any(
            value != crop[key] for key, value in reproduced.items()
        ):
            raise ValueError("The recorded crop cannot be reproduced on the source grid.")
    spacing = state.voxel_spacing_zyx_um
    oy, ox = state.working_offset_yx
    if masks.shape != state.working_dataset.standardized_shape[1:] or (grid and (
        grid["axes"] != "ZYX" or tuple(grid["shape"]) != masks.shape
        or tuple(grid["offset_yx_px"]) != (oy, ox)
        or not np.allclose(grid["offset_zyx_um"], (0, oy * spacing[1], ox * spacing[2]), rtol=1e-8, atol=0)
    )):
        raise ValueError("Raw masks do not align with the recorded working grid. A reproducible crop is required.")


def _config(state, original, verified):
    source = original["input"]
    primary = original["segmentation_channel"]
    records = {}
    for item in original["channels"]:
        index = item["index"]
        if type(index) is not int or index in records or not 0 <= index < len(state.channels):
            raise ValueError("Legacy channel indexes are duplicated or outside the source image.")
        channel = state.channels[index]
        if (not verified and "original_channel_names" not in source and channel.original_name
                and channel.original_name.casefold() != item["name"].casefold()):
            raise ValueError(f"Source channel {index + 1} does not match the recorded channel identity.")
        records[index] = dict(item, role="segmentation" if item["name"] == primary else
                              ("reference" if item.get("role") == "reference" else "measurement"))
    if primary not in {item["name"] for item in records.values()}:
        raise ValueError("The segmentation channel is missing from the legacy channel map.")
    channels = []
    for channel in state.channels:
        item = records.get(channel.index, dict(index=channel.index, name=channel.name, role="measurement",
                                               measure=False, filter_enabled=False))
        channels.append(item)
    state.channels = [replace(channel, name=item["name"], role=item["role"])
                      for channel, item in zip(state.channels, channels, strict=True)]
    state.segmentation_channel_index = next(c.index for c in state.channels if c.name == primary)
    indexes = {c.name: c.index for c in state.channels}
    marker_records = original.get("perinuclear_markers", [])
    if "map2_association" in original and "perinuclear_markers" not in original:
        raise ValueError("This historical MAP2 association layout is unsupported.")
    state.set_perinuclear_markers([
        dict(channel_index=indexes[item["name"]], enabled=item.get("enabled", True),
             ring_inner_um=item["ring_inner_um"], ring_outer_um=item["ring_outer_um"],
             pixel_threshold=item.get("configured_pixel_threshold", item.get("pixel_threshold")),
             initial_min_positive_fraction=item.get("minimum_positive_fraction", 0))
        for item in marker_records
    ])
    model = original["cellpose"]
    filters = original["filters"]
    intensity = original["intensity_measurements"]
    mode = intensity["mode"]
    if mode not in {"raw", "global_background_subtracted"}:
        raise ValueError("Unsupported legacy intensity measurement mode.")
    nominal = original["nominal_section_thickness_um"]
    if type(model.get("do_3D")) is not bool:
        raise ValueError("The executed 2D/3D segmentation mode was not recorded.")
    options = dict(
        RUN_MODE="segment", CHANNELS=channels, SEGMENTATION_CHANNEL=primary,
        VOXEL_SPACING_OVERRIDE_UM=list(state.voxel_spacing_zyx_um),
        NOMINAL_SECTION_THICKNESS_UM=nominal,
        AXES_OVERRIDE=state.original_dataset.axes_override, TIME_INDEX=state.original_dataset.time_index,
        MODEL_NAME=model["model_name"], CELLPOSE_DO_3D=model["do_3D"],
        CELLPROB_THRESHOLD=model["cellprob_threshold"], CELLPOSE_MIN_SIZE_VOXELS=model["minimum_size_voxels"],
        BATCH_SIZE=model["batch_size"], FLOW3D_SMOOTH=model.get("flow3d_smooth"),
        CELLPOSE_STITCH_THRESHOLD=model.get("stitch_threshold", 0.),
        DIAMETER_PIXELS=model.get("diameter_pixels"), CELLPOSE_RESCALE=model.get("rescale"),
        CELLPOSE_RESAMPLE=model.get("resample", True), CELLPOSE_NORMALIZE=model.get("normalize", True),
        CELLPOSE_ROI_NORMALIZATION=model.get("roi_normalization"),
        INTENSITY_MODE=mode, GLOBAL_BACKGROUND_PERCENTILE=intensity["global_background_percentile"],
        INITIAL_MIN_VOLUME_UM3=filters["minimum_volume_um3"], INITIAL_MIN_SPHERICITY=filters["minimum_sphericity"],
        INITIAL_FIRST_TISSUE_Z=filters["first_tissue_z"], INITIAL_LAST_TISSUE_Z=filters["last_tissue_z"],
        INITIAL_Z_GUARD_UM=filters["z_guard_um"], INITIAL_Z_GUARD_MODE=filters["z_guard_mode"],
        FILTER_COMBINATION=filters["intensity_combination"],
        PERINUCLEAR_COMBINATION=filters["perinuclear_combination"],
        PERINUCLEAR_MARKERS=state.legacy_perinuclear_markers(), Z_CORRECTIONS=original.get("z_corrections", {}),
        Z_CORRECTION_SOURCE=original.get("z_correction_source"),
        PROCESSING_DEVICE="cpu" if model.get("device") == "cpu" else "auto",
    )
    return validate_config(options)


def _restore_preprocessing(state, original, config, grid, notes):
    source = state.original_dataset
    for channel in state.channels:
        options = config["Z_CORRECTIONS"].get(channel.name)
        if options is None:
            continue
        report = original.get("z_correction_reports", {}).get(channel.name)
        if (not report or report["options"] != options
                or len(report["gain"]) != grid["working_shape_czyx"][1]
                or not np.isfinite(report["gain"]).all()
                or np.any(np.asarray(report["gain"]) <= 0)
                or any(len(report[key]) != len(report["gain"])
                       for key in ("observed_profile", "smoothed_profile", "positive_pixel_counts"))
                or not np.isfinite(report["smoothed_profile"]).all()
                or not any(value is not None for value in report["observed_profile"])
                or not all(value is None or np.isfinite(value) for value in report["observed_profile"])):
            raise ValueError(f"Applied Z correction has no compatible gain report for {channel.name}.")
        index = channel.index
        state.z_correction_options[index] = options
        state.z_correction_reports[index] = dict(
            report, legacy_recorded=True, source_path=str(source.source_path),
            series_index=source.series_index, series_identifier=source.series_identifier,
            channel_name_at_calculation=channel.name, working_shape_czyx=list(grid["working_shape_czyx"]),
            working_offset_yx=list(state.working_offset_yx), voxel_spacing_zyx_um=list(state.voxel_spacing_zyx_um),
        )
        state.z_correction_applied.add(index)
    if state.z_correction_applied:
        notes.append("Applied Z-correction profiles restored. Corrected image previews are unavailable.")
    normalize = config["CELLPOSE_NORMALIZE"]
    if original.get("applied_normalization", {}).get("cellpose_normalize", normalize) != normalize:
        raise ValueError("The saved normalization does not match the executed Cellpose configuration.")
    state.normalization_configuration = {
        "mode": "legacy_recorded", "cellpose_normalize": normalize,
    }
    state.normalization_is_current = True


def _read_tables(folder, notes):
    tables = {}
    for name in TABLES:
        path = folder / f"{name}.csv"
        if not path.is_file():
            continue
        try:
            tables[name] = pd.read_csv(path)
        except (ValueError, OSError) as error:
            notes.append(f"{path.name} could not be read ({error}).")
    return tables


def _index_table(table):
    column = next((name for name in ("label", "mask_id") if name in table), None)
    if column is None:
        raise ValueError("The table has no mask ID column.")
    indexed = table.set_index(column)
    if not indexed.index.is_unique:
        raise ValueError("The table contains duplicate mask IDs.")
    if not indexed.empty and (indexed.index.dtype.kind not in "iu" or indexed.index.min() <= 0):
        raise ValueError("The table contains invalid mask IDs.")
    return indexed.rename_axis("label")


def _single_row(tables, name):
    frame = tables.get(name)
    return frame.iloc[0].to_dict() if frame is not None and len(frame) == 1 else {}


def _filter_cache(state, tables, settings, config, label_sizes, manual, notes):
    table = tables.get("all_mask_properties")
    if table is None:
        return None, {}
    try:
        properties = _index_table(table)
        required = {
            "size_voxels", "volume_um3", "sphericity", "min_z", "max_z", "centroid_z_px", "centroid_y_px",
            "centroid_x_px", "retained", "automatic_keep", "automatic_failure_reasons",
            "excluded_by_sampling_roi", "excluded_by_z_guard", "manual_decision", "manual_reason",
            "excluded_manually",
        }
        if not required <= set(properties) or set(properties.index) != set(label_sizes):
            raise ValueError("The measurement columns or mask IDs differ from the raw segmentation.")
        if any(row != label_sizes[label] for label, row in properties["size_voxels"].items()):
            raise ValueError("The saved object sizes differ from the raw labels.")
        voxel_volume = float(np.prod(state.voxel_spacing_zyx_um))
        if not np.allclose(properties["volume_um3"], properties["size_voxels"] * voxel_volume):
            raise ValueError("The saved object volumes differ from the calibrated label grid.")
        for name in ("retained", "automatic_keep", "excluded_by_sampling_roi", "excluded_by_z_guard"):
            if properties[name].dtype.kind != "b":
                raise ValueError(f"{name} must contain Boolean values.")
        retained = tuple(int(label) for label in properties.index[properties["retained"]])
        for name, ids in (("retained_mask_properties", set(retained)),
                          ("excluded_mask_properties", set(label_sizes) - set(retained))):
            if name in tables and set(_index_table(tables[name]).index) != ids:
                raise ValueError(f"{name}.csv disagrees with the all-mask table.")
        if manual is not None:
            for label, decision in manual.decisions.items():
                if properties.loc[label, "manual_decision"] != decision["decision"]:
                    raise ValueError("Manual decisions disagree with the measurement table.")
        recorded_manual = properties.get("manual_decision", pd.Series("automatic", index=properties.index))
        for label, value in recorded_manual.items():
            if value != (manual.decisions.get(label, {}).get("decision", "automatic") if manual else "automatic"):
                raise ValueError("The measurement table contains unsigned manual decisions.")

        intensity, controls = [], {}
        prefix = "mean_intensity_" if config["INTENSITY_MODE"] == "raw" else "background_corrected_mean_"
        for item in config["CHANNELS"]:
            name, index = item["name"], item["index"]
            if not item.get("measure", True):
                continue
            column = prefix + safe_key(name)
            stats = properties[column]
            if not np.isfinite(stats).all():
                raise ValueError(f"Intensity measurements for {name} are incomplete.")
            active = settings["intensity_filters"].get(name)
            low = (active or {}).get("minimum", item.get("filter_min"))
            high = (active or {}).get("maximum", item.get("filter_max"))
            low = float(stats.min() if low is None else low)
            high = float(stats.max() if high is None else high)
            if not np.isfinite([low, high]).all() or low > high or (active is not None and active["column"] != column):
                raise ValueError(f"Intensity filtering for {name} is incompatible.")
            intensity.append(dict(index=index, name=name, column=column, minimum=min(float(stats.min()), low),
                                  maximum=max(float(stats.max()), high), enabled=active is not None))
            controls[index] = (active is not None, low, high)

        markers = []
        for item in state.legacy_import["configuration"].get("perinuclear_markers", []):
            if not item.get("enabled", True):
                continue
            name = item["name"]
            active = settings["perinuclear_filters"].get(name)
            column = item["fraction_column"]
            if (active is None or active["column"] != column or column not in properties
                    or not properties[column].between(0., 1.).all()):
                raise ValueError(f"Perinuclear measurements or filters for {name} are incomplete.")
            markers.append(dict(
                item, column=column, channel_index=next(c.index for c in state.channels if c.name == name),
                configured_pixel_threshold=item.get("configured_pixel_threshold", item.get("pixel_threshold")),
            ))
        if set(settings["intensity_filters"]) - {i["name"] for i in intensity} or (
            set(settings["perinuclear_filters"]) != {i["name"] for i in markers}
        ):
            raise ValueError("The filter configuration references missing measurements.")
    except (KeyError, ValueError, TypeError) as error:
        notes.append(f"Measurement cache is historical ({error}).")
        return None, {}

    masks = state.segmentation_result["masks"]
    filtered = np.empty_like(masks)
    for dest, raw in zip(filtered, masks, strict=True):
        dest[:] = np.where(np.isin(raw, retained), raw, 0)
    provenance = state.segmentation_result["input_provenance"]
    roi = state.scientific_roi
    return dict(properties=properties, background_table=tables.get("channel_background_estimates", pd.DataFrame()),
                settings=settings, retained_ids=retained, retained_count=len(retained), filtered_masks=filtered,
                raw_mask_shape=masks.shape, scientific_roi=roi, roi_reviewed=state.scientific_roi_reviewed,
                intensity_definitions=intensity, perinuclear_definitions=markers, ring_labels={},
                input_segmentation_provenance=provenance, measurement_provenance=provenance,
                roi_source=state.legacy_import["configuration"].get("mntb_roi", {}).get("source", "Imported ROI")), controls


def _restore_roi(state, folder, legacy, verified, signed_masks, plane_table, notes):
    filename = "mntb_roi.tif"
    if not (folder / filename).is_file():
        return
    roi_info = legacy.get("mntb_roi") or {}
    try:
        roi = _read_labels(folder / filename, state.voxel_spacing_zyx_um)
        if roi.shape != state.segmentation_result["mask_shape"] or roi.max() != 1:
            raise ValueError("ROI is empty, nonbinary, or on a different working grid.")
        roi = roi.astype(bool, copy=False)
        fingerprint = mask_digest(roi)
        provenance = state.segmentation_result["input_provenance"]
        expected = dict(provenance, mask_shape=roi.shape)
        recorded = roi_info.get("provenance")
        if recorded is not None:
            _verify_provenance(recorded, expected, legacy["input"])
        if roi_info.get("sha256") not in (None, fingerprint):
            raise ValueError("The ROI fingerprint differs from the exported ROI.")
        counts = np.count_nonzero(roi, axis=(1, 2))
        planes_match = plane_table is not None and np.array_equal(plane_table.get("z_index"), np.arange(len(counts))) and (
            np.array_equal(plane_table.get("roi_voxels"), counts))
        reviewed = verified and signed_masks and roi_info.get("reviewed") is True and (
            roi_info.get("mask_file") == filename
            and (roi_info.get("sha256") == fingerprint and recorded is not None
                 or not legacy.get("mask_grid") and planes_match)
        )
        state.set_scientific_roi(roi, expected, reviewed=reviewed)
    except (KeyError, ValueError, TypeError, OSError) as error:
        notes.append(f"INCOMPATIBLE — scientific ROI ({error}).")


def _verify_provenance(saved, expected, source):
    expected = json.loads(json.dumps(expected))
    expected["source_path"] = source.get("path_at_analysis")
    if any(saved.get(key) != value for key, value in expected.items()):
        raise ValueError("Recorded segmentation, measurement, or ROI provenance does not match the working grid.")


def _quantification(state, tables, notes):
    density = _single_row(tables, "mntb_density_summary")
    if not density:
        return
    density.pop("roi_source", None)
    result, roi = state.filtering_result, state.scientific_roi
    if not state.filtering_is_current or not state.scientific_roi_reviewed:
        return
    settings, retained = result["settings"], result["retained_ids"]
    try:
        first, last, guard, mode = (settings[key] for key in COUNTING_KEYS[1:])
        counts = np.count_nonzero(roi, axis=(1, 2))
        _, effective = density_z_planes(settings, len(counts), state.voxel_spacing_zyx_um[0])
        planes = tables["mntb_roi_by_z"]
        summary = _single_row(tables, "analysis_summary")
        total = state.segmentation_result["object_count"]
        config = state.legacy_import["configuration"]
        physical_fields = (
            "roi_stack_volume_um3", "roi_tissue_volume_um3", "roi_effective_volume_um3", "roi_effective_volume_mm3",
            "measured_full_tissue_thickness_um", "nominal_section_thickness_um", "thickness_density_correction_factor",
        )
        density_fields = ("retained_nuclei_per_mm3", "retained_nuclei_per_100um_cube",
                          "nominal_corrected_nuclei_per_100um_cube")
        if (
            not np.array_equal(planes["z_index"], np.arange(len(counts)))
            or not np.array_equal(planes["roi_voxels"], counts)
            or planes["included_in_density"].dtype.kind != "b"
            or not np.array_equal(planes["included_in_density"], effective)
            or density["density_status"] != "valid" or density["roi_reviewed"] is not True
            or density["roi_effective_voxels"] != counts[effective].sum()
            or density["retained_nuclei_in_roi"] != len(retained)
            or any(not np.isfinite(density[key]) or density[key] <= 0 for key in physical_fields)
            or any(not np.isfinite(density[key]) or density[key] < 0 for key in density_fields)
            or density["nominal_section_thickness_um"] != config["nominal_section_thickness_um"]
            or any(summary[k] != density[k] for k in (*physical_fields, *density_fields))
            or any(summary[k] != v for k, v in zip(COUNTING_KEYS[1:], (first, last, guard, mode), strict=True))
            or (summary["total_cellpose_masks"], summary["retained_masks"], summary["excluded_masks"])
            != (total, len(retained), total - len(retained))
        ):
            raise ValueError("Density, counting, ROI planes, or retained-count records disagree.")
        if config.get("counting_rule") is not None and density["counting_rule"] != config["counting_rule"]:
            raise ValueError("The density counting description differs from its configuration.")
        # Some standalone writers reported centroid counting even for first/last
        # after switching their filter to directional entry. Leave that density historical.
        if mode in {"first", "last"} and f"{'minimum' if mode == 'first' else 'maximum'} object Z" not in density["counting_rule"]:
            raise ValueError("The reported density description cannot verify directional Z-entry counting.")
        if "centroid" not in density["counting_rule"].lower() or "descriptive" not in density["interpretation"].lower():
            raise ValueError("The saved counting or density interpretation is incomplete.")
        reasons = result["properties"]["automatic_failure_reasons"].astype("string").fillna("").str.split("; ")
        exclusions = {
            "failed_morphology": int(reasons.map(lambda vals: "volume" in vals or "sphericity" in vals).sum()),
            "failed_intensity": int(reasons.map(lambda vals: "intensity" in vals).sum()),
            "failed_perinuclear": int(reasons.map(lambda vals: "shell association" in vals).sum()),
            "excluded_by_z_guard": int(result["properties"]["excluded_by_z_guard"].sum()),
            "excluded_by_sampling_roi": int(result["properties"]["excluded_by_sampling_roi"].sum()),
        }
        state.set_quantification_result(dict(
            density, density=density, total_cellpose_masks=total, retained_masks=len(retained),
            excluded_masks=total - len(retained), **exclusions, first_tissue_z=first, last_tissue_z=last,
            z_guard_um=guard, z_guard_mode=mode, eligible_sampling_planes=int(effective.sum()),
            roi_plane_counts=tuple(counts.tolist()), effective_sampling_planes=tuple(effective.tolist()),
            nominal_section_thickness_source=config.get("nominal_section_thickness_source", "unknown"),
            scientific_roi_sha256=mask_digest(roi), scientific_roi_provenance=state.scientific_roi_provenance,
            source_identity=result["measurement_provenance"], filter_settings=settings, retained_mask_ids=retained,
            segmentation_channel=state.segmentation_channel, voxel_spacing_zyx_um=state.voxel_spacing_zyx_um,
            executed_segmentation_config=state.segmentation_result["scientific_config"],
        ))
    except (KeyError, ValueError, TypeError) as error:
        notes.append(f"Density cache is historical ({error}).")


def import_legacy_analysis(folder, replacement=None, bioimage_factory=None):
    folder = Path(folder)
    for name in ("analysis_config.json", "cellpose_raw_masks.tif"):
        if not (folder / name).is_file():
            raise FileNotFoundError(f"Legacy package requires {name}.")
    legacy = json.loads((folder / "analysis_config.json").read_text(encoding="utf-8"))
    if legacy.get("schema_version") not in (7, 8):
        raise ValueError("Unsupported legacy schema. Select a schema 7 or 8 root scientific results folder.")
    spacing = tuple(legacy["calibration"]["voxel_spacing_zyx_um"])
    if len(spacing) != 3 or not np.isfinite(spacing).all() or min(spacing) <= 0:
        raise ValueError("The legacy voxel calibration is invalid.")
    dataset, verified = _source(folder, legacy["input"], spacing, replacement, bioimage_factory)
    state = ProjectState()
    state.set_dataset(dataset)
    state.set_calibration(spacing, legacy["calibration"].get("source", "legacy analysis"))
    masks = _read_labels(folder / "cellpose_raw_masks.tif", spacing)
    if masks.dtype.kind not in "iu":
        raise ValueError("The legacy raw mask file must contain integer label IDs.")
    labels, sizes = np.unique(masks, return_counts=True)
    label_sizes = {int(label): int(size) for label, size in zip(labels, sizes, strict=True) if label}
    if not label_sizes:
        raise ValueError("The legacy raw mask file contains no labels.")
    _working_grid(state, legacy.get("mask_grid") or {}, masks)
    notes = []
    config = _config(state, legacy, verified)
    provenance = scientific_input_provenance(state, config)
    if legacy.get("segmentation_input") is not None:
        _verify_provenance(legacy["segmentation_input"], provenance, legacy["input"])
    _restore_preprocessing(state, legacy, config, provenance, notes)
    validate_scientific_input(state, config)
    tables = _read_tables(folder, notes)
    settings = dict(legacy["filters"])
    settings.setdefault("intensity_filtering_enabled", bool(settings["intensity_filters"]))
    density_z_planes(settings, masks.shape[0], spacing[0])
    for name in ("minimum_volume_um3", "minimum_sphericity"):
        if not np.isfinite(settings[name]) or settings[name] < 0:
            raise ValueError("Legacy morphological thresholds are invalid.")
    if any(settings[name] not in {"all", "any"} for name in ("intensity_combination", "perinuclear_combination")):
        raise ValueError("Unsupported legacy filter combination.")
    state.legacy_import = dict(folder=str(folder), configuration=legacy, source_verified=verified,
                               settings=settings, tables=tables)
    signed = legacy.get("manual_review")
    manual = None
    if signed:
        try:
            manual = ManualReview(masks, label_sizes, signed)
        except (TypeError, ValueError) as error:
            if signed.get("mask_sha256"):
                from .manual_review import mask_signature
                if signed["mask_sha256"] != mask_signature(masks):
                    raise ValueError("Raw labels do not match the analysis mask signature.") from error
            notes.append(f"INCOMPATIBLE — manual decisions ({error}).")
    elif "manual_mask_review" in tables:
        notes.append("IMPORTED — REVIEW REQUIRED: the manual review table has no raw-mask signature.")
    if manual and manual.decisions and settings.get("z_counting_rule") != "directional_entry_v1":
        notes.append("IMPORTED — REVIEW REQUIRED: manual decisions are historical under the recorded counting rule.")
    model = legacy["cellpose"]
    state.set_segmentation_result(dict(
        legacy_imported=True, masks=masks, mask_shape=masks.shape, object_count=len(label_sizes),
        maximum_label_id=max(label_sizes), input_provenance=provenance, scientific_config=config,
        model_name=model["model_name"], device=model.get("device", "not recorded"),
        device_type=None if model.get("device") is None else model["device"].split(":")[0],
        parameters=model.get("executed_parameters", {}), segmentation_channel=state.segmentation_channel,
        model_load_seconds=model.get("model_load_seconds"), evaluation_seconds=model.get("evaluation_seconds"),
        z_corrections=config["Z_CORRECTIONS"], cellpose_normalize=config["CELLPOSE_NORMALIZE"],
        nominal_section_thickness_source=legacy.get("nominal_section_thickness_source"),
        manual_review=manual.to_record() if manual is not None else None,
    ))
    _restore_roi(state, folder, legacy, verified, manual is not None, tables.get("mntb_roi_by_z"), notes)
    if state.scientific_roi is not None:
        state.legacy_import["roi_sha256"] = mask_digest(state.scientific_roi)
    filtering, intensity = _filter_cache(state, tables, settings, config, label_sizes, manual, notes)
    measured_grid = legacy.get("measurement_input")
    measurement_matches = not legacy.get("mask_grid")
    if measured_grid is not None:
        try:
            _verify_provenance(measured_grid, provenance, legacy["input"])
            measurement_matches = True
        except ValueError as error:
            notes.append(f"INCOMPATIBLE — measurement grid ({error}).")
    if filtering is not None:
        state.set_filtering_result(filtering)
        state.filtering_is_current = bool(
            verified and manual is not None and measurement_matches and state.scientific_roi_reviewed
            and settings.get("z_counting_rule") == "directional_entry_v1"
        )
    _quantification(state, tables, notes)

    snapshot = snapshot_state(state)
    index = state.segmentation_channel_index
    pending = dict(config, FLOW3D_SMOOTH=config["FLOW3D_SMOOTH"] or [0., 0., 0.])
    if not isinstance(pending["FLOW3D_SMOOTH"], (list, tuple)):
        pending["FLOW3D_SMOOTH"] = [pending["FLOW3D_SMOOTH"]] * 3
    if len(pending["FLOW3D_SMOOTH"]) != 3:
        raise ValueError("Unsupported Cellpose flow-smoothing settings.")
    snapshot.update(
        projection_settings=(index, "maximum"), roi_grid_compatible=True,
        z_settings={c.index: state.z_correction_options.get(c.index, dict(DEFAULTS)) for c in state.channels},
        z_inspected=index, z_selected=list(state.z_correction_applied), z_include_zeros=False,
        z_previews={}, normalization_pixels=None,
        normalization_settings=dict(channel_index=index, mode="cellpose_default", lower=1., upper=99.,
                                    norm3D=True, input_source="working_original"),
        segmentation_settings=pending, filter_controls=settings, intensity_controls=intensity,
    )
    _validate_controls(snapshot, {c.index for c in state.channels})
    missing = [f"{name}.csv" for name in TABLES if name not in tables and name not in
               {"z_correction_profiles", "perinuclear_marker_metrics"}]
    status = "IMPORTED — REVIEW REQUIRED"
    state.legacy_import["summary"] = "\n".join((
        "Source image: VERIFIED (saved SHA-256 matches)." if verified else
        f"Source image: {status}. Cryptographic verification is unavailable. Only compatible metadata was checked.",
        "Raw masks: VERIFIED on the recorded working grid.",
        f"Measurements: {'VERIFIED' if state.filtering_is_current else status if tables.get('all_mask_properties') is not None else 'UNAVAILABLE'}.",
        f"Filtering: IMPORTED minimum volume {settings['minimum_volume_um3']:g} µm³, "
        f"sphericity {settings['minimum_sphericity']:g}, intensity mode {config['INTENSITY_MODE']}, "
        f"{len(settings['intensity_filters'])} active intensity filters, "
        f"{len(state.perinuclear_markers)} configured perinuclear markers.",
        f"Counting: tissue Z {settings['first_tissue_z']}–{settings['last_tissue_z']}, "
        f"guard {settings['z_guard_um']:g} µm ({settings['z_guard_mode']}); "
        f"nominal thickness {config['NOMINAL_SECTION_THICKNESS_UM']:g} µm.",
        f"ROI: {'VERIFIED — reviewed' if state.scientific_roi_reviewed else status if state.scientific_roi is not None else 'UNAVAILABLE'}.",
        f"Quantification: {'VERIFIED' if state.quantification_is_current else status + ' (historical values only)' if 'mntb_density_summary' in tables else 'UNAVAILABLE'}.",
        *notes,
        *([f"UNAVAILABLE optional tables: {', '.join(missing)}."] if missing else []),
    ))
    return dataset, restored_state(state), snapshot, state.legacy_import["summary"]
