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

## Phase 2D.5.2 — Integrated automatic matching

The four native macOS follow-up checks for Phase 2D.5.1.1 passed at `11f72aa`. In Phase 2D.5.2, select the measured pair in Matching, enter the overlap fractions, and click **Run Automatic Matching**. The calculation uses the two captured retained-label arrays in memory. The result belongs to the open project's selected pair and is available through `ProjectState.matching_pair.current_result`.

### Algorithm and units

The integrated and standalone paths share the plane-wise intersection counter and the `voxel_overlap_v1` classifier. The standalone `overlap_pairs`, `thresholds` and `classify` APIs retain their DAPI/MAP2 records and settings. The integrated scan counts each whole retained object's voxels directly, including portions outside every intersection. The product of calibrated Z, Y and X spacings gives the volume of one voxel in µm³.

For a nucleus with N voxels, rank each positive intersection by decreasing voxel count and break ties by increasing cytoplasmic ID. If the best intersection has I voxels and its cytoplasmic object has C voxels, the best fraction is I/N, IoU is I/(N+C−I) and Dice is 2I/(N+C). The second fraction uses the same N, with zero for an absent runner-up. The total fraction sums all intersection counts divided by N.

- **NO_MATCH** means zero intersection voxels. Both candidate IDs are null, and the overlap fractions, IoU and Dice are zero.
- **MATCH** requires a positive intersection, a best fraction ≥ the minimum (default 0.50), and a second fraction ≤ the maximum (default 0.20).
- **AMBIGUOUS** means a positive intersection failed either threshold. A tie can qualify as MATCH at settings that admit both fractions.

Both thresholds must be finite. The best minimum lies in (0, 1] and the second maximum in [0, 1]. Classification uses the original grid without registration, resampling, dilation or centroid/distance association.

### In-memory result and future checkpoint contract

| Record | Contents |
| --- | --- |
| Pair identity | Acquisition and crop grid, spacing, independent project/session/input/geometry owners, role-ordered primary channel indexes and sorted retained IDs. |
| Execution | Measurement revision of each captured source, algorithm version, whole-nucleus denominator, thresholds, ranking rule and proximity-use flag. |
| `OverlapStatistics` | Per-ID nuclear and cytoplasmic voxel counts, voxel volume in µm³, and every positive (nuclear ID, cytoplasmic ID) intersection in voxels. |
| `NuclearOverlap` | One row per retained nucleus. Nuclear ID, automatic class, nullable best and second cytoplasmic IDs, nuclear and best cytoplasmic volumes in µm³, best intersection in voxels and µm³, best/second/total nuclear fractions, candidate count, IoU and Dice. Missing best-candidate volume is null. |
| Automatic links | `accepted_cytoplasmic_id` gives the best ID for a MATCH and null otherwise. Per cytoplasmic object, including those with zero accepted nuclei, the result exposes the accepted nucleus count and a multiple-association flag. |
| Summary | Counts per class, count of cytoplasmic objects accepting two or more nuclei, and sum of their accepted nucleus counts. Individual classifications stay as calculated. |

A future checkpoint writer can serialize the sparse intersections as integer triples and expand the per-ID mappings into keyed records. Preserve exact integer IDs, nulls, units, executed settings, both source revisions and the complete pair provenance. Arrays, Qt objects and worker references are absent from the result. Reopening a future checkpoint will require rebinding and validating both persisted source identities against the restored project. The current native checkpoint contains the source sessions. Reopening it requires selecting the pair and running matching again.

### Empty populations and inspection

A validated empty nuclear population produces a current empty result with the full table schema, zero class counts and undefined class percentages displayed as a dash. This also covers dual-empty inputs. With retained nuclei and an empty cytoplasmic population, every row is NO_MATCH. Absence of a retained cytoplasmic candidate does not establish absence of biological marker expression.

**Matching Results** shows the last completed settings, class counts and fractions, and both multiple-association totals. The read-only table distinguishes an accepted automatic link from a best candidate. All rows represent automatic classifications. Manual validation is deferred. **Color nuclei by automatic classification** shows green for MATCH, yellow for AMBIGUOUS and red for NO_MATCH. It changes the colormap of the existing read-only nuclear overlay and preserves its data, IDs, visibility and opacity. Turning it off restores the nuclear input color. Leaving Matching restores the ordinary session display. Returning to Matching restores the requested overlays and rechecks eligibility for classification colors.

### Worker, validity and memory

The GUI thread validates and captures a `MatchingPairSnapshot`, immutable settings and any compatible statistics. A Qt worker scans one Z plane at a time, reporting progress, then classifies from counts. On completion the GUI thread checks the exact job, current pair snapshot (including both measurement revisions), settings and result metadata. Changing a selector or threshold retires the job even if the user immediately restores the old value. Source changes are observed while Matching is hidden. An obsolete completion or error cannot publish into a replacement project. Closing or replacing a project releases its result and cache. A worker already in flight can finish with its captured inputs. The window stays open during worker cleanup and can be closed once the worker finishes.

The project holds one completed set of statistics, shared with its result. Threshold-only reruns reuse it. Changing the grid, geometry revision, selected pair or a validated retained population evicts it. Measurement-only invalidation temporarily hides the result and colors. Recalculation can restore a **previously completed** result only for the same full population identity and executed settings. An earlier in-flight job still requires its original measurement revisions. Renaming a session, modifying unrelated sessions, changing appearance and ordinary workflow navigation preserve compatible results.

For N nuclei, C cytoplasmic objects and P positive pairs, stored statistics and results take O(N+C+P) space. Scan temporaries scale with one XY plane. As in Phase 2D.5.1, the first validation of a newly published label array also checks its planes against committed geometry on the GUI thread. Repeated captures of those same read-only arrays reuse that validation. Runtime includes the per-plane unique counts and sorting candidates within each nucleus. Threshold-only work iterates objects and sparse pairs without reading label voxels. The Qt table requests individual cells on demand. The overlay adds an ID-to-color map, and napari manages its own render buffers. Workers share read-only references to existing filtered volumes until cleanup. Peak memory during reclassification can include both the old and new row sets. High overlap complexity can still make P large.

### Files changed

- `src/nuclear_segmentation/automatic_matching.py` contains shared counting and classification, legacy adapters, settings and numeric records.
- `src/nuclear_segmentation/matching_pair.py` owns the captured job, result, validity checks and cache. `project_state.py` reconciles session changes and resets pair ownership.
- `src/nuclear_segmentation/matching_execution.py` runs the scan and classification on a QThread. `desktop_window.py` guards application shutdown while the worker is active.
- `src/nuclear_segmentation/matching_panel.py` provides controls, status, progress, the results table and overlay coloring.
- `tests/test_integrated_matching.py` exercises the science and worker lifecycle. `tests/test_matching_pair.py` extends GUI filtering and embedded canvas checks. This document records the contract and verification.

### Automated verification for Phase 2D.5.2

Linux verification used PyQt6, napari 0.10.0 and an Xorg OpenGL canvas. All 385 distinct tests and 13 subtests passed across four processes.

| Group | Passed | Coverage |
| --- | ---: | --- |
| Integrated automatic, pair and legacy automatic matching | 52 | Thresholds, nullable and large integer IDs, sparse counts, calibrated volumes, multiple associations, corrected geometry, cache reuse, stale jobs, empty GUI filters and rendered color changes in 2D/3D |
| Wider regression suite | 328 + 13 subtests | Legacy matching and splits, protected brushes, independent sessions, scientific exports, checkpoints and ordinary desktop workflows |
| Live plots | 4 | Existing plot behavior and measurement reuse |
| Legacy launcher preview worker | 1 | Existing preview-worker lifecycle |

A separate deterministic comparison loaded the original algorithm from commit `11f72aa` and compared 64 randomized volume pairs at four threshold combinations. All intersection counts, legacy output records and settings agreed exactly. Integrated classifications, candidate IDs, overlap fractions, IoU and Dice also agreed in all 256 runs.

### Focused native macOS validation for Phase 2D.5.2

1. Open a measured nuclear–cytoplasmic pair. Show both inputs, choose distinct opacities, then run at 0.50/0.20. Inspect progress, the executed settings, class fractions, linked IDs and multiple-association totals.
2. Turn on classification colors. Inspect representative IDs in 2D and 3D, toggle nuclear visibility, then move between Matching and Proofreading. Confirm the ordinary session selection and appearance on exit and the automatic colors on return.
3. Edit a threshold, confirm the outdated status and withdrawal of colors, then rerun. Restore Defaults and rerun. Inspect a nucleus near a threshold and verify the displayed settings on each run.
4. In Mask Review, filter first one role and then both to zero. Run matching at each stage. Check the empty status, table headers, percentages and NO_MATCH rows. Restore the populations and rerun.
5. Apply a measurement-only change, recalculate, and confirm compatible recovery. Commit a geometry edit or change retained IDs, recalculate the source measurements, and confirm matching requires a new run. Compare with a rename or alteration to an unrelated session.
6. Start a long run and change the pair or threshold. Check that the obsolete completion has no effect. Repeat with project close or replacement, then select a new valid pair and run again.

Native macOS validation for this phase is pending. Manual association decisions and history, guided splitting, matched density, final exports and native result persistence belong to later phases. Automatic links have no effect on quantification, ROI review or stereological eligibility.
