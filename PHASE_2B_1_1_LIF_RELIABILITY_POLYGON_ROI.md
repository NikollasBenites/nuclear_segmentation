# NuCytoSeg Phase 2B.1.1

## Scope

This milestone improves Leica LIF import reliability and makes polygon cropping a
true ROI operation. It does not change segmentation, measurement, filtering,
stereology, density, export, or legacy analysis schemas.

## Leica LIF reliability

- Series discovery and loading no longer request BioIO `standard_metadata`.
  Optional fields such as objective magnification and acquisition date are read
  only when they are present in the exact selected Leica scene XML subtree.
- Missing optional metadata is represented as unavailable and does not block
  loading. Complete positive Z/Y/X spacing remains required, with the existing
  manual calibration override available when Leica calibration is incomplete.
- Pixel loading stays on the existing worker thread. A non-modal, indeterminate
  dialog reports the filename, selected series, current stage, and elapsed time.
  No percentage or cancel operation is shown because BioIO does not expose
  trustworthy progress or interruption at this level.
- Import diagnostics record the ordered stages for file opening, series
  selection, dimension and calibration validation, pixel loading, layer
  creation, and completion. Worker failures log a full Python traceback through
  `nuclear_segmentation.lif_import` and show a concise file/series/stage error in
  the application.
- Image layers are created transactionally. A layer-creation failure removes
  only layers created by the failed import and retains the previously displayed
  project.

## Polygon ROI cropping

- Rectangle selections retain the Phase 2B.1 rectangular crop behavior.
- Polygon selections create a deterministic boolean mask from the polygon in
  pixel coordinates. The crop is the polygon bounding box, with pixels outside
  the polygon set to zero in every channel and Z plane.
- The operation preserves dtype, Z depth, channel count, voxel spacing, source
  identity, and the immutable original data array.
- The working project stores an independent boolean ROI mask. Crop provenance
  records the selection type, original and cropped vertices, physical-space
  vertices, bounding box and physical offset, ROI shape, pixel count, and area.
- Preview uses the same mask operation as confirmation, so the preview and
  accepted working volume are numerically consistent.
- Invalid, non-finite, degenerate, empty, and self-intersecting polygons are
  rejected before project state is changed.

## Validation environment

Automated tests are run with Python 3.12, PyQt6, and napari 0.10.0 using the
offscreen Qt platform. Native OpenGL rendering remains a macOS verification
step.

Run the focused regression tests from the repository root:

```bash
python -m pytest tests/test_lif_import.py tests/test_projection_crop.py -q
```

Run the complete suite:

```bash
python -m pytest -q
```

## Native macOS verification

1. Start NuCytoSeg and load a multi-series Leica LIF file.
2. Confirm the series browser shows unavailable optional values without BioIO
   binning, objective, or duration warnings.
3. Choose a large series and confirm the loading dialog stays visible and the
   window remains responsive until the image appears.
4. Confirm channel names, C/Z dimensions, dtype, and Z/Y/X calibration against
   the acquisition record.
5. In Preprocessing, generate an XY projection, draw a non-rectangular polygon,
   and preview it. Confirm pixels outside the polygon are black while the drawn
   boundary remains visible.
6. Apply the crop and navigate through Z. Confirm the polygon is applied to all
   channels and planes, the scale bar remains calibrated, and Reset restores the
   original volume.
7. Repeat an import and close/reopen the application to check worker and dialog
   lifecycle behavior.

Real Leica files and native Metal/OpenGL rendering are not available in the
cloud test environment, so these checks require the macOS workstation.
