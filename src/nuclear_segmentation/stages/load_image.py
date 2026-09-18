# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
manual_spacing = VOXEL_SPACING_OVERRIDE_UM
if RUN_MODE in {"resume", "density_only"} and previous_config is not None:
    manual_spacing = tuple(previous_config["calibration"]["voxel_spacing_zyx_um"])

VOXEL_SPACING_UM, CALIBRATION_SOURCE = read_voxel_spacing_um(
    DEFAULT_INPUT,
    manual_override=manual_spacing,
)
Z_SPACING_UM, Y_SPACING_UM, X_SPACING_UM = VOXEL_SPACING_UM
XY_SPACING_UM = float(np.mean((Y_SPACING_UM, X_SPACING_UM)))
ANISOTROPY = float(Z_SPACING_UM / XY_SPACING_UM)

image_czyx, image_information, OME_ZARR_CACHE_INFO = (
    load_tiff_with_optional_ome_zarr_cache(
        DEFAULT_INPUT,
        voxel_spacing_zyx_um=VOXEL_SPACING_UM,
        channel_configuration=CHANNELS,
        use_cache=USE_OME_ZARR_CACHE,
        cache_directory=OME_ZARR_CACHE_DIRECTORY,
        requested_chunks=OME_ZARR_CHUNKS_CZYX,
        axes_override=AXES_OVERRIDE,
        time_index=TIME_INDEX,
    )
)

channel_count = int(image_czyx.shape[0])
for item in CHANNELS:
    if int(item["index"]) >= channel_count:
        raise ValueError(
            f"Channel {item['name']!r} uses index {item['index']}, but the "
            f"standardized image has {channel_count} channels."
        )

channel_volumes = {
    item["name"]: np.ascontiguousarray(image_czyx[int(item["index"])])
    for item in CHANNELS
}
segmentation_volume = channel_volumes[SEGMENTATION_CHANNEL]
INPUT_SHA256 = file_sha256(DEFAULT_INPUT) if COMPUTE_INPUT_SHA256 else None

if RUN_MODE in {"resume", "density_only"} and previous_config is not None:
    expected_hash = previous_config["input"].get("sha256")
    if expected_hash and INPUT_SHA256 and expected_hash != INPUT_SHA256:
        raise ValueError(
            "The selected TIFF does not match the SHA-256 fingerprint stored "
            "with the previous analysis. Select the original TIFF."
        )

print("Original TIFF information:")
for key, value in image_information.items():
    print(f"  {key}: {value}")
print(f"Calibration source: {CALIBRATION_SOURCE}")
print(f"Voxel spacing (Z, Y, X): {VOXEL_SPACING_UM} µm")
print(f"Cellpose Z/XY anisotropy: {ANISOTROPY:.6f}")
print(f"Storage backend: {OME_ZARR_CACHE_INFO['storage_backend']}")

if OME_ZARR_CACHE_INFO["tiff_load_seconds"] is not None:
    print(f"TIFF loading: {OME_ZARR_CACHE_INFO['tiff_load_seconds']:.2f} s")
if OME_ZARR_CACHE_INFO["ome_zarr_conversion_seconds"] is not None:
    print(
        "OME-Zarr conversion: "
        f"{OME_ZARR_CACHE_INFO['ome_zarr_conversion_seconds']:.2f} s"
    )
if OME_ZARR_CACHE_INFO["ome_zarr_load_seconds"] is not None:
    print(
        "OME-Zarr loading: "
        f"{OME_ZARR_CACHE_INFO['ome_zarr_load_seconds']:.2f} s"
    )
if OME_ZARR_CACHE_INFO["enabled"]:
    print(f"OME-Zarr cache: {OME_ZARR_CACHE_INFO['path_at_analysis']}")
    print(f"OME-Zarr chunks CZYX: {OME_ZARR_CACHE_INFO['chunks_czyx']}")

print(f"Segmentation channel: {SEGMENTATION_CHANNEL}")
print(
    f"Segmentation intensity range: {segmentation_volume.min()} to "
    f"{segmentation_volume.max()} ({segmentation_volume.dtype})"
)
