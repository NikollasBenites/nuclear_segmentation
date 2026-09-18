# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
saved_properties_path = previous_result_folder / "retained_mask_properties.csv"
if not saved_properties_path.is_file():
    raise FileNotFoundError("density_only requires retained_mask_properties.csv from a previous export.")
saved_retained = pd.read_csv(saved_properties_path).set_index("label")
saved_settings = dict(previous_config["filters"])
for column in ("centroid_z_px", "centroid_y_px", "centroid_x_px"):
    if column not in saved_retained:
        raise ValueError(f"Previous table lacks {column}; density_only cannot determine ROI membership.")
saved_filtered_path = previous_result_folder / "filtered_masks_syglass.tif"
saved_display_masks = np.asarray(tifffile.imread(
    saved_filtered_path if saved_filtered_path.is_file() else previous_masks_path))
if saved_display_masks.shape != segmentation_volume.shape:
    raise ValueError("Previous masks do not match the selected image ZYX grid.")
if not saved_filtered_path.is_file():
    saved_display_masks = np.where(np.isin(saved_display_masks, saved_retained.index), saved_display_masks, 0)
