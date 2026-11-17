# NuCytoSeg Phase 2B.3

## Scope

Phase 2B.3 adds an independent normalization ROI, multichannel Z-intensity
correction, scrollable scientific results, and calibrated profile plots to the
independent Qt6 desktop. It does not run Cellpose, change TIFF/LIF import,
perform scientific export, or alter segmentation, matching, stereology,
density, or legacy export schemas.

## Independent normalization ROI

Preprocessing → Normalization ROI provides Draw Polygon ROI, Edit ROI, Clear
ROI, Show/Hide ROI Mask, and Use Crop Polygon. Only one normalization polygon is
supported; multiple shapes are rejected rather than merged.

Drawing generates a maximum-intensity projection of the selected normalization
channel on a worker thread. The application-owned Shapes overlay is calibrated
and translated consistently with the working image. The validated
`polygon_roi_mask()` rasterizer creates the scientific binary mask
automatically. The full-resolution scientific mask is stored independently on
the immutable original XY grid; the Shapes layer is only its visual/editing
representation.

The normalization ROI does not crop or zero the image. It survives crop reset
and normalization reset. A crop polygon is used only through the explicit Use
Crop Polygon action. If a crop is active, normalization uses the intersection
of the independent ROI and valid working region. This excludes artificial
polygon-fill zeros while `compare_percentiles()` continues to include genuine
zero-valued voxels inside the effective ROI.

ROI provenance records source, series, channel index, original pixel and
physical vertices, calibration, working offset, scope, and mask digest. ROI
editing or clearing invalidates only dependent normalization state; it does not
alter Z-correction settings.

## Scientific normalization behavior

Phase 2B.2 behavior is preserved. `compare_percentiles()`,
`normalize_preview()`, and `mask_digest()` remain the scientific implementation.
Normalization Results is now a collapsible read-only text area with vertical
scrolling, selectable text, wrapping, and Copy Results. The nearby status is
intentionally short.

ROI-fixed limits calculated on uncorrected data cannot be applied together with
an applied Z correction. The interface explains which operation must be reset
and recalculated; it never silently accepts inconsistent processing order.

## Multichannel Z-intensity correction

The desktop adapter directly reuses `DEFAULTS`, `validate_options()`, and
`correct_z()` from `z_correction.py`. The algorithm remains:

1. selected positive finite pixel percentile per plane;
2. missing-plane interpolation;
3. Gaussian smoothing in Z planes;
4. median smoothed target;
5. multiplicative gain calculation and configured clipping;
6. independent float32 corrected output.

Zeros remain zero and source arrays are never modified. Every calculation
starts from the uncorrected current working channel—cropped when a crop exists,
otherwise uncropped. Normalization preview data are never correction inputs.

The UI supports one, multiple, or all channels. Settings and reports are keyed
by stable channel index; renaming updates display/legacy name resolution without
reassigning corrections. Each channel keeps independent percentile, sigma,
gain limits, segmentation-routing flag, and measurement-routing flag. Batch
processing is sequential and independent; no common interchannel gain is
calculated.

Only the inspected channel's corrected volume is retained as a napari preview.
Other batch outputs are released after compact profiles and reports are stored.
Changing inspected channel removes a mismatched image preview and asks for a
recalculation. Failed batches do not replace previously valid reports.

Applied configuration remains project-owned for future segmentation/export
integration. The designated segmentation channel is never changed by a batch.
Its corrected image is routed later only if that channel's `segmentation` option
is enabled. Measurement channels remain original unless their own
`measurement` option is explicitly enabled. `legacy_z_corrections()` resolves
the stable index records into the existing name-keyed `Z_CORRECTIONS` contract.

## Z profiles and results

Z-Correction Results is channel-specific, collapsible, scrollable, selectable,
and copyable. Channel rows display No correction, Preview ready, Applied, or
Outdated status. Reports include parameters, Z-plane count, observed and
smoothed reference summaries, actual gain range, duration, diagnostic sampling
scope, preview/application state, and routing.

The resizable Matplotlib viewer contains:

- original and corrected P99 versus calibrated Z position;
- original and corrected mean versus calibrated Z position;
- observed correction percentile and smoothed reference;
- multiplicative gain.

P99 diagnostics are explicitly distinguished from a non-99 correction
reference. The include-zero option affects only diagnostics. Artificial crop
fill is always excluded; genuine valid zeros are included when requested. Plane
indexes remain in the report while plots use calibrated micrometers.

## Lifecycle and memory

Crop changes invalidate Z reports/previews but preserve per-channel control
choices as outdated settings. Normalization ROI changes do not affect Z
correction. Applying Z correction invalidates a normalization preview that no
longer represents future processing order. Close Project removes ROI drawing
layers, projections, normalization and Z previews, reports, configurations,
profile dialogs, and worker references without recreating napari or resetting
workspace preferences.

Import, project replacement/closure, and application shutdown are blocked while
a non-interruptible preprocessing worker is active. Cancellation is not claimed.

## Phase 2B.4 preparation

No export UI is added here. Per-channel options, reports, stable indexes,
routing, source identity, crop mask digest, calibration, offset, shapes, and
dtypes are retained for later export selection. `image_export.py` is suitable
for Phase 2B.4 because it already writes calibrated OME/ImageJ TIFF planes
incrementally, protects source paths, preserves channel ordering/names, supports
float32, and records explicit per-channel uint16 scaling in a JSON sidecar.
Scientific processing arrays must remain distinct from that visualization-only
uint16 conversion.

## Automated validation

Focused tests cover independent ROI drawing/editing/clearing, explicit crop
reuse, crop intersection, anisotropic alignment, genuine/artificial zeros,
invalid polygons, overlay visibility, original immutability, legacy
normalization parity, Z-correction parity, defaults, parameters, empty planes,
gain/smoothing behavior, multichannel independence, stable indexes, routing,
failure preservation, profile sampling, results copying/collapse, reset,
worker blocking, and project closure.

```bash
python -m pytest \
  tests/test_preprocessing_phase_2b3.py \
  tests/test_desktop_normalization.py \
  tests/test_projection_crop.py \
  tests/test_z_correction.py -q
```

Run the broader headless suite with:

```bash
python -m pytest -q --ignore=tests/test_workflow.py
```

The validated napari 0.10 cloud environment reports 117 passed tests and 13
passed subtests for this command.

The known napari 0.10 headless abort in legacy `test_launcher_roundtrip` is
reported separately and scientific expectations are not weakened.

## macOS update and launch

```bash
git fetch origin feature/nucytoseg-qt6-shell
git switch feature/nucytoseg-qt6-shell
git pull --ff-only
source .venv-nucytoseg/bin/activate
python -m pip install -e '.[gui,test]'
QT_API=pyqt6 nucytoseg
```

## Native macOS verification checklist

1. Open a calibrated multichannel TIFF.
2. Draw a normalization ROI without cropping.
3. Confirm automatic binary-mask generation and ROI status.
4. Toggle ROI visibility through the custom Layers panel and ROI control.
5. Edit the polygon and confirm normalization results become stale.
6. Calculate ROI-derived normalization and inspect genuine-zero reporting.
7. Resize the sidebar and scroll Normalization Results.
8. Copy Normalization Results and paste them into a text editor.
9. Apply an existing polygon crop and verify ROI/crop independence.
10. Select one channel and calculate Z correction.
11. Select multiple channels and calculate independent corrections.
12. Select All and verify sequential per-channel progress.
13. Give channels different percentile/sigma settings and revisit each.
14. Switch the Z-results channel and verify its own report appears.
15. Inspect the corrected preview through every Z plane in 2D.
16. Inspect the corrected preview in 3D.
17. Open profiles and compare original/corrected P99 and mean.
18. Inspect observed/smoothed reference and gain plots.
19. Verify plotted Z positions against the acquisition calibration.
20. Apply ROI-fixed normalization, then confirm incompatible Z application is blocked.
21. Verify segmentation designation stays unchanged and measurement routing is explicit.
22. Reset one channel correction without affecting other valid channels.
23. Reset all corrections and confirm preview/report cleanup.
24. Close Project and confirm every preprocessing overlay disappears.
25. Open a different TIFF and confirm no ROI, report, or correction leaks.
26. Repeat open/close and clean application shutdown several times.

Native macOS/OpenGL and large real-volume behavior were not available in the
cloud environment and are not claimed as validated.
