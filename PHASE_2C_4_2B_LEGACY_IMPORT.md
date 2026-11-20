# NuCytoSeg legacy analysis import compatibility

## Supported package layouts

| Layout | Import contract |
| --- | --- |
| Legacy Nuclear Segmentation schema 7 or 8 | Root results folder with the original full TIFF grid. Schema 7 and early schema 8 caches without a recorded compatible counting rule remain historical. |
| Desktop scientific export schema 8 | Root results folder with explicit mask-grid provenance. A rectangle or polygon XY crop must reproduce the entire recorded grid, calibration, offsets, and crop provenance. |
| Schema 5, other schema versions, density-only addenda, and unversioned or notebook-only folders | Unsupported. Native NuCytoSeg folders open through Open Project. |

Required files are `analysis_config.json` and `cellpose_raw_masks.tif`. The original microscope image must be accessible or located by the user. Optional files include `all_mask_properties.csv`, retained and excluded tables, `manual_mask_review.csv`, `filter_settings.csv`, `mntb_roi.tif`, `analysis_summary.csv`, `mntb_density_summary.csv`, `mntb_roi_by_z.csv`, background estimates, perinuclear measurements, and Z-correction profiles. Scientific figures, PDFs, and filtered-mask TIFFs are unnecessary for restoration.

The compatible source reader, raw-mask signature, and original working grid determine whether the raw labels can be restored. SHA-256 is checked against the saved hash when available. Imports without a saved hash require compatible source metadata and explicit acknowledgement. An imported cache can be historical even when its raw labels are usable. No resampling, inference, or automatic measurement is part of import.

## Verification and continuation

- Use **Project → Import Legacy Analysis** and select the export root. **Locate Original Image** handles a moved source. For hashless packages, review the warning before proceeding. Older TIFFs with unnamed channels can be checked only by position. Hashless packages need independently readable calibration, and ambiguous TIFF timepoints or axis interpretations are rejected. Volumes previously cropped into independent TIFFs can open on that TIFF's grid when the exported analysis identifies it as the original source.
- The import checks integer labels and TIFF calibration against the reconstructed working grid. The desktop crop record must reproduce its pixel and physical offsets exactly. Applied Z-correction settings and gain profiles, Cellpose normalization, and executed inference parameters stay with the segmentation record. Legacy exports lack corrected preview pixels, so those previews remain unavailable until explicitly generated.
- Optional CSVs are archived as imported values. A current measurement cache requires the saved source hash, matching raw-mask signature, compatible settings and columns, matching object sizes and IDs, verified ROI review, and the recorded current counting rule. Saved manual decisions stay distinct from automatic results. Explicit filtering reuses compatible signed decisions on the original ROI with the original counting settings.
- An ROI may reopen for review when its geometry matches the working grid. A missing ROI leaves the analysis without a scientific ROI. Current density additionally depends on consistent retained and excluded counts, saved ROI plane records, effective sampling, and an explicit compatible counting description. Saved historical density is displayed separately with measured and nominal-thickness-corrected values. Some standalone directional exports report a centroid description in the density CSV, which keeps those density values historical.
- **Save Project As** creates a native project outside the legacy directory. The native archive keeps the original configuration, source verification status, imported tables, and usable analysis state. It can reopen after the legacy directory has been removed. The original image is still externally referenced. The new native fingerprint records the image the user acknowledged, while the legacy provenance continues to identify a hashless import as such.

## Native macOS verification

1. Import a complete schema 8 package from the Project workflow. Verify the compatibility summary, raw label IDs, filter settings, and reviewed ROI in the viewer.
2. Move the original image in Finder and locate it during import. Try a different image and confirm that the open project remains in place. Cancel a hashless import at its warning.
3. Import a cropped package with Z correction. Check physical translation, recorded correction settings, and measured and corrected density labels.
4. Convert with **Save Project As**, move the legacy package, quit, and reopen the native folder. Verify that the saved analysis and legacy provenance are still available.
