# Derived from the synchronized M3 v5.5 workflow. Executed in a session namespace.
# Reconstruct before segmentation, using all ORIGINAL image channels.
# No intensity normalization, dilation, or convex hull is applied.
roi_source = "inferred_" + MNTB_ROI_MODE
saved_roi = (previous_result_folder / "mntb_roi.tif"
             if previous_result_folder is not None else None)
if MNTB_ROI_MASK_PATH:
    roi_path = Path(MNTB_ROI_MASK_PATH)
    mntb_roi = np.asarray(tifffile.imread(roi_path)) > 0
    roi_source = "explicit_mask"
elif saved_roi is not None and saved_roi.is_file():
    mntb_roi = np.asarray(tifffile.imread(saved_roi)) > 0
    roi_source = "restored_mask"
else:
    mntb_roi = reconstruct_mntb_roi(image_czyx, MNTB_ROI_MODE)
if mntb_roi.shape != segmentation_volume.shape:
    raise ValueError("MNTB ROI must match the original image ZYX grid exactly.")
if not mntb_roi.any():
    raise ValueError("Empty MNTB ROI. Check the input or supply an explicit ROI mask.")
print(f"MNTB ROI source: {roi_source}")
print(f"Provisional ROI voxels: {int(mntb_roi.sum()):,}")
print(f"Provisional ROI volume: {mntb_roi.sum() * np.prod(VOXEL_SPACING_UM):,.2f} µm³")
print("Review MNTB ROI in Napari across Z; dark regions connected to the exterior cannot be recovered reliably.")
