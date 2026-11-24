# Phase 2D.5.1 — Integrated Matching inputs

Matching opens the Nuclear Masks, Cytoplasmic Masks, Compatibility and Visualization sections in the existing desktop context panel. Select one session of each scientific role and choose **Show Matching Inputs** the first time you want to see the pair. The selectors list session and source-channel names. The panel reports segmentation and filtering validity, retained counts and any action required for a compatible pair. It shows an explicit empty-population message when a current filter legitimately retains zero objects.

## Project ownership and input contract

`ProjectState.matching_pair` stores the two session IDs for the lifetime of the open project. Selecting or renaming either source preserves the active segmentation session. A frozen `MatchingPairSnapshot` binds both sources to their project and session IDs, input and geometry revisions, measurement revisions, primary channel indexes and sorted sets of validated retained IDs. It captures the source file and available digest, series identity, time index, import axes and override, standardized working shape, calibrated spacing and crop offset, type, bounds and vertices. The label arrays are read-only views of the existing published filtering volumes.

A future worker can call `capture()` on the GUI thread and use the snapshot independently. A successful capture means both inputs passed validation. The GUI thread can call `is_current(snapshot)` before publishing results. The snapshot's `population_identity` identifies the grid, geometry owners and retained populations separately from measurement revisions. Recalculation with the same retained IDs on the same geometry restores the same population identity while making the earlier measurement snapshot stale.

The validator checks each session's role and segmentation validity, filtering validity and ownership, and the inference and measurement provenance against the project's canonical grid. Desktop import standardizes both sources onto CZYX with ZYX label arrays. Source, series, calibrated shape and spacing, crop type and offset must agree with the recorded inputs. The project binds the axis interpretation and time point at import. Each filtered volume must contain nonnegative integer labels and equal the committed effective geometry restricted to the documented positive retained IDs, with zero everywhere else. Validation reads the session's authoritative records and performs no spatial transformation.

Matching uses the existing nuclear retained population, including the filter settings for morphology, intensities, perinuclear markers, the nuclear ROI, counting planes and Z guards, plus applicable legacy review decisions. The full voxels of each retained instance enter the matching input. The ROI and counting rules govern instance inclusion. Reviewing an ROI for density or producing density results is unnecessary for inspecting the matching inputs. A pending edit recalculation or outdated measurement blocks readiness even when a provisional or historical result is visible in a segmentation workflow. Those diagnostic views remain accessible through the existing workflows.

## Display and invalidation

The embedded viewer creates two read-only Labels layers in the **Matching Layers** group. Cyan identifies nuclear masks and orange identifies cytoplasmic masks. The panel controls visibility and opacity independently, and both layers use the working grid's physical scale and crop translation alongside the fluorescence images. Recalibration updates the reference layers’ scale and crop translation together. Matching metadata carries the pair identity and source revision. Ordinary Review, Pick, Paint, Erase and the proofreading context menu exclude these layers.

**Show Matching Inputs** temporarily hides the ordinary session masks without editing their display preferences. The request, matching visibility and matching opacity are kept for the open project. Exiting the workflow removes the two layers and restores the ordinary session display. Returning revalidates the selected inputs and restores the overlays automatically when the pair is ready. A changed pair replaces its layers. Invalidated sources withdraw the overlays and restore the ordinary session display while Matching shows the recovery action. Source changes use the existing revision and invalidation paths, with a session-ID notification covering affected inactive sessions. Unrelated session changes, session switches, renames and display settings preserve compatible snapshots. Project close and replacement release the controller's layer references and reset the pair selection and matching display preferences.

Validation compares voxel geometry one Z plane at a time. Successful comparisons are cached against weak references to the published arrays and the retained IDs. Matching adds no second full label volume in project memory or in the checkpoint. napari still allocates its normal display slices and textures for each visible layer. The first validation of a new projection scans its volume on the GUI thread, so a large pair may take a moment to become available.

## Native checkpoints and deferred work

Pair selection is scoped to the open project. Reopening a project requires reselecting the two sessions. The native checkpoint format is unchanged. For older validated filtering records that lacked ownership fields, loading fills in the session and revision after validating their provenance and voxel projection. The legacy import path binds the same ownership when publishing a verified filtering cache.

Automatic matching, manual decisions, watershed splitting, association history and result persistence are deferred. The existing standalone algorithms retain their current behavior. Subsequent automatic matching should preserve nuclear-normalized overlap, deterministic tie-breaking, best and runner-up thresholds, and the distinction between classification of each nucleus and multiple nuclei associated with a cytoplasmic object.

## Modified files

| Files | Purpose |
| --- | --- |
| `matching_pair.py`, `project_state.py`, `scientific_input.py` | Pair selection, immutable snapshots, grid validation and source-session revision tracking. |
| `matching_panel.py`, `context_panel.py`, `desktop_window.py` | Matching controls and lifecycle in the embedded viewer. |
| `session_display.py`, `layer_panel.py`, `mask_selection.py`, `viewer_toolbar.py` | Temporary display suspension, layer grouping and editing protection. |
| `native_project.py`, `legacy_import.py` | Filtering ownership for previously saved and legacy-imported data. |
| `tests/test_matching_pair.py` | Scientific, checkpoint, workflow and rendered-canvas regressions. |

## Phase 2D.5.1 native macOS validation checklist

- Open a calibrated project, preferably with a nonzero crop offset. Segment and filter at least two sessions of each role. Select two inputs with an unrelated session active. Confirm that the dropdowns list the expected source channels, the active session stays selected, and renaming a source preserves the pair.
- Show the matching inputs. Toggle visibility and change opacity independently in 2D and 3D with the rail and context sections collapsed and expanded. Check the colors, overlap, calibrated positioning against fluorescence, Z navigation and scale bar. Try the ordinary viewer tools and right-click either matching layer to verify it cannot enter proofreading.
- Switch between Matching and Proofreading several times. Verify that only two matching overlays appear and that the ordinary active-session visualization and its preferences return on exit.
- Commit and undo a source edit, change its retained population, and apply a measurement-only correction while a selected source is inactive. Confirm that pending inputs require recalculation. Recalculate and verify the recovered overlays. Change an unrelated session and confirm the pair stays ready.
- Use a filter that retains zero objects and confirm the explicit empty-population status. Change the shared crop or spacing and verify that both sources require current segmentation and filtering on the new grid.
- Save, close and reopen the project. Reselect the two sources and confirm the same validated mask populations. Replace the project while the overlays are visible and verify that the previous pair, overlays and selections clear.

## Phase 2D.5.1 automated verification

All 361 distinct tests and 13 subtests passed on Linux. The 23 new Matching tests covered pair validity, native project reopening and the embedded Qt/OpenGL canvas in 2D and 3D under Xorg. The wider regression group passed 333 tests, including independent sessions, proofreading transactions, protected brushes and the legacy matching algorithms. Four live plot tests and the legacy launcher preview test passed in separate processes. The launcher had aborted during Qt event processing in the combined run, as previously observed at the baseline. The native macOS follow-up is recorded below.

## Phase 2D.5.1.1 — Restoration and empty-population feedback

Eight of the ten reported native macOS checks passed for commit `78233a3`. The developer had to request visualization again after returning to Matching. When filtering retained zero nuclear objects, cytoplasmic labels stayed visible and the developer did not see an empty-population message. The previous code put that warning only in Compatibility when both sources were valid. The Nuclear Masks section showed only the numeric count, so the explicit warning could be outside the viewport or hidden under a collapsed section. The native report did not establish which occurred.

`MatchingDisplay` now keeps the open-project visualization request independently of live layers and temporary suspension of the ordinary display. Re-entry runs the existing pair checks before restoring visibility and opacity. Outdated measurements and provisional edits keep the matching overlays withdrawn until valid measurements return. A validated empty input shows its count and a prompt to review filter settings in its own mask section. Its label layer remains empty, and the other role retains its appearance and visibility controls.

Future automatic matching should treat an empty nuclear population as zero nuclei to classify. With retained nuclei and an empty cytoplasmic population, each retained nucleus has no cytoplasmic candidate. If both are empty, there are zero nuclei to classify. These cases describe valid measurements with empty populations.

### Focused native macOS validation

- Select a valid pair, click **Show Matching Inputs** once, hide one role and set distinct opacities. Move repeatedly between Matching and Proofreading, and switch the active segmentation session outside Matching. Verify that the pair and appearance return automatically and that the ordinary display returns on exit.
- Save a geometry edit to a selected source. Return to Matching before recalculation and check the recovery message and overlay removal. Recalculate in Mask Review and return to Matching to verify automatic restoration with the saved appearance.
- Narrow the context panel and use the Mask Review filters to retain zero nuclear objects, then zero cytoplasmic objects. Scroll to each role and confirm that its count and review prompt are readable. Restore one role at a time and verify the corresponding warning clears and the restored masks are visible.
- Close or replace the project after enabling visualization. Select another ready pair and confirm that its first display requires **Show Matching Inputs**, with both roles visible at the default opacity.

### Phase 2D.5.1.1 automated verification

Linux verification passed 363 tests and 13 subtests across four groups. The 25 Matching tests covered repeated restoration, session switches, stale and provisional inputs, recalculation, project lifecycle and the real Qt/napari OpenGL canvas. The narrow-panel test drove both populations through empty and nonempty states using the debounced Mask Review volume controls. The broader suite passed 333 tests. Four live plot tests and the legacy launcher preview passed in separate processes. The focused native macOS checklist remains pending.
