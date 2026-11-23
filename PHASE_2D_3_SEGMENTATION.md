# Phase 2D.3 — native macOS validation

Use a calibrated multichannel DAPI/MAP2 stack, a known nuclear reference analysis and a locally available model appropriate for MAP2. Record the image series, working crop, voxel spacing, Cellpose version, model names or paths, and actual device for comparisons.

1. Import the stack. Verify DAPI and MAP2 channel indices and the Z/Y/X spacing. Apply any shared crop and channel-specific Z corrections before running the two sessions.
2. In Nuclear, run the validated DAPI configuration. Compare raw label geometry, calibrated volumes, channel intensities, retained IDs, and the reviewed nuclear density export with the reference from the same settings and device.
3. Create a Cytoplasmic session on MAP2. Choose its model explicitly. Verify the normalization preview uses MAP2 and the same original or Z-corrected route as inference. A custom preview from the wrong channel or route should give a readiness message.
4. Run MAP2 on the same working grid. Exercise 3D and 2D with a positive stitching threshold on a representative crop. Check the reported mode, model, device and mask alignment through Z. Inspect MAP2 mask quality against the image. Synthetic execution tests do not assess model suitability.
5. Calculate cytoplasmic measurements. Check a selected label's volume against its voxel count times the calibrated voxel volume. Inspect shape and channel intensities, then change an intensity threshold and verify the retained IDs. The Quantification page should explain that density is available for nuclear sessions.
6. Switch repeatedly. Inspect the same numeric label in each session and verify its overlay, measurements, and filter status belong to the active session. Change a future-run parameter, then try a missing MAP2 model path. Verify the failure appears in the log and both prior results remain available.
7. Save with MAP2 active, close the application and reopen. Check the active session, pending controls, executed settings, raw label IDs, measurements, and current/outdated indicators for both roles. Opening should require no inference. Repeat with a native v1 project and an imported legacy nuclear analysis.
8. On the target Mac, repeat launch, session switching and shutdown. Check Apple MPS selection when available and inspect napari rendering in both 2D and 3D.

## Checkpoint compatibility

The writer continues to use checkpoint version 2. Each session carries its normalization controls, applied configuration and current flag. The reader accepts Phase 2D.2 records that used shared normalization and migrates native v1 records into the original nuclear session. One normalization preview volume is displayed at a time. Switching sessions releases the preview pixels and retains the settings and applied report. Shared calibration, crop and Z-correction changes retain the existing project-wide invalidation behavior.

## Qt and napari shutdown

Earlier headless runs reported native shutdown aborts in the real-viewer launcher and CLI tests. Run those tests in separate processes when diagnosing teardown. The synthetic Cellpose tests exercise the existing stage and worker without downloading weights. Native rendering, real MAP2 segmentation quality, and Apple MPS execution require the Mac checks above.

On Linux with Qt offscreen, the broad run passed 251 tests and 13 subtests. The two real-viewer tests passed individually. An additional run with all 253 tests in one process aborted in the event loop of `test_launcher_preview_worker_does_not_run_cellpose`. That test passed in the broad run. The combined headless lifecycle remains unverified.
