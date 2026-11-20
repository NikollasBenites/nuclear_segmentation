# NuCytoSeg native projects

Use **File** or the **Project** workflow to create, open, save, save as, or close an analysis. Import a TIFF, OME-TIFF, or Leica LIF with **Open Image**, then use **Save Project** to name a new project folder. Open the folder with **Open Project**. **Save Project As** creates an independent folder and switches the active analysis to it. Close, replace, and quit prompt to save, discard, or cancel when a checkpoint has unsaved changes. Workspace layout continues to use the application’s existing workspace settings.

## Version 1 folder

| File | Contents |
| --- | --- |
| `project.json` | Native format name, schema version, and stable project ID |
| `checkpoint.ncs` | ZIP container with a revision and matching project ID, source image identity, JSON settings and analysis records, measurement tables with data types, and lossless NumPy array members |

Arrays are read without pickle and retain their original data types. Integer label IDs above 65535 are supported in native checkpoints. Scientific exports retain their existing format and limits.

Save and Open use a background Qt worker. A write lock and revision check protect an active folder from concurrent updates. Saves flush a temporary archive in that folder and replace the previous checkpoint only after the archive is complete. Save As requires a new directory.

## Original image verification

The app records the original file path and a SHA-256 digest computed by reading the file in chunks. The project references the original microscopy image. Derived caches such as Z-corrected preview pixels may be stored in the project.

Open checks the original path and requires a matching SHA-256 before reading image pixels. If the path is missing, **Locate Original Image** accepts an exact copy at a new path. The project is then marked as modified so saving records the new path. The app checks the recorded series, timepoint, axes, dimensions, data type, and channel identities before restoring the analysis. A changed file, incompatible grid, unreadable checkpoint, or unsupported version produces an error while the active analysis remains open.

## What resumes

- The dataset, channel configuration, calibration, confirmed crop and crop mask, offsets, preprocessing settings, normalization reference ROI, and compatible applied preprocessing and preview results.
- Raw segmentation labels and executed Cellpose configuration, including model and device. Compatible optional edited-label arrays and manual decision records are preserved when already present. The Qt6 Mask Editing page remains limited to the controls currently available.
- Filtering settings, measurement tables, retained IDs, filtered labels, and reviewed scientific ROI on the compatible grid. Unaccepted ROI voxels remain available for review. An ROI whose viewer transform departed from the scientific grid requires measurement recalculation and review.
- Cached quantification when the saved filtering, counting, reviewed ROI, and calibration agree. Stale results remain pending, and outdated masks and measurements are retained in the checkpoint where available. Open does not run Cellpose, morphology, intensity or marker measurement, Z correction, or density calculations.

Application-owned temporary XY projection, unfinished crop selection and preview layers, prepared export previews, export history, arbitrary napari layers, and transient viewer interaction state are outside the checkpoint. Finish applying the crop before saving its geometry.

## Native macOS verification

1. Import a calibrated OME-TIFF and, separately, a Leica LIF with multiple series or timepoints. Save at the image checkpoint and reopen after quitting. Check the original axes, channel order, calibration, and series and timepoint.
2. Apply a polygon crop and Z correction, configure normalization and reference ROI, and save, quit, and reopen. Inspect XY alignment and verify separate applied and pending settings.
3. Reopen a project with segmentation, filtering, and an accepted ROI. Confirm mask IDs, editable ROI alignment, unchanged quantification, and scientific export availability.
4. Move the original in Finder and reopen with **Locate Original Image**. Try a different file first, then the exact original. Save and open again from the updated path.
5. Exercise **Save Project As**, **Close Project**, and quitting with Save, Discard Changes, and Cancel. Verify the application stays responsive through a large-file save and retains the current analysis on cancellation.

## Legacy import follow-up

Legacy export packages require a separate adapter for legacy TIFF labels, source and ROI geometry, settings and measurement tables, and manual review records. Import will need explicit source compatibility checks and a warning when a legacy package has no SHA-256. A density or measurement cache with insufficient provenance must remain pending until verified or regenerated. Version 1 native checkpoints require SHA-256.
