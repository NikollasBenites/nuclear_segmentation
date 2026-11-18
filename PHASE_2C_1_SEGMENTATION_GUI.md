# Phase 2C.1 — Segmentation Configuration GUI

Phase 2C.1 adds an application-owned Segmentation page to NuCytoSeg. It only
identifies the scientific input, edits the existing Cellpose configuration, and
reports execution readiness. It does not import Cellpose or PyTorch and does not
run segmentation.

## Existing scientific components reused

- `config.defaults()` supplies every displayed default.
- `config.validate_config()` remains the authoritative full-configuration
  validator, including device, 2D stitching, resampling, rescale, normalization,
  Z-correction, and run-mode contracts.
- `ProjectState` supplies the stable segmentation-channel index, current working
  CZYX dataset, calibration, crop/ROI identity, Cellpose normalization state,
  and applied Z-correction state.
- `ProjectState.analysis_channel_records()` and `legacy_z_corrections()` adapt
  stable desktop channel indexes to the existing scientific configuration.
- `stages/segment.py` remains unchanged and continues to define effective
  Cellpose behavior, including zero stitch threshold for 3D, calibrated
  anisotropy, optional flow smoothing, diameter, resample, rescale, and
  normalization.

No scientific algorithm, default, exporter, image conversion, or legacy launcher
was changed.

## GUI and parameter mapping

The Segmentation workflow now contains collapsible Input, Model, Parameters,
Advanced, and Readiness sections. The input summary reports source filename,
stable channel index and current name, working ZYX shape, Z/Y/X spacing,
`Z / mean(Y, X)` anisotropy, crop/ROI state, selected-channel Z correction, and
the current Cellpose normalization mode.

The controls map directly to the existing keys:

| GUI control | Existing key |
| --- | --- |
| Model name or local path | `MODEL_NAME` |
| Automatic or CPU device | `PROCESSING_DEVICE` |
| 3D / 2D + stitching | `CELLPOSE_DO_3D` |
| Cell probability threshold | `CELLPROB_THRESHOLD` |
| Minimum size | `CELLPOSE_MIN_SIZE_VOXELS` |
| Stitch threshold | `CELLPOSE_STITCH_THRESHOLD` |
| Batch size | `BATCH_SIZE` |
| Z/Y/X flow smoothing | `FLOW3D_SMOOTH` |
| Resampling | `CELLPOSE_RESAMPLE` |
| Optional rescale | `CELLPOSE_RESCALE` |
| Optional diameter | `DIAMETER_PIXELS` |

3D flow controls are disabled in 2D mode; stitch threshold is disabled in 3D
mode. The existing validator rejects 2D mode without a positive stitch threshold.
Automatic device selection retains the validated CUDA → Apple MPS → CPU behavior
implemented by the existing segmentation stage; the GUI adds no backend choice.

The adapter sets `RUN_MODE="segment"` only in the generated in-memory candidate
configuration. It does not change the legacy default stored in `defaults.json`.
The Run Segmentation button is deliberately disabled and identifies Phase 2C.2
as the execution milestone.

## Normalization and Z correction

Cellpose normalization controls remain exclusively in Preprocessing. The
Segmentation page displays a read-only summary and maps the existing
`cellpose_normalize` value to `CELLPOSE_NORMALIZE`. Global multichannel export
normalization and display contrast/gamma are not used as segmentation input.

The selected channel's Z-correction state is read from the existing project
configuration. Applied corrections are mapped through `legacy_z_corrections()`;
the correction algorithm is not called and no corrected volume is copied merely
to populate the panel.

The current independent desktop ROI-fixed normalization provenance is not yet a
drop-in replacement for the legacy path-based `CELLPOSE_ROI_NORMALIZATION`
record. Phase 2C.1 therefore does not claim execution support for that boundary;
the current normalization value and readiness are displayed without inventing a
new analysis schema.

## Readiness behavior

Readiness requires a loaded working dataset, positive finite Z/Y/X calibration,
a valid stable segmentation-channel designation, current non-default
normalization settings, and a configuration accepted by `validate_config()`.
Missing requirements are listed explicitly. Readiness means configuration is
prepared for the next integration milestone; it never means Cellpose has run.

The page updates after import, project replacement/closure, channel role or name
changes, calibration changes, crop apply/reset, normalization changes, and
Z-correction changes. Opening or editing the panel does not mutate source or
working pixels.

## Scientific execution boundary for Phase 2C.2

The current path boundary must be resolved deliberately:

1. `AnalysisSession.__init__()` stores an input path.
2. `AnalysisSession.prepare()` runs `stages/load_image.py`.
3. `stages/load_image.py` calls the TIFF loader, builds `image_czyx`, channel
   volumes, `segmentation_volume`, calibration, anisotropy, and optional file
   hash from that path.
4. `z_correction.prepare_volumes()` and `stages/segment.py` then operate on the
   arrays already present in the session namespace. The Cellpose stage itself
   can consume an in-memory `model_input_volume` or `segmentation_volume`.

Consequently, passing the original TIFF path from the desktop would silently
discard an active working crop and polygon mask. Passing globally normalized
prepared-export arrays would also be incorrect, and applying desktop Z
correction before allowing the legacy preparation path to correct again would
double-process the data.

Phase 2C.2 needs one minimal, explicitly validated input adapter before invoking
the existing session stages. It must provide the current working CZYX arrays,
stable channel records, calibration, XY offset/crop provenance, and valid polygon
mask at the load boundary; select the segmentation channel by index; and route
normalization and Z correction exactly once. A calibrated temporary TIFF may be
considered for the validated TIFF workflow, but it must preserve crop/ROI
provenance and source identity and must not be confused with the original file.
Alternatively, a narrowly scoped in-memory load adapter could populate the same
namespace contract. Neither option is implemented in Phase 2C.1.

Direct LIF segmentation is not claimed. TIFF remains the validated primary
workflow.

## Automated validation

Focused tests cover defaults, existing validation, edited model parameters,
3D/2D mode behavior, stable channel indexes and renaming, dimensions,
anisotropy, polygon crop/reset, normalization and Z-correction summaries,
project closure/replacement, invalid model/stitch settings, disabled execution,
source-array immutability, and absence of a Cellpose import from panel actions.

## Native macOS verification checklist

1. Launch NuCytoSeg and open a calibrated multichannel TIFF.
2. Open Segmentation and verify source, channel index/name, ZYX dimensions,
   spacing, and anisotropy.
3. Rename the segmentation channel and then assign another channel; verify the
   stable index and displayed name update.
4. Apply and reset rectangle and polygon crops; verify working shape and ROI
   status update.
5. Apply/reset Cellpose normalization and selected-channel Z correction; verify
   only their read-only summaries change.
6. Switch between 3D and 2D + stitching and verify flow/stitch controls enable
   appropriately. Confirm 2D requires a positive stitch threshold.
7. Edit model, CPU/automatic device, thresholds, batch, resample, rescale, and
   diameter; verify readiness updates without model loading or image changes.
8. Confirm Run Segmentation remains disabled.
9. Close the project and open a different TIFF; verify no source, channel, crop,
   or preprocessing status leaks from the previous project.

Native macOS rendering is not claimed until this checklist is completed.
