# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
if PERINUCLEAR_COMBINATION not in {"any", "all"}:
    raise ValueError('PERINUCLEAR_COMBINATION must be "any" or "all".')

enabled_perinuclear_markers = [
    dict(item)
    for item in PERINUCLEAR_MARKERS
    if item.get("enabled", True)
]

PERINUCLEAR_MARKER_RESULTS = {}
perinuclear_metric_tables = []

if not enabled_perinuclear_markers:

    print(
        "No perinuclear markers are enabled. "
        "Skipping perinuclear-marker analysis."
    )

    perinuclear_metrics = pd.DataFrame(
        index=mask_properties.index
    )

else:

    marker_names = [
        str(item["name"])
        for item in enabled_perinuclear_markers
    ]

marker_names = [str(item["name"]) for item in enabled_perinuclear_markers]
marker_keys = [safe_key(name).lower() for name in marker_names]
if len(marker_names) != len(set(marker_names)):
    raise ValueError("Enabled perinuclear marker names must be unique.")
if len(marker_keys) != len(set(marker_keys)):
    raise ValueError(
        "Enabled marker names must remain unique after CSV-safe conversion."
    )

PERINUCLEAR_MARKER_RESULTS = {}
perinuclear_metric_tables = []

for marker_configuration in enabled_perinuclear_markers:
    marker_name = str(marker_configuration["name"])
    if marker_name not in channel_volumes:
        raise KeyError(
            f"Perinuclear marker {marker_name!r} is not present in CHANNELS."
        )

    ring_inner_um = float(marker_configuration.get("ring_inner_um", 0.5))
    ring_outer_um = float(marker_configuration.get("ring_outer_um", 3.0))
    minimum_fraction = float(
        marker_configuration.get("initial_min_positive_fraction", 0.0)
    )
    if not 0 <= minimum_fraction <= 1:
        raise ValueError(
            f"{marker_name} initial_min_positive_fraction must be between 0 and 1."
        )

    metrics, ring_labels, resolved_threshold, threshold_source = (
        calculate_perinuclear_marker_metrics(
            nucleus_masks=masks,
            intensity_volume=channel_volumes[marker_name],
            voxel_spacing=VOXEL_SPACING_UM,
            channel_name=marker_name,
            inner_distance_um=ring_inner_um,
            outer_distance_um=ring_outer_um,
            pixel_threshold=marker_configuration.get("pixel_threshold"),
        )
    )
    prefix = safe_key(marker_name).lower()
    fraction_column = f"{prefix}_ring_positive_fraction"
    perinuclear_metric_tables.append(metrics)
    mask_properties = mask_properties.join(
        metrics,
        how="left",
        validate="one_to_one",
    )
    mask_properties[fraction_column] = mask_properties[fraction_column].fillna(0.0)

    initial_keep = mask_properties[fraction_column] >= minimum_fraction
    initial_labels = mask_properties.index[initial_keep].to_numpy()
    initial_associated_masks = np.where(
        np.isin(masks, initial_labels),
        masks,
        0,
    )

    shell_layer_name = f"{marker_name} perinuclear shells"
    associated_layer_name = f"initial {marker_name}-associated DAPI nuclei"
    for layer_name in [shell_layer_name, associated_layer_name]:
        if layer_name in viewer.layers:
            viewer.layers.remove(layer_name)

    viewer.add_labels(
        ring_labels,
        name=shell_layer_name,
        scale=VOXEL_SPACING_UM,
        opacity=0.45,
        visible=False,
    )
    viewer.add_labels(
        initial_associated_masks,
        name=associated_layer_name,
        scale=VOXEL_SPACING_UM,
        opacity=0.75,
        visible=(len(PERINUCLEAR_MARKER_RESULTS) == 0),
    )

    PERINUCLEAR_MARKER_RESULTS[marker_name] = {
        "name": marker_name,
        "prefix": prefix,
        "fraction_column": fraction_column,
        "ring_inner_um": ring_inner_um,
        "ring_outer_um": ring_outer_um,
        "configured_pixel_threshold": marker_configuration.get("pixel_threshold"),
        "resolved_pixel_threshold": float(resolved_threshold),
        "threshold_source": threshold_source,
        "initial_min_positive_fraction": minimum_fraction,
        "ring_labels": ring_labels,
    }

    print(
        f"{marker_name}: threshold {resolved_threshold:.4f} "
        f"({threshold_source}); shell {ring_inner_um:.2f}–{ring_outer_um:.2f} µm; "
        f"initially associated {int(initial_keep.sum())}/{len(initial_keep)} nuclei."
    )

if perinuclear_metric_tables:

    perinuclear_metrics = pd.concat(
        perinuclear_metric_tables,
        axis=1
    )

    display(
        perinuclear_metrics.describe().T
    )

else:

    perinuclear_metrics = pd.DataFrame(
        index=mask_properties.index
    )