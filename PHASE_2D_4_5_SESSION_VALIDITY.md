# Phase 2D.4.5 — Cross-session scientific validity

## Investigation

On baseline `8764b5c3`, both sessions were segmented and measured using desktop Qt workers with a deterministic inference substitute. The first session received two saved retained-scope edits. The second used a different source channel and normalization mode. Switching, executing the other session, editing it and reopening the project preserved the first session in both directions. Tracing `ProjectState.invalidate_segmentation` found no call from session selection, widget restoration, proofreading, filter application or worker publication. The normalization apply call targeted its owner.

A separate baseline experiment reproduced both sessions becoming outdated. Calculating a first, unapplied, DAPI segmentation-only Z preview called `set_z_correction_preview → invalidate_segmentation` with global scope. Input, geometry and measurement revisions changed from `(2, 1, 2)` to `(3, 1, 3)` for Nuclear and from `(0, 1, 2)` to `(1, 1, 3)` for MAP2. Both mask-valid flags became false. Neither label array had changed. The same unconditional fan-out existed on applying and resetting corrections, changing project channel roles or names, and reapplying identical calibration. The Mac incident had no operation trace, so its specific triggering operation remains open.

A second dependency error affected recovery. The measurement controller reused the entire executed segmentation configuration. Resume required measurement provenance to equal inference provenance, including every channel's Z routing. Those checks could reject compatible geometry or prevent newly measured intensities from surviving a round trip.

## Invalidation rules

| Operation | Scientific effect |
| --- | --- |
| Switch sessions, restore controls, change layer visibility or color | Preserve validity and revisions. |
| Publish a successful inference result | Replace the captured session's masks and clear its dependent results. The existing project, session, input and geometry ownership checks still guard publication. |
| Apply a different normalization configuration | Invalidate masks and their dependents for that session. Pending controls remain separate from executed settings. |
| Change the project default segmentation channel | Invalidate sessions that follow that default. Explicitly configured sessions keep their source index. |
| Rename a source or measured channel | Invalidate the masks bound to that primary name or the measurements that use the renamed column. Indexed Z reports retain their original calculation name and the verified current alias. Incompatible named filter criteria require review. |
| Create an unapplied Z preview or reset a channel with no applied correction | Preserve scientific revisions. Replacing an applied correction with a preview withdraws the old route, as in the baseline. |
| Add or withdraw a segmentation Z route | Invalidate masks using that primary channel. Each other session is checked separately for a measurement dependency. |
| Add or withdraw a measurement Z route | Advance the measurement revision of sessions that measure the channel or use it as an enabled nuclear perinuclear marker. Invalidate their filtering and quantification. Preserve masks, geometry revision and editing history. |
| Save geometry or change a filter threshold | Keep the existing proofreading, measurement and filter controllers. A geometry commit invalidates dependent measurements in its own session. A threshold change reuses that session's current measurements. |
| Change the working crop or voxel spacing | Invalidate all sessions on the shared grid. Reapplying identical spacing preserves revisions. |

When intensities change, the previous retained IDs on the same geometry revision can seed a protected provisional edit. Scientific export still requires current filtering and quantification and a reviewed nuclear ROI. Recalculation uses complete committed geometry and the current measurement routes. The result retains separate inference and measurement provenance. A reviewed ROI can be reused across an intensity-only change on the same mask grid. The new dependency checks examine channel mappings and small option records. They add no full-volume copy to a session switch or invalidation.

## Native checkpoints and diagnostics

The two optional validity-reason fields default to missing when reading an older native v2 checkpoint. Earlier application builds that require an exact field list can reject a checkpoint saved by this build. Resume checks the recorded source, grid, primary inference route, measurement consumers, retained geometry, ROI and session ownership. A missing applied correction invalidates its consumers. A checkpoint that already says a segmentation is outdated stays outdated. Historical records lack the triggering operation and the full sequence of intervening input changes. Identical current settings alone cannot establish that those masks remained valid. Preserve the edited checkpoint when comparing with an earlier, known-current version.

To capture invalidations in a native desktop session, launch the installed application from its Python environment with logging enabled. Each affected session has a line with the operation and revisions. A switch or display change should produce no invalidation line.

```sh
python -c 'import logging; logging.basicConfig(level=logging.WARNING); logging.getLogger("nuclear_segmentation").setLevel(logging.INFO); from nuclear_segmentation.desktop import main; raise SystemExit(main())' 2>&1 | tee nucytoseg-validity.log
```

## Native macOS validation

1. Use a copy of a calibrated multichannel project. Segment and filter nuclear and cytoplasmic sessions on different primary channels. Give them distinct normalization settings. Record retained IDs/counts, a recognizable edited mask, the filter thresholds and the geometry history.
2. Save two retained-scope edits in the cytoplasmic session and recalculate. Switch to Nuclear, rerun Cellpose, filter and save an edit. Return to the cytoplasmic session. Verify its edited labels, history, controls and current status. Repeat with the roles reversed. Switch several more times with different image-channel visibility settings.
3. Calculate a first segmentation-only Z preview on one primary channel. Inspect both sessions before applying it. Apply it and verify that only masks using that primary input require a rerun.
4. On current masks, apply a measurement-only correction. Check that dependent sessions offer proofreading and Recalculate Measurements. Scientific export should be unavailable until the relevant measurements and counts are current. Save, reopen, recalculate and inspect the intensity table, reviewed ROI and export. Reopen again and verify both sessions.
5. Change the actual crop or voxel spacing. Verify both sessions report outdated masks with the corresponding reason. Open an older checkpoint already marked outdated and inspect its stored masks and saved edit history.
6. If the original symptom recurs on a plain session switch, retain the operation log, the active session ID, the checkpoint immediately before the switch and both sessions' status messages.

## Files

- `src/nuclear_segmentation/project_state.py` owns dependency scoping, revisions, diagnostics and provisional retained access.
- `scientific_input.py`, `filtering_workspace.py`, `native_project.py` and `image_export_workspace.py` bind and validate the inference and measurement records.
- `desktop_window.py`, `crop_workspace.py`, `normalization_workspace.py` and `z_correction_workspace.py` handle display restoration and preprocessing notifications.
- `segmentation_panel.py`, `geometry_panel.py`, `mask_review_panel.py` and `quantification_panel.py` present the recovery action for the affected scientific stage.
- `tests/test_session_validity.py` and `tests/test_native_project.py` cover the regression and backward compatibility.

## Automated verification

The 12 Phase 2D.4.5 cases and the existing worker, scientific-input and native-project tests passed together (57 tests). The desktop integration cases used Qt 6.11.2, PyQt6 6.11.0 and napari 0.10.0 on Linux. They exercised Qt event delivery, session restoration, label arrays, geometry commits, measurement workers and native checkpoint I/O. A deterministic runner supplied Cellpose output for the cross-session tests. Native model and GUI behavior on macOS remain for the checklist above.

All 330 distinct tests and 13 scientific subtests passed across four final groups. The focused group passed 57 tests, the remaining main group passed 268 plus 13 subtests, live plots passed four, and the legacy launcher preview passed one. A single-process attempt aborted during the legacy launcher's Qt event processing. The launcher and live plots ran in fresh processes with their original assertions. The regression run included rendered napari canvas tests under Xorg with software OpenGL.
