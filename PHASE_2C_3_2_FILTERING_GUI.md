# Phase 2C.3.2 — Scientific Filtering GUI Integration

## Architecture and reused science

NuCytoSeg now routes the current immutable Cellpose label volume and current
in-memory working CZYX image through the existing scientific input adapter. It
does not reload a TIFF, rerun Cellpose, apply global normalization, or repeat
Z correction. Measurement channels therefore retain the established
`prepare_volumes()` measurement routing.

The desktop integration directly reuses:

- `calculate_mask_properties()` for calibrated morphology and intensity;
- `calculate_perinuclear_marker_metrics()` for physical-distance shells;
- `calculate_z_guard_properties()`, `sampling_membership()`, and
  `density_z_planes()` for the established boundary, directional-entry, and
  eligibility rules;
- the legacy intensity-column names, defaults, and Any/All semantics.

The filter-combination code formerly embedded in `stages/live.py` is now the
shared `filtering.py` implementation. The legacy live and export stages retain
their existing callable contract through a compatibility wrapper, while the
desktop controller calls the same implementation. This extraction prevents
two independent filtering implementations.

## Desktop behavior

The Mask Review page provides compact scientific-measurement and filtering
controls for volume, sphericity, stable-index channel intensities, perinuclear
positive fractions, tissue Z boundaries, physical guard thickness, and guard
mode. Measurement runs in a Qt worker. Changing thresholds reuses the measured
table and updates a separate `Live filtered masks` layer without renumbering
IDs or changing the raw masks.

The provisional 3D MNTB ROI is shown in a separate editable Labels layer. It
is not the polygon crop. Results remain explicitly preliminary until that ROI
is inspected and accepted; editing it invalidates acceptance. This milestone
does not calculate or present final density.

Application-owned filtering state preserves measured properties, background
values, settings, automatic status and failure reasons, guard/ROI exclusions,
retained IDs, ROI review state, and segmentation/measurement provenance.
Selected-mask details display the available measurements and automatic status.
Manual keep/exclude remains disabled for a later validated integration.

## Lifecycle and performance

- Source, crop, calibration, channel, preprocessing, or segmentation changes
  invalidate filtering through the existing segmentation lifecycle.
- Project closure removes filtering and ROI layers without recreating napari.
- A failed measurement does not replace a previous valid result.
- Expensive morphology and shell calculations run once in a worker; slider
  changes reuse their tables.

## Automated validation

Focused tests compare morphology, intensity Any/All, perinuclear thresholds,
zero-guard boundary behavior, directional ROI membership, stable IDs,
calibration/provenance, and raw-array preservation. Existing Z-boundary,
scientific-input, Cellpose-execution, Mask Review, and manual-review tests are
also included in the relevant regression run.

## Known limitations

- Manual decisions, density calculation, scientific exports, geometry editing,
  and nuclear/cytoplasmic matching are intentionally outside this milestone.
- Perinuclear definitions whose configured channel is absent are not run; the
  GUI shows only applicable measured markers.
- Native Qt/OpenGL behavior cannot be validated in the headless cloud runner.

## Native macOS verification checklist

1. Open a calibrated TIFF, crop if desired, and run Cellpose segmentation.
2. Open Mask Review and select **Calculate Measurements**.
3. Confirm raw masks remain unchanged and `Live filtered masks` appears.
4. Inspect volume, sphericity, channel intensity, and perinuclear controls.
5. Test intensity **all** and **any**, and verify retained IDs are unchanged.
6. Test `none`, `first`, `last`, and `both` guard modes, including zero guard.
7. Show the provisional MNTB ROI, edit one voxel, and confirm review is invalid.
8. Inspect and accept the ROI; confirm the summary changes from preliminary to
   validated without presenting a density estimate.
9. Select raw and filtered objects in 2D and 3D and inspect their properties.
10. Change a segmentation-relevant setting and confirm filtering becomes
    outdated; close/reopen a project and confirm no layers or state leak.

## Phase 2C.3.2.1 GUI and marker-configuration refinement

Filtering thresholds now use a synchronized horizontal slider and numerical
editor. The editor retains precise floating-point entry, while slider ranges
come from measured volume and channel-intensity statistics. Sphericity and
positive-fraction controls remain bounded to 0–1, Z guard remains calibrated
in micrometres, and first/last tissue planes remain discrete integer controls.
A 120 ms Qt debounce coalesces rapid slider motion; it does not rerun morphology
or perinuclear measurements.

The Mask Review workflow now includes a distinct **Perinuclear Marker
Configuration** section. Each entry stores a stable channel index and the
existing `enabled`, inner/outer distance, automatic/manual pixel threshold, and
initial positive-fraction fields. Current channel names are resolved only when
constructing `PERINUCLEAR_MARKERS`, so renaming `Channel 4` does not change its
identity. Duplicate source channels and invalid physical distances are rejected.

Channel, enabled-state, shell-distance, and pixel-threshold changes invalidate
the previous measurement result and require **Calculate Measurements**. A
minimum-positive-fraction change is filter-only: it updates the existing filter
control and reuses cached shell measurements. No new thresholding, shell, or
filtering algorithm was introduced.

Additional native verification:

1. Confirm each supported threshold has a visible slider and exact numerical
   field, and that changing either updates the other.
2. Confirm intensity sliders use measured values rather than a fixed 0–255
   range.
3. With generic channel names, add two marker configurations using different
   source channels and test automatic and manual pixel thresholds.
4. Rename a configured source channel and confirm the configuration remains on
   the same channel index with the new display name.
5. Change shell distances and confirm existing results become outdated without
   rerunning Cellpose; recalculate and confirm new shell measurements appear.
6. Change only minimum positive fraction and confirm live filtering updates
   without recalculating morphology or shells.

## Phase 2C.3.2.2 result preservation and conditional controls

The outdated-mask defect was caused by a direct connection from every Cellpose
parameter edit to `ProjectState.invalidate_segmentation()`. Parameter edits now
represent pending settings for the next explicit run. Existing masks,
measurements, filtering results, and reviewed ROI state remain current, while a
non-blocking message explains that another run is required to apply the pending
settings.

Every GUI execution result stores the validated scientific configuration used
by its worker. Filtering starts from that executed snapshot, then overlays the
current perinuclear measurement configuration. Consequently, an unexecuted
model, device, threshold, or inference-option edit cannot alter measurement
provenance. Existing ProjectState invalidation remains unchanged for image,
crop, calibration, normalization, applied Z-correction, channel identity, and
project lifecycle changes. Successful reruns replace results through the
existing lifecycle; failed reruns leave compatible prior results intact.

Optional filter controls are now conditional:

- **Enable Intensity Filtering** controls whether cached intensity measurements
  participate in exclusion. Turning it off hides channel thresholds and the
  combination selector without deleting their values or measurements.
- Intensity **all/any** is visible only when at least two individual channels
  are enabled.
- Perinuclear threshold controls appear only for enabled, successfully measured
  markers, and **all/any** appears only when at least two are active.
- Recalculation carries forward compatible cached filter settings, including a
  disabled intensity master state, while discarding columns that no longer
  exist after a measurement-configuration change.

Native verification additions:

1. Complete segmentation and measurement/filter review, then change several
   Cellpose parameters. Confirm raw and live masks remain current and the
   pending-settings message appears.
2. Run again successfully and confirm the result and executed provenance are
   replaced. Trigger a controlled failed rerun and confirm the prior result is
   retained.
3. Change crop or calibration and confirm the previous result is still marked
   scientifically incompatible/outdated.
4. Disable intensity filtering, confirm thresholds disappear and live masks
   update from cached measurements, then re-enable it and confirm values return.
5. Exercise zero, one, and two enabled intensity channels and markers; confirm
   each **all/any** selector appears only for two or more active filters.
