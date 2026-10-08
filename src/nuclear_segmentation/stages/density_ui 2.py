# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
for item in CHANNELS:
    viewer.add_image(channel_volumes[item["name"]], name=item["name"],
                     scale=VOXEL_SPACING_UM, visible=item["name"] == SEGMENTATION_CHANNEL)
viewer.add_labels(saved_display_masks, name="previously retained nuclei", scale=VOXEL_SPACING_UM)
retrospective_roi_layer = viewer.add_labels(mntb_roi.astype(np.uint8), name="MNTB ROI - review",
                                            scale=VOXEL_SPACING_UM, opacity=0.25)
retrospective_info = Label(value=f"Previous retained nuclei: {len(saved_retained)}. Review ROI across Z.")
retrospective_status = Label(value="Density: pending ROI review")
retrospective_accept = PushButton(text="Accept reviewed ROI and calculate density")
retrospective_save = PushButton(text="Save density addendum")
retrospective_reviewed = False
retrospective_result = None

def invalidate_retrospective_roi(*_):
    global retrospective_reviewed
    retrospective_reviewed = False
    retrospective_status.value = "ROI changed: accept again before saving"

def calculate_retrospective_density(*_):
    global retrospective_reviewed, retrospective_result, retrospective_counts, retrospective_table
    validate_roi_geometry(retrospective_roi_layer, VOXEL_SPACING_UM, segmentation_volume.shape)
    roi = np.asarray(retrospective_roi_layer.data) > 0
    if roi.shape != segmentation_volume.shape or not roi.any():
        retrospective_reviewed = False
        retrospective_status.value = "Invalid or empty ROI"
        return
    inside, centroid_z = roi_centroid_membership(saved_retained, roi)
    _, sampling = density_z_planes(saved_settings, roi.shape[0], VOXEL_SPACING_UM[0])
    valid_z = (centroid_z >= 0) & (centroid_z < roi.shape[0])
    in_z = np.zeros(len(saved_retained), dtype=bool)
    in_z[valid_z] = sampling[centroid_z[valid_z]]
    counted = inside & in_z
    retrospective_counts = np.count_nonzero(roi, axis=(1, 2))
    retrospective_result = calculate_density_summary(retrospective_counts, saved_settings,
                                                     VOXEL_SPACING_UM, int(counted.sum()), True)
    retrospective_result.update({"previous_retained_nuclei": len(saved_retained),
                                 "previous_retained_outside_sampling_roi": int((~counted).sum()),
                                 "roi_source": roi_source,
                                 "source_analysis": str(previous_result_folder),
                                 "input_file": str(DEFAULT_INPUT)})
    retrospective_table = saved_retained.copy()
    retrospective_table["included_in_density"] = counted
    retrospective_reviewed = True
    density = retrospective_result["retained_nuclei_per_mm3"]
    value = (f"{density / 1000:,.1f} nuclei per (100 µm)³" if density is not None else "unavailable (empty volume)")
    retrospective_status.value = (f"Previously retained nuclei: {len(saved_retained):,}\n"+ format_density_panel(retrospective_result))

def save_retrospective_density(*_):
    if not retrospective_reviewed:
        retrospective_status.value = "Accept the reviewed ROI before saving"
        return
    calculate_retrospective_density()
    if not retrospective_reviewed:
        return
    destination = previous_result_folder / ("density_addendum_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    destination.mkdir(parents=True, exist_ok=False)
    validate_roi_geometry(retrospective_roi_layer, VOXEL_SPACING_UM, segmentation_volume.shape)
    roi = np.asarray(retrospective_roi_layer.data) > 0
    calibrated_label_tiff(destination / "mntb_roi.tif", roi.astype(np.uint8), VOXEL_SPACING_UM)
    pd.DataFrame([retrospective_result]).to_csv(destination / "mntb_density_summary.csv", index=False)
    retrospective_table.reset_index().to_csv(destination / "previous_nuclei_density_membership.csv", index=False)
    _, sampling = density_z_planes(saved_settings, roi.shape[0], VOXEL_SPACING_UM[0])
    pd.DataFrame({"z_index": np.arange(roi.shape[0]), "roi_voxels": retrospective_counts,
                  "area_um2": retrospective_counts * VOXEL_SPACING_UM[1] * VOXEL_SPACING_UM[2],
                  "included_in_density": sampling}).to_csv(destination / "mntb_roi_by_z.csv", index=False)
    record = {"density": retrospective_result, "original_filters": saved_settings,
              "voxel_spacing_zyx_um": list(VOXEL_SPACING_UM), "roi_mode": MNTB_ROI_MODE,
              "input_sha256": INPUT_SHA256, "source_configuration": previous_config,
              "date": datetime.now().isoformat()}
    (destination / "density_addendum.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    retrospective_status.value = f"Saved: {destination.name}"
    print(f"Density addendum saved to: {destination}")

retrospective_roi_layer.events.data.connect(invalidate_retrospective_roi)
if hasattr(retrospective_roi_layer.events, "paint"):
    retrospective_roi_layer.events.paint.connect(invalidate_retrospective_roi)
retrospective_accept.changed.connect(calculate_retrospective_density)
retrospective_save.changed.connect(save_retrospective_density)
retrospective_panel = Container(widgets=[retrospective_info, retrospective_status,
                                          retrospective_accept, retrospective_save])
viewer.window.add_dock_widget(retrospective_panel, area="right", name="Previous analysis: density only")
print("Review ROI, accept, then Save density addendum. Later analysis cells are skipped in density_only mode.")
