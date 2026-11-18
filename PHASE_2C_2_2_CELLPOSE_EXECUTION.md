# Phase 2C.2.2 — Cellpose Execution Integration

## Architecture and code reuse

NuCytoSeg now connects the existing Segmentation panel to the Phase 2C.2.1 in-memory adapter and then executes the authoritative bundled `stages/segment.py`. No source TIFF is reloaded and no intermediate TIFF is created.

The implementation reuses unchanged:

- `scientific_input.prepare_scientific_input()` for working CZYX data, calibration, ROI reconstruction, Z-correction routing, and normalization configuration;
- `z_correction.prepare_volumes()` and `correct_z()` through that adapter;
- `stages/segment.py` for CPU/CUDA/MPS device selection, Cellpose logger/model initialization, the complete `model.eval()` argument contract, synchronization, timing, probability recovery, mask validation, and detected-label calculation;
- `config.validate_config()` and `scientific_input.validate_scientific_input()`;
- the established QObject/QThread worker lifecycle used by preprocessing and export controllers;
- application-owned napari layer management and project lifecycle checks.

`runtime.execute_stage()` is the only runtime compatibility hook. `AnalysisSession.stage()` delegates to it, so the legacy path-based application and the desktop executor run the same source file through the same namespace mechanism.

## New integration code and justification

`segmentation_execution.py` is a narrow GUI/science boundary. A separate controller is necessary because Cellpose must run outside the Qt event loop, while result registration and napari layer creation must occur on the main thread. It contains no segmentation mathematics or device-selection algorithm.

The worker:

1. prepares the existing `ScientificInput` in its background thread;
2. lazily imports PyTorch and Cellpose only when Run Segmentation is used;
3. injects those dependencies into the scientific namespace;
4. executes the unchanged `segment.py` stage;
5. returns its validated masks, effective parameters, device, timings, and provenance.

Modified source files are `runtime.py`, `project_state.py`, `segmentation_panel.py`, and `desktop_window.py`. The only new source module is `segmentation_execution.py`. Tests are in `test_cellpose_execution.py`, with the prior Phase 2C.1 expectations updated only where Run Segmentation is now intentionally enabled.

The controller uses indeterminate progress and descriptive stage/log messages. It does not fabricate percentages. Elapsed time is updated by a main-thread timer. Unsafe thread termination is not implemented; project replacement, closing, and conflicting preprocessing controls remain blocked until inference finishes.

## Input contract

Input is the current confirmed working CZYX dataset. Rectangular and polygon crops, stable channel indexes, voxel spacing, anisotropy, crop offset, provisional three-dimensional MNTB ROI, polygon inclusion mask, applied Z-correction settings, and Cellpose normalization configuration come from Phase 2C.2.1.

The worker receives uncorrected working channel data and relies on the adapter's single call to `prepare_volumes()`. It uses the resulting `model_input_volume`; temporary preview layers and global TIFF-preparation arrays are never scientific inputs. Cellpose Default remains `CELLPOSE_NORMALIZE=True`, passed to `model.eval()` without pre-normalization.

## Output contract

On success, `ProjectState.segmentation_result` retains:

- immutable raw ZYX masks;
- mask shape, object count, and maximum label ID;
- segmentation channel, model, actual device, and effective `model.eval()` parameters;
- input provenance, applied Z corrections, and Cellpose normalization configuration;
- model-load and inference timings;
- completion time and optional probability volume.

A read-only `Raw Cellpose masks` Labels layer is created on the main thread with physical scale and crop translation. A failed rerun preserves the previous valid result. Changes to scientific parameters, source/crop, calibration, channel identity, normalization, or Z correction mark prior results and their layer as outdated. Closing the project releases the result and layer.

The polygon crop mask remains distinct from the provisional 3D scientific ROI. No stereology, density, measurements, filtering, mask editing, matching, or scientific export is performed.

## Error handling

Import, model-loading, backend, memory, execution, and result-validation failures retain a full traceback in the execution log and a concise status message. Incompatible or empty masks cannot be registered. Previously valid results are not overwritten by a failed rerun.

## Known limitations

- Cellpose does not expose a safe, general cancellation boundary for the existing `model.eval()` call, so cancellation is deliberately unavailable during inference.
- Progress is indeterminate because the existing stage does not provide reliable numerical completion information.
- The Labels layer is read-only; full Mask Review integration is outside this phase.
- Cloud tests mock Cellpose execution and do not validate native Apple MPS performance or OpenGL rendering.
- Two pre-existing headless napari tests may abort inside native `viewer.close()`; this is tracked separately from scientific assertion failures.

## Automated validation

- Focused execution, input-adapter, and Segmentation panel tests: 13 passed.
- Desktop and scientific regression suite excluding `test_workflow.py`: 140 passed, plus 13 subtests.
- Non-crashing workflow regression tests: 9 passed; the two known native-viewer shutdown tests were deselected and remain documented separately.
- Cellpose/PyTorch are simulated for Cloud execution tests. The test still executes the authoritative `stages/segment.py` and asserts its exact `model.eval()` image and parameter contract.

## Native macOS verification

1. Check out `feature/nucytoseg-qt6-shell` and install the project with its Cellpose/PyQt6 desktop dependencies.
2. Launch NuCytoSeg using the existing desktop command.
3. Open a small calibrated multichannel TIFF and make a modest confirmed crop.
4. Designate DAPI as the segmentation channel.
5. Calculate and apply DAPI Z-intensity correction for segmentation.
6. Select Cellpose Default normalization and verify the panel reports a valid input.
7. Select CPU and click **Run Segmentation**.
8. Confirm the UI remains responsive, preprocessing/project controls are locked, elapsed time advances, and log messages identify backend/model/evaluation stages.
9. On completion, confirm `Raw Cellpose masks` appears as a read-only Labels layer and its ZYX dimensions equal the working image.
10. Change one Cellpose parameter and confirm the previous mask layer is marked outdated.
11. Rerun with a deliberately invalid model path; confirm the previous result is retained and the complete error appears in the execution log.
12. Start a valid run and confirm Close Project/Open Image are blocked until it finishes.
13. Close the project after completion and confirm the mask layer and result summary are cleared.
14. Repeat with Automatic device selection. Confirm the log reports Apple MPS when available, or CPU fallback otherwise.

Native verification must be reported by the user; it is not claimed by headless Cloud testing.
