# Phase 2C.2.1 — Scientific Input Adapter

## Recovered state

The interrupted attempt had not produced source, test, documentation, staged, or committed changes. The branch remained at `3eab85f`; only the pre-existing `src/.DS_Store` modification and generated Python caches were present. No interrupted implementation was overwritten.

## Integration boundary

`scientific_input.py` is a narrow bridge between the desktop `ProjectState` and the established scientific namespace. It does not execute Cellpose and does not replace `AnalysisSession`. The legacy path-based application remains unchanged.

The adapter:

- validates the current working CZYX dataset, stable channel indexes, calibration, crop geometry, project identity, normalization state, and applied Z-correction provenance;
- supplies per-channel ZYX views of the working data without reloading the original TIFF or writing an intermediate TIFF;
- calculates the existing Z/mean-XY anisotropy convention;
- reconstructs the provisional scientific MNTB ROI with `core.reconstruct_mntb_roi()`;
- retains the two-dimensional polygon crop mask separately as `crop_inclusion_mask_yx` rather than treating it as the reviewed three-dimensional tissue ROI;
- invokes `z_correction.prepare_volumes()` once to create the existing `model_input_volume`, measurement routes, corrected volumes, and reports.

`SegmentationPanel.prepare_scientific_input()` is the future execution handoff. The Run Segmentation button remains disabled in this milestone.

## Existing scientific functions reused unchanged

- `config.validate_config()`
- `core.validate_channel_configuration()`
- `core.reconstruct_mntb_roi()`
- `z_correction.validate_options()`
- `z_correction.prepare_volumes()`
- `z_correction.correct_z()` indirectly through `prepare_volumes()`

No normalization, Z-correction, ROI, Cellpose, measurement, stereology, density, or export mathematics were changed.

## Input and metadata contract

The namespace contains the current working `image_czyx`, channel-name-to-ZYX views, `segmentation_volume`, calibrated Z/Y/X spacing, XY spacing, anisotropy, provisional scientific ROI, crop inclusion mask, and the validated legacy configuration. Provenance records the source path and series, working dimensions and offset, crop type, segmentation channel, Z-correction options, and Cellpose normalization configuration.

Rectangular and polygon crops use `working_dataset`; the adapter never silently falls back to `original_dataset`. Polygon exterior zeros remain in the working data, while the independent boolean crop mask preserves their origin. Genuine internal zeros are not altered.

## Z correction and normalization

The adapter passes uncorrected working channel views into `prepare_volumes()`. Applied `Z_CORRECTIONS` determine segmentation and measurement routing, preventing an already corrected preview or export-preparation array from being corrected again.

For Cellpose Default, `CELLPOSE_NORMALIZE=True` is preserved. No preview array is substituted for model input and no normalization is performed before the future Cellpose call. Custom Cellpose normalization is accepted only when its project configuration is current. Global TIFF normalization and display contrast remain unrelated.

## Validation and equivalence coverage

Focused tests cover uncropped, rectangular, and polygon working data; stable indexes after channel renaming; ZYX dimensions; voxel spacing and anisotropy; default Cellpose normalization; segmentation and measurement Z-correction routes; preservation of source arrays; stale channel/crop/Z-correction/normalization rejection; and project closure.

The adapter uses the same configuration validation, ROI reconstruction, and volume-routing functions as the path workflow. Direct array comparisons verify the routed data. The only intentional storage difference is that unchanged channel inputs remain views rather than the eager contiguous copies made after disk loading.

## Remaining Phase 2C.2.2 work

- Connect the validated namespace to an application-owned background execution worker.
- Invoke the existing Cellpose stage without changing its `model.eval()` contract.
- Define the GUI review handoff for the separately reconstructed MNTB ROI.
- Validate native memory use and cancellation with representative large volumes.

Unsupported in this milestone: explicit/restored scientific ROI masks, resume/density-only execution, Cellpose loading or inference, mask generation, measurements, editing, stereology, and scientific export.

## Native verification checklist

1. Open a calibrated multichannel TIFF and designate the segmentation channel.
2. Confirm the Segmentation panel reports the current working ZYX dimensions and anisotropy.
3. Rename the channel and confirm its stable index is retained.
4. Apply rectangle and polygon crops and confirm the panel remains ready.
5. Apply a Z correction and confirm the segmentation status reports it.
6. Select Cellpose Default and confirm no extra normalization step or preview layer is required.
7. Close the project and confirm the segmentation input returns to “Not ready”.
8. Confirm Run Segmentation remains disabled and no Cellpose model is loaded.
