# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
validate_channel_configuration(CHANNELS, SEGMENTATION_CHANNEL)

if RUN_MODE not in {"segment", "resume", "density_only"}:
    raise ValueError('RUN_MODE must be segment, resume, or density_only.')


previous_result_folder = None
previous_config = None

if RUN_MODE in {"resume", "density_only"}:
    selected_folder = str(session_previous_folder or "")
    if not selected_folder:
        raise RuntimeError("No previous-result folder was selected.")

    previous_result_folder = Path(selected_folder)
    previous_config_path = previous_result_folder / "analysis_config.json"
    previous_masks_path = previous_result_folder / "cellpose_raw_masks.tif"

    missing = [
        path
        for path in (previous_config_path, previous_masks_path)
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "The selected result folder is missing:\n"
            + "\n".join(str(path) for path in missing)
        )

    previous_config = json.loads(previous_config_path.read_text(encoding="utf-8"))
    NOMINAL_SECTION_THICKNESS_UM = float(previous_config.get("nominal_section_thickness_um", NOMINAL_SECTION_THICKNESS_UM))
    CHANNELS = previous_config["channels"]
    SEGMENTATION_CHANNEL = previous_config["segmentation_channel"]
    FILTER_COMBINATION = previous_config["filters"]["intensity_combination"]
    INTENSITY_MODE = previous_config["intensity_measurements"]["mode"]
    GLOBAL_BACKGROUND_PERCENTILE = previous_config[
        "intensity_measurements"
    ]["global_background_percentile"]
    INITIAL_MIN_VOLUME_UM3 = previous_config["filters"]["minimum_volume_um3"]
    INITIAL_MIN_SPHERICITY = previous_config["filters"]["minimum_sphericity"]
    INITIAL_FIRST_TISSUE_Z = previous_config["filters"]["first_tissue_z"]
    INITIAL_LAST_TISSUE_Z = previous_config["filters"]["last_tissue_z"]
    INITIAL_Z_GUARD_UM = previous_config["filters"]["z_guard_um"]
    INITIAL_Z_GUARD_MODE = previous_config["filters"]["z_guard_mode"]
    MODEL_NAME = previous_config["cellpose"]["model_name"]
    CELLPROB_THRESHOLD = previous_config["cellpose"]["cellprob_threshold"]
    CELLPOSE_MIN_SIZE_VOXELS = previous_config["cellpose"][
        "minimum_size_voxels"
    ]
    BATCH_SIZE = previous_config["cellpose"]["batch_size"]
    FLOW3D_SMOOTH = previous_config["cellpose"].get("flow3d_smooth")
    DIAMETER_PIXELS = previous_config["cellpose"].get("diameter_pixels")

    if "perinuclear_markers" in previous_config:
        PERINUCLEAR_MARKERS = []
        for previous_marker in previous_config["perinuclear_markers"]:
            PERINUCLEAR_MARKERS.append(
                {
                    "name": previous_marker["name"],
                    "enabled": previous_marker.get("enabled", True),
                    "ring_inner_um": previous_marker["ring_inner_um"],
                    "ring_outer_um": previous_marker["ring_outer_um"],
                    "pixel_threshold": previous_marker.get(
                        "resolved_pixel_threshold",
                        previous_marker.get("pixel_threshold"),
                    ),
                    "initial_min_positive_fraction": previous_marker.get(
                        "minimum_positive_fraction",
                        0.0,
                    ),
                }
            )
        PERINUCLEAR_COMBINATION = previous_config.get(
            "perinuclear_combination",
            previous_config.get("filters", {}).get(
                "perinuclear_combination",
                PERINUCLEAR_COMBINATION,
            ),
        )
    elif "map2_association" in previous_config:
        previous_map2 = previous_config["map2_association"]
        PERINUCLEAR_MARKERS = [
            {
                "name": previous_map2.get("channel", "MAP2"),
                "enabled": True,
                "ring_inner_um": previous_map2.get("ring_inner_um", 0.5),
                "ring_outer_um": previous_map2.get("ring_outer_um", 3.0),
                "pixel_threshold": previous_map2.get("pixel_threshold"),
                "initial_min_positive_fraction": previous_config.get(
                    "filters",
                    {},
                ).get("minimum_map2_ring_positive_fraction", 0.35),
            }
        ]
        PERINUCLEAR_COMBINATION = "any"

    validate_channel_configuration(CHANNELS, SEGMENTATION_CHANNEL)

    print(f"Previous result folder:\n{previous_result_folder}")
    print("Previous generalized settings restored.")

selected_file = str(session_input_path or "")
if not selected_file:
    raise RuntimeError("No input TIFF file was selected.")

# Do not resolve mapped Windows drives; resolving can produce a much longer UNC path.
DEFAULT_INPUT = Path(selected_file)
REPO_ROOT = DEFAULT_INPUT.parent

print(f"Selected input file:\n{DEFAULT_INPUT}")