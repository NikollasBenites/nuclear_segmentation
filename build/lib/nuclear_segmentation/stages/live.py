# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
filter_properties = mask_properties.copy()
max_z_index = int(masks.shape[0] - 1)
z_spacing_um = float(VOXEL_SPACING_UM[0])
voxel_volume_um3 = float(np.prod(VOXEL_SPACING_UM))

default_last_z = (
    max_z_index
    if INITIAL_LAST_TISSUE_Z is None
    else int(INITIAL_LAST_TISSUE_Z)
)
default_min_volume = (
    float(500 * voxel_volume_um3)
    if INITIAL_MIN_VOLUME_UM3 is None
    else float(INITIAL_MIN_VOLUME_UM3)
)

first_z_widget = Slider(
    value=int(np.clip(INITIAL_FIRST_TISSUE_Z, 0, max_z_index)),
    min=0,
    max=max_z_index,
    step=1,
    label="Upper Z plane (section #)",
)
last_z_widget = Slider(
    value=int(np.clip(default_last_z, 0, max_z_index)),
    min=0,
    max=max_z_index,
    step=1,
    label="Down Z plane (section #)",
)
max_guard_um = max(1.0, float(np.ceil(max_z_index * z_spacing_um / 2)))
guard_widget = FloatSlider(
    value=float(np.clip(INITIAL_Z_GUARD_UM, 0, max_guard_um)),
    min=0.0,
    max=max_guard_um,
    step=0.1,
    label="Z guard (µm)",
)
guard_mode_widget = ComboBox(
    value=INITIAL_Z_GUARD_MODE,
    choices=["none", "first", "last", "both"],
    label="Stereology mode",
)

maximum_volume = float(np.ceil(filter_properties["volume_um3"].max()))
volume_widget = FloatSlider(
    value=float(np.clip(default_min_volume, 0, maximum_volume)),
    min=0.0,
    max=maximum_volume,
    step=max(0.1, maximum_volume / 1000),
    label="Minimum volume (µm³)",
)
sphericity_widget = FloatSlider(
    value=float(INITIAL_MIN_SPHERICITY),
    min=0.0,
    max=1.0,
    step=0.01,
    label="Minimum sphericity",
)

perinuclear_combination_widget = ComboBox(
    value=PERINUCLEAR_COMBINATION,
    choices=["any", "all"],
    label="Combine perinuclear markers",
)
perinuclear_widgets = {}
perinuclear_widget_list = []
for marker_name, marker_info in PERINUCLEAR_MARKER_RESULTS.items():
    widget = FloatSlider(
        value=float(marker_info["initial_min_positive_fraction"]),
        min=0.0,
        max=1.0,
        step=0.01,
        label=f"Minimum {marker_name}-positive fraction",
    )
    perinuclear_widgets[marker_name] = {
        "column": marker_info["fraction_column"],
        "minimum": widget,
    }
    perinuclear_widget_list.append(widget)

combination_widget = ComboBox(
    value=FILTER_COMBINATION,
    choices=["all", "any"],
    label="Combine intensity filters",
)
intensity_widgets = {}
intensity_widget_list = []
for item in CHANNELS:
    if not item.get("filter_enabled", False):
        continue
    name = item["name"]
    key = safe_key(name)
    column = f"{intensity_column_prefix}{key}"
    if column not in filter_properties:
        raise KeyError(
            f"Filtering was enabled for {name!r}, but {column!r} was not measured."
        )
    observed_min = float(np.floor(filter_properties[column].min()))
    observed_max = float(np.ceil(filter_properties[column].max()))
    if observed_max <= observed_min:
        observed_max = observed_min + 1.0
    step = max(0.01, (observed_max - observed_min) / 500)
    configured_min = item.get("filter_min")
    configured_max = item.get("filter_max")
    start_min = observed_min if configured_min is None else float(configured_min)
    start_max = observed_max if configured_max is None else float(configured_max)
    minimum_widget = FloatSlider(
        value=float(np.clip(start_min, observed_min, observed_max)),
        min=observed_min,
        max=observed_max,
        step=step,
        label=f"{name} Minimum",
    )
    maximum_widget = FloatSlider(
        value=float(np.clip(start_max, observed_min, observed_max)),
        min=observed_min,
        max=observed_max,
        step=step,
        label=f"{name} Maximum",
    )
    intensity_widgets[name] = {
        "column": column,
        "minimum": minimum_widget,
        "maximum": maximum_widget,
    }
    intensity_widget_list.extend([minimum_widget, maximum_widget])

count_label = Label(value="Masks kept: --")
guard_count_label = Label(value="Guard excluded: --")
status_label = Label(value="Ready")
refresh_button = PushButton(text="Refresh filters")

filter_panel = Container(
    widgets=[
        first_z_widget,
        last_z_widget,
        guard_widget,
        guard_mode_widget,
        volume_widget,
        sphericity_widget,
        perinuclear_combination_widget,
        *perinuclear_widget_list,
        combination_widget,
        *intensity_widget_list,
        count_label,
        guard_count_label,
        status_label,
        refresh_button,
    ],
    layout="vertical",
)

live_layer_name = "Live filtered masks"
guard_layer_name = "Excluded by guard"
current_keep = None
filtered_masks = None


def current_filter_settings():
    return {
        "minimum_volume_um3": float(volume_widget.value),
        "minimum_sphericity": float(sphericity_widget.value),
        "first_tissue_z": int(first_z_widget.value),
        "last_tissue_z": int(last_z_widget.value),
        "z_guard_um": float(guard_widget.value),
        "z_guard_mode": str(guard_mode_widget.value),
        "perinuclear_combination": str(perinuclear_combination_widget.value),
        "perinuclear_filters": {
            name: {
                "column": widgets["column"],
                "minimum_positive_fraction": float(widgets["minimum"].value),
            }
            for name, widgets in perinuclear_widgets.items()
        },
        "intensity_combination": str(combination_widget.value),
        "intensity_filters": {
            name: {
                "column": widgets["column"],
                "minimum": float(widgets["minimum"].value),
                "maximum": float(widgets["maximum"].value),
            }
            for name, widgets in intensity_widgets.items()
        },
    }


def calculate_perinuclear_filter_keep(settings, properties=None):
    properties = filter_properties if properties is None else properties
    conditions = []
    for marker_filter in settings["perinuclear_filters"].values():
        conditions.append(
            properties[marker_filter["column"]].fillna(0.0)
            >= marker_filter["minimum_positive_fraction"]
        )
    if not conditions:
        return pd.Series(True, index=properties.index)
    condition_table = pd.concat(conditions, axis=1)
    if settings["perinuclear_combination"] == "all":
        return condition_table.all(axis=1)
    if settings["perinuclear_combination"] == "any":
        return condition_table.any(axis=1)
    raise ValueError('Perinuclear combination must be "any" or "all".')


def apply_filter_settings(settings):
    z_properties = calculate_z_guard_properties(
        filter_properties,
        first_tissue_z=settings["first_tissue_z"],
        last_tissue_z=settings["last_tissue_z"],
        guard_um=settings["z_guard_um"],
        guard_mode=settings["z_guard_mode"],
        z_spacing_um=z_spacing_um,
        max_z_index=max_z_index,
    )
    for column in z_properties.columns:
        filter_properties[column] = z_properties[column]
        mask_properties[column] = z_properties[column]

    morphology_keep = (
        (filter_properties["volume_um3"] >= settings["minimum_volume_um3"])
        & (filter_properties["sphericity"] >= settings["minimum_sphericity"])
        & (~filter_properties["excluded_by_z_guard"])
    )
    perinuclear_keep = calculate_perinuclear_filter_keep(settings)

    intensity_conditions = []
    for name, intensity_filter in settings["intensity_filters"].items():
        if intensity_filter["minimum"] > intensity_filter["maximum"]:
            raise ValueError(f"{name} minimum intensity exceeds its maximum.")
        intensity_conditions.append(
            filter_properties[intensity_filter["column"]].between(
                intensity_filter["minimum"],
                intensity_filter["maximum"],
                inclusive="both",
            )
        )
    if not intensity_conditions:
        intensity_keep = pd.Series(True, index=filter_properties.index)
    elif settings["intensity_combination"] == "all":
        intensity_keep = pd.concat(intensity_conditions, axis=1).all(axis=1)
    else:
        intensity_keep = pd.concat(intensity_conditions, axis=1).any(axis=1)

    _, sampling_z = density_z_planes(settings, masks.shape[0], z_spacing_um)
    z_valid = (roi_centroid_z >= 0) & (roi_centroid_z < masks.shape[0])
    centroid_in_z = np.zeros(len(filter_properties), dtype=bool)
    centroid_in_z[z_valid] = sampling_z[roi_centroid_z[z_valid]]
    filter_properties["centroid_in_mntb_roi"] = roi_inside
    filter_properties["centroid_in_sampling_z"] = centroid_in_z
    filter_properties["excluded_by_sampling_roi"] = ~(roi_inside & centroid_in_z)
    return morphology_keep & perinuclear_keep & intensity_keep & roi_inside & centroid_in_z


def refresh_live_filter(*_):
    global current_keep, filtered_masks
    try:
        settings = current_filter_settings()
        current_keep = apply_filter_settings(settings)
        kept_labels = filter_properties.index[current_keep].to_numpy()
        filtered_masks = np.where(np.isin(masks, kept_labels), masks, 0)

        # Every shell voxel carries its parent nucleus label. Reuse the same
        # kept-label lookup so the live shell layers always match the live
        # filtered nuclei without recalculating shell geometry or intensities.
        kept_label_lookup = np.zeros(int(masks.max()) + 1, dtype=bool)
        kept_label_lookup[kept_labels.astype(int)] = True
        first_marker_name = next(iter(PERINUCLEAR_MARKER_RESULTS), None)
        for marker_name, marker_info in PERINUCLEAR_MARKER_RESULTS.items():
            ring_labels = marker_info["ring_labels"]
            live_ring_labels = np.where(
                kept_label_lookup[ring_labels],
                ring_labels,
                0,
            )
            live_ring_layer_name = f"live {marker_name} perinuclear shells"
            if live_ring_layer_name in viewer.layers:
                viewer.layers[live_ring_layer_name].data = live_ring_labels
                viewer.layers[live_ring_layer_name].scale = VOXEL_SPACING_UM
            else:
                viewer.add_labels(
                    live_ring_labels,
                    name=live_ring_layer_name,
                    scale=VOXEL_SPACING_UM,
                    opacity=0.55,
                    visible=(marker_name == first_marker_name),
                )

        guard_excluded = filter_properties["excluded_by_z_guard"].astype(bool)
        guard_labels = filter_properties.index[guard_excluded].to_numpy()
        guard_masks = np.where(np.isin(masks, guard_labels), masks, 0)

        if live_layer_name in viewer.layers:
            viewer.layers[live_layer_name].data = filtered_masks
            viewer.layers[live_layer_name].scale = VOXEL_SPACING_UM
        else:
            viewer.add_labels(
                filtered_masks,
                name=live_layer_name,
                scale=VOXEL_SPACING_UM,
                opacity=0.75,
            )
        if guard_layer_name in viewer.layers:
            viewer.layers[guard_layer_name].data = guard_masks
            viewer.layers[guard_layer_name].scale = VOXEL_SPACING_UM
        else:
            viewer.add_labels(
                guard_masks,
                name=guard_layer_name,
                scale=VOXEL_SPACING_UM,
                opacity=0.75,
                visible=False,
            )

        count_label.value = (
            f"Masks kept: {int(current_keep.sum())} / {len(current_keep)}"
        )
        guard_count_label.value = (
            f"Guard excluded: {int(guard_excluded.sum())}"
        )
        density_result = calculate_density_summary(roi_plane_counts, settings, VOXEL_SPACING_UM, int(current_keep.sum()), roi_reviewed)
        roi_volume_label.value = format_density_panel(density_result)
        density_label.value = ""
        density = density_result["retained_nuclei_per_mm3"]
        #density_label.value = (f"Nuclei per (100 µm)³: {density / 1000:,.1f}"
                                #if density is not None
                                #else f"Density unavailable: {density_result['density_status']}")
        status_label.value = "Valid filter settings"
        viewer.status = (
            f"Kept {int(current_keep.sum())}/{len(current_keep)} | "
            f"Perinuclear markers: {settings['perinuclear_combination']} | "
            f"Volume ≥ {settings['minimum_volume_um3']:.1f} µm³ | "
            f"Sphericity ≥ {settings['minimum_sphericity']:.2f}"
        )
    except ValueError as error:
        current_keep = None
        filtered_masks = None
        roi_volume_label.value = "Density unavailable: invalid settings"
        density_label.value = "Density unavailable: invalid settings"
        status_label.value = f"Invalid settings: {error}"
        viewer.status = str(error)


for widget in [
    first_z_widget,
    last_z_widget,
    guard_widget,
    guard_mode_widget,
    volume_widget,
    sphericity_widget,
    perinuclear_combination_widget,
    *perinuclear_widget_list,
    combination_widget,
    *intensity_widget_list,
]:
    widget.changed.connect(refresh_live_filter)
refresh_button.changed.connect(refresh_live_filter)

if "filter_dock_widget" in globals():
    try:
        viewer.window.remove_dock_widget(filter_dock_widget)
    except (KeyError, RuntimeError):
        pass

filter_dock_widget = viewer.window.add_dock_widget(
    filter_panel,
    area="right",
    name="DAPI nuclei and perinuclear markers",
)

# ROI counts are cached; slider changes only sum one small array per Z.
mntb_roi_layer = viewer.layers["MNTB ROI - review before density"]
roi_reviewed = False
roi_plane_counts = np.count_nonzero(mntb_roi_layer.data, axis=(1, 2))
roi_inside, roi_centroid_z = roi_centroid_membership(filter_properties, mntb_roi_layer.data > 0)
roi_volume_label = Label(value="ROI volume: --")
density_label = Label(value="Density: review ROI first")
roi_review_button = PushButton(text="Accept reviewed MNTB ROI")
filter_panel.append(roi_volume_label)
filter_panel.append(density_label)
filter_panel.append(roi_review_button)


def accept_mntb_roi(*_):
    global mntb_roi, roi_reviewed, roi_plane_counts, roi_inside, roi_centroid_z
    validate_roi_geometry(mntb_roi_layer, VOXEL_SPACING_UM, masks.shape)
    mntb_roi = np.asarray(mntb_roi_layer.data) > 0
    if mntb_roi.shape != masks.shape or not mntb_roi.any():
        roi_reviewed = False
        density_label.value = "Invalid or empty ROI"
        return
    roi_plane_counts = np.count_nonzero(mntb_roi, axis=(1, 2))
    roi_inside, roi_centroid_z = roi_centroid_membership(filter_properties, mntb_roi)
    roi_reviewed = True
    refresh_live_filter()


def invalidate_mntb_roi(*_):
    global roi_reviewed
    roi_reviewed = False
    density_label.value = "ROI changed: accept reviewed ROI to recalculate"
    roi_volume_label.value = "ROI volume: pending review"

roi_review_button.changed.connect(accept_mntb_roi)
# Disconnect callbacks when this cell is rerun in an existing viewer.
old_roi_callback = mntb_roi_layer.metadata.get("density_callback")
if old_roi_callback is not None:
    mntb_roi_layer.events.data.disconnect(old_roi_callback)
    if hasattr(mntb_roi_layer.events, "paint"):
        mntb_roi_layer.events.paint.disconnect(old_roi_callback)
mntb_roi_layer.events.data.connect(invalidate_mntb_roi)
if hasattr(mntb_roi_layer.events, "paint"):
    mntb_roi_layer.events.paint.connect(invalidate_mntb_roi)
mntb_roi_layer.metadata["density_callback"] = invalidate_mntb_roi

refresh_live_filter()
