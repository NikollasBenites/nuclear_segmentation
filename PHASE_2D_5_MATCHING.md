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

The developer completed all nine native macOS checks for Phase 2D.5.2 at `816e534623b98761c6c64b414cc354acdb0a2cdd`. Automatic matching, classification colors, threshold reclassification, multiple associations, empty populations, measurement recovery, geometry and filtering invalidation, session independence and stale worker protection all passed. Manual association decisions and history, guided splitting, matched density, final exports and native result persistence belong to later phases. Automatic links have no effect on quantification, ROI review or stereological eligibility.

## Phase 2D.5.3 · Multiple Association Explorer and Automatic QC

### Approved sequence

| Phase | Responsibility |
| --- | --- |
| 2D.5.3 | Inspect accepted automatic links and positive-overlap candidates in Matching. Evaluate classifications in Quality Control. |
| 2D.5.4 | Initiate nuclear-guided watershed in Proofreading. |
| 2D.5.5 | Review and decide manual associations in Proofreading. |
| 2D.5.6 | Persist associations and perform final QC. |
| 2D.6 | Quantify and export matched results. |

### Inspect an association

Run Automatic Matching and expand **Multiple Associations**. Each row represents one retained cytoplasmic label with at least one accepted MATCH. The default filter requires two or more accepted nuclei. Clear **Only multiple associations** to include singly linked objects. Sort a column or use Previous and Next to move through the visible order. The selected cytoplasmic ID and candidate survive sorting and filtering while the object remains in the list.

For the selected object, the lower table includes **every** positive nuclear intersection, including ambiguous nuclei and nuclei automatically linked elsewhere. **Accepted here** requires the nucleus's accepted cytoplasmic ID to equal the selected ID. Intersection volume is intersection voxels × calibrated voxel volume. The fraction divides by the complete retained nucleus's voxel count, including voxels outside all cytoplasmic masks. The upper table's candidate count counts distinct nuclear IDs. Both tables retain integer IDs through lookup and sorting.

Choose an object or candidate row and use **Focus Selected Object**. Focus chooses an occupied voxel near the center of an occupied section, applies the layer's data-to-world transform, sets the optical plane in 2D and centers the camera in the current dimensionality. **Isolate Associated Masks** displays the cytoplasmic object in orange and accepted nuclei in green. The checkbox adds other candidates in magenta. Focusing one of those other candidates during isolation also enables the checkbox so the focused geometry is visible. **Show All Masks** restores the previous colormaps, visibility and opacity, including a previously hidden role.

The explorer uses the two embedded, read-only matching layers. Ordinary mask selection and the active segmentation session retain their own ownership. Leaving Matching removes isolation and restores ordinary session appearance. Re-entry reuses the selected identities only for the same current result. A newly published result starts its own list. Outdated sources or settings withdraw the old selection, isolation and classifications. Project replacement also resets the filter and candidate-display preference.

### Proofreading handoff for Phase 2D.5.4

The explorer constructs an immutable `AssociationSelection` from a published result. It carries a weak reference to that result, the pair population identity, both session revisions (including project, session and geometry IDs), the cytoplasmic ID, accepted nuclear IDs, other candidate IDs and the focused role and label. Phase 2D.5.4 uses this context for the explicit **Review in Proofreading** action described below.

### Matching graphs

**Show Matching Graphs** opens the Matching category in the existing Quality Control window. The category shares the segmentation graphs' Matplotlib canvas, navigation toolbar, theme and deferred redraw timer. All six views describe automatic classifications before manual review.

| View | Population, units and interpretation |
| --- | --- |
| Classification proportions | Counts and fractions of **all retained nuclei**, including NO_MATCH. Zero retained nuclei give an empty-state message. |
| Nuclear volume distributions | Nuclear volume in µm³. Counts show the number of nuclei per bin. Relative frequency divides those counts by the class sample size. |
| Nuclear volume ECDF | Cumulative fraction of each nonempty class at or below a volume in µm³. Tied volumes contribute their combined count to the step. |
| Accepted nuclei per cytoplasmic object | Counts of **all retained cytoplasmic objects** at each observed link count, including zero. Objects with two or more accepted nuclei have a distinct color. |
| Best and second-best overlap | Two histograms of fractions of the complete nucleus on common 0–1 bins. Each retained nucleus contributes once to each histogram. Vertical lines show the executed thresholds. |
| Classification decision space | Best fraction on X, second-best on Y, colored by automatic class. The shaded triangle satisfies second ≤ best and best + second ≤ 1. Coincident nuclei share a marker whose area grows with log count, including NO_MATCH at the origin. The vertical and horizontal lines use the executed settings. |

For both **Nuclear volume distributions** and **Reviewed nuclear volumes**, choose **Overlaid** or **Separate panels** in the same QC window. Both start with counts and 30 bins. Relative frequency gives each nonempty group a total bin height of one. Both layouts use the same bin edges and leave 5% headroom above the tallest displayed bin. Separate panels share both axes, including during zoom and pan. Changing these controls redraws the published measurements.

Each view reports nuclear and cytoplasmic sample sizes. Empty groups contribute no histogram or ECDF. An empty cytoplasmic population gives an empty link-count plot while the nuclear plots retain every NO_MATCH nucleus. Automatic classifications alone do not establish biological cell identity.

### Validity, memory and limitations

The published result remains owned by the project pair. A Qt signal reports job, executed-result, settings and failure transitions. The graph widget also observes source validity independently of the visible workflow. Outdated and failed states immediately hide the scientific canvas and offer recovery actions. Compatible measurement recalculation can recover the original result. A threshold rerun publishes a new classification from the existing sparse statistics. Display changes, renames and unrelated session operations preserve the scientific result.

The explorer builds a reverse index from the published counts and sparse positive intersections, using O(N+C+P) additional space for N nuclei, C cytoplasmic objects and P positive pairs. Table models format visible cells on demand. Selecting another cytoplasmic object prepares only its candidate rows. Sorting the object table costs O(L log L) for L visible linked objects. Classification groups, overlap coordinates and other plot inputs are computed on demand for the current result. Hidden graphs defer rendering.

Focus normally searches within the measured bounding box, using at most one section of candidate coordinates at a time. If a measurement lacks usable bounds, the first focus locates that label plane by plane. The display caches visited positions for the current geometry and clears them on layer or result replacement. Isolation changes sparse label colormaps on existing arrays. Napari still allocates its usual render buffers. Large populations can make index construction, table sorting, plotting or a first uncached focus noticeable. Inspection is scoped to the open project. Native matching persistence, final exports and matched density remain deferred.

### Files for Phase 2D.5.3

- `matching_explorer.py` implements the sparse index, table models, navigation and selection context. `matching_inspection.py` handles calibrated focus and reversible isolation. `mask_selection.py` provides the shared plane-bounded label lookup.
- `matching_panel.py` integrates inspection into the existing pair and layer lifecycle. `project_state.py` and `matching_pair.py` expose result transitions.
- `matching_plots.py`, `live_plots.py` and `desktop_window.py` implement plot projections and the Matching category in the existing QC window.
- `tests/test_matching_explorer.py` covers scientific values, UI lifecycle, scientific validity and a real embedded Qt/napari canvas. This document and `PHASE_3_UX_ROADMAP.md` record the phase requirements and validation.

### Linux regression results

Under PyQt6, napari 0.10.0 and an Xorg OpenGL canvas, **394 distinct tests and 13 subtests passed** across separate pytest processes.

| Group | Tests passed |
| --- | ---: |
| New explorer, scientific plots, graph lifecycle and real canvas | 9 |
| Existing integrated, pair and automatic matching | 52 |
| Wider regression suite, including proofreading, geometry, protected brushes and legacy matching | 328 plus 13 subtests |
| Existing live plots | 4 |
| Legacy launcher preview worker | 1 |

The canvas test checks rendered differences with and without overlapping candidates in 2D and 3D, calibrated focus on a cropped image, read-only shared label arrays and restoration of prior appearance. The graph tests check plotted values, empty groups, executed thresholds, worker publication outside Matching and immediate stale-data withdrawal. The new suite also checks scientific IDs above 2⁶³.

### Focused native macOS validation for Phase 2D.5.3

1. Open a cropped, calibrated pair containing a multiply linked object, a singly linked object and a nucleus with a positive overlap accepted elsewhere. Run matching. Compare both tables with the per-nucleus automatic result, including exact IDs, accepted counts, voxel counts, calibrated volumes and whole-nucleus fractions.
2. Select a candidate, toggle the multiple-only filter, sort in both directions and navigate Previous and Next. Verify the selected identity while its row stays in the list, the candidate table after navigation and the disabled controls at the ends.
3. Set distinct role visibility and opacity. Focus the object and a candidate over the fluorescence image. Check crop offset, optical plane and calibrated center in 2D. Repeat in 3D. Isolate accepted nuclei, include the other candidates and restore the prior appearance. Try selecting the matching layer for painting and confirm the masks and active session are preserved.
4. Leave Matching during isolation. Inspect ordinary session layers in Proofreading and return. Change a threshold or invalidate a source measurement and check that old highlighting withdraws immediately. Recalculate a compatible source, then change its retained population and repeat. Replace the project and check for residual selection or layers.
5. Open **Show Matching Graphs**. Inspect the six plots, including common volume bins, ECDF ties, zero-link objects, the NO_MATCH origin and executed thresholds. Start a threshold rerun, switch to Quality Control and confirm automatic refresh. Rename a selected session and modify an unrelated session. Check that current graphs persist.
6. Invalidate source measurements with Matching hidden. Check the QC recovery message and restoration after compatible recalculation. Close the graph window, rerun, reopen and check the new settings. Test zero nuclei, zero cytoplasmic objects, both empty, a single class and a valid pair without multiple associations.

The developer validated all nine Phase 2D.5.3 checks on native macOS at `7a6be0a78dd24c7bbc1eec9b3bb5b85d16c126b8`. The Multiple Association Explorer, candidate inspection, sorting and navigation, calibrated focus, isolation and restoration, inspection lifecycle, automatic QC graphs, graph refresh and recovery, and empty populations and project reset all passed.

## Phase 2D.5.4 · Focus highlight and nuclear-guided watershed

### Inspect and hand off a parent

**Focus Selected Object** highlights the selected cytoplasmic parent or nuclear candidate in cyan. A padded binary crop follows the source layer's data-to-world transform, including the acquisition crop offset and physical ZYX spacing. It provides the same read-only emphasis on a 2D section and on a rotated 3D view. The classification colormap, ordinary masks, their opacity and the active scientific session retain their settings. Only one Matching highlight exists. Changing the focused object, restoring all masks, removing an input layer, leaving Matching, replacing the project or losing the current result withdraws it. Turning off other candidates during isolation also withdraws a highlight on one of those candidates.

**Review in Proofreading** selects the inspected parent even when the candidate table has focus. It verifies the immutable association selection against the published result, current project, acquisition grid, source session revisions and retained populations. The existing session switch selects the cytoplasmic target. Proofreading opens at an occupied voxel of the parent with the retained mask selected. Geometry editing starts only when the user requests an edit or a watershed preview. A busy project, pending filter change, active editor or obsolete selection blocks the handoff.

### Read the candidate evidence

The contextual **Nuclear-guided Watershed** section identifies both sessions and the parent. Each candidate has a positive intersection with that parent. Its row shows the nuclear ID, automatic classification, intersection and complete nuclear voxel counts, their ratio and seed eligibility. The ratio includes the entire retained nuclear object on the working grid. This also includes candidates classified as ambiguous or automatically linked to another parent.

The minimum seed overlap fraction starts at **0.50**. Every candidate with fraction ≥ the entered value qualifies. The threshold must be finite and greater than zero and at most one. A preview needs two or more qualifying nuclei. Changing this field affects the proposed partition only. The automatic classifier retains its own thresholds.

### Partition and conservation

The generic core in `watershed.py` accepts a 3D Boolean target support, integer markers and physical spacing. Both the integrated proposal and the legacy adapter use it. Inside the complete parent's tight bounding box, each qualifying intersection supplies a compact positive marker. The elevation is the negative Euclidean distance transform of the parent support, sampled with calibrated Z, Y and X spacing in µm. Watershed uses face connectivity and the parent as its mask. The output maps compact regions to exact integer child IDs allocated above the current, original and historical target labels, including excluded IDs.

A connected component with a single seed belongs to its child. Each seed voxel stays in its corresponding child. Seedless components keep the parent ID and require further inspection. The preview reports their component count, voxel count and volume. With no residual the parent ID disappears. Parent volume equals the sum of child volumes plus residual volume. Each volume is its integer voxel count multiplied by Z × Y × X spacing in µm³. Raw Cellpose geometry, the reference segmentation and voxels outside the parent retain their original values.

### Preview, Apply and Save

**Preview Watershed** captures the retained pair and calculates the proposal in a Qt worker. Publication rechecks the handoff and discards an obsolete completion. The embedded editing viewer shows proposed children and a separately switchable nuclear reference containing the qualifying seed voxels with their scientific IDs. The panel lists the nucleus-to-child mapping. Use 2D or 3D and toggle **Show nuclear seed voxels** to examine the boundaries underneath.

The guided preview uses a dedicated filtered transaction with read-only geometry. **Cancel Operation** restores the pre-operation working masks. **Apply Operation** checks the live reference and target, accepts one sparse step and enables preview Undo and Redo. **Discard Changes** exits without a committed revision. **Save Geometry Changes** checks the source context again, replays the accepted change and commits one target-session revision. Other structural operations and brush edits require a separate transaction. The existing manual split, merge and brush paths remain available for ordinary proofreading.

The commit invalidates the target's measurements and downstream quantification through the existing geometry dependency rules. It withdraws the affected Matching result, overlap cache, explorer, classification colors and QC data. The existing automatic recalculation option can schedule target measurements after saving. Once both measured populations are compatible, return to Matching and run the classifier again. The seed-to-child mapping records how the geometry was made. Association decisions continue to come from the automatic classifier.

### History, checkpoint and memory contract

A single `nuclear_guided_watershed` record contains the sparse changed indexes, before/after labels, retained population changes, signatures of the complete source and resulting target geometry, timestamp and revision. Its parameters identify the generating project, source and target sessions and revisions, parent, candidates and complete-object fractions, threshold, ordered seed and child IDs, spacing, method, bounds, voxel counts and volumes. The manual refinement flag is false. Native checkpoints retain the existing representation for all earlier operations. Older applications reject histories containing the new operation type.

At Apply, the live pair verifies full-object denominators and actual seed locations. The record encodes each seed intersection as runs of local C-order voxel indexes. Replay can verify those recorded locations belong to their recorded children, only parent voxels move to unused IDs, component membership and volume accounting hold, and the sparse delta reproduces the target signature and retained IDs. Historical candidate statistics describe the source at acceptance. Reopening a checkpoint does not recompute historical measurements from today's nuclear session. Committed Undo uses the existing reverse delta. A mixed transaction containing a guided step is invalid.

The EDT, markers, component labels, seed overlay and proposed child array use the parent box. Measured bounds are checked against the parent count, with a plane-by-plane fallback. Focus uses a padded object crop. The existing geometry editor still needs its working preview and commit/replay volume. The filtered source supplies the cancel snapshot. Checkpoint history grows with changed voxels and seed runs. A parent spanning most of an acquisition can still require nearly full-grid working memory. Unsigned editing labels can widen to uint64 when the child allocation exceeds uint32. Exhausted supported integer ranges produce a capacity error.

### Linux regression results for Phase 2D.5.4

**224 distinct tests passed** with PyQt6 6.11.0, napari 0.10.0, scikit-image 0.26.0 and an Xorg OpenGL display. The combined run passed 220 tests across guided splitting, Matching, geometry editing, protected brushes, mask review, the desktop shell, session validity, legacy watershed and native projects. The four live-plot tests passed in a separate process. An earlier combined run missed one live-plot debounce deadline. Its assertions passed on the isolated rerun.

The real-canvas checks compare rendered images with the focus or seed layer visible and hidden, including a dimensionality switch with focus active. Synthetic tests cover calibrated EDT against an independent watershed calculation, exact-threshold candidates, seedless components, stale worker publication and transaction rejection, and sparse IDs above 2³² and 2⁶³. A native project save/reopen test changes the reference geometry after the guided commit and verifies target replay and committed Undo.

### Focused native macOS validation for Phase 2D.5.4

1. On a calibrated crop, focus the parent and then two nuclear candidates with classification colors on. Check the occupied section, XY offset, cyan highlight, ordinary opacity and active session. Switch 2D → 3D → 2D while focused. Repeat during isolation.
2. Change row, remove an input overlay, leave Matching, invalidate the result and replace the project in separate trials. Check that each obsolete highlight disappears. Restore the pair and focus again.
3. Starting with the nuclear session active and a candidate selected, choose **Review in Proofreading**. Check the cytoplasmic session, parent ID, camera focus and idle editor. Try a stale or busy handoff and read the recovery message.
4. Inspect a parent with accepted and other overlapping candidates. Compare complete nuclear counts and the exact threshold boundary. Raise the seed threshold until fewer than two qualify. Check that the automatic settings and result stay current.
5. Preview an anisotropic parent with multiple seeds, a disconnected one-seed component and a seedless component. Toggle the nuclear reference in 2D and 3D. Check seed positions, child IDs and residual counts and volumes against the reference mask.
6. Try to paint the preview. Cancel, regenerate, Apply, Undo, Redo and Discard. Compare the working and committed masks at each step. Repeat and save with automatic recalculation off, then on.
7. On commit, verify the original Cellpose masks, nuclear session, unrelated sessions and excluded objects. Inspect the target's provisional population and the withdrawn Matching results. Recalculate and rerun Matching.
8. Save Project, quit and reopen after a guided commit. Check child and residual volumes, ordered history, seed mapping and Undo of the saved geometry. Repeat after an independent nuclear geometry change. Open an older native checkpoint.

Native macOS sign-off for Phase 2D.5.4 is pending. Controlled painting of watershed children, manual seed selection, general user-seeded workflows, manual association decisions, association persistence, matched density and final matching exports remain deferred.

## Phase 2D.5.5 · Manual association review

Select a measured pair and run Matching. Open Manual Association Review in Proofreading to navigate retained nuclei by automatic class and manual state. The target selector lists positive intersections in descending voxel count and accepts any retained target ID, following the legacy spatial review workflow. Focus or isolate the objects, then explicitly confirm the automatic link or record a decision and optional comment. Undo restores the preceding decision for the selected pair. Geometry editing uses its own transaction.

Decisions belong to the project and ordered session pair. Each event retains the automatic class, accepted target and thresholds at review, manual origin, UTC timestamp, source channel and input/geometry revisions, acquisition grid and retained-ID digests. Compatible threshold reruns, renames and unrelated sessions preserve decisions. A different geometry revision, grid or retained population yields Requires Review. Decisions on absent nuclei remain historical. Replacing the project creates a fresh review context.

Linux automated gate: 406 distinct tests and 13 subtests passed across the broad, isolated live-plot, isolated launcher and focused review runs. Native macOS and Windows checks remain pending.

## Phase 2D.5.6 · Checkpoint and final QC

Native projects with executed matching or review history store the selected sessions, grid, retained counts and population signatures, both source revisions, measurement revisions at classification, thresholds, sparse intersections, automatic rows, accepted link counts and ordered manual decision/undo events. Older checkpoints open with an empty review context. The open worker verifies compatible automatic evidence against the restored complete retained arrays before publication. A dependency mismatch leaves the saved automatic result historical and requires a rerun. Live project identities are rebound during restoration. Full mask arrays use the existing session checkpoint representation.

The Matching category in Quality Control compares automatic outcomes and explicit review outcomes across all retained nuclei. Completion includes Matched, Unmatched and Uncertain decisions. Changed counts compare both status and accepted target. Uncertain is a subset of reviewed nuclei. Stale decisions are excluded from reviewed links. Multiple-object counts use distinct retained cytoplasmic IDs with at least two links in each independent analysis. ECDFs report within-group cumulative fractions. Group legends report sample counts.

The checkpoint worker also checks signatures of the saved effective source volumes. Files with incompatible current populations keep manual history for inspection. A failed or interrupted classification requires rerunning. Older writers can omit the new optional association block on resave.

Linux gate: 409 distinct tests and 13 subtests passed across runs. The initial round-trip regression caught duplicate undo replay during validation. Validation now uses a separate history instance. The corrected checkpoint, matching and native-project group passed 60 tests, and all five final association tests passed, including rendered QC updates. Native platform validation is pending.

### Phase 2D.7 association inspection

Under **Multiple Associations**, choose which direction to inspect. The original explorer starts with cytoplasmic objects that have multiple accepted nuclei. **Previous Object** and **Next Object** change the parent. **Previous Nucleus** and **Next Nucleus** inspect the parent's overlapping nuclei. **Follow selected object** starts enabled and moves the current viewer to an occupied, calibrated position. Turning it off keeps the camera in place while the highlight changes.

**Multiple Cytoplasmic Candidates** lists each retained nucleus with positive intersections with at least two retained cytoplasmic IDs, across all automatic classes. Sort the tables or filter by class. Each candidate row reports its intersection voxel count, fraction of the complete nucleus, complete cytoplasmic volume in µm³ and other automatically linked nuclei. These figures come from the published sparse matching result. Several overlaps can be biologically plausible.

Select candidate rows with the platform's multiple-selection gesture. The summary reports the sum of their complete volumes and the fraction of the complete nucleus covered by their disjoint label supports. It identifies other retained nuclei with a positive intersection or a current explicit matched decision on a selected target. Isolation and navigation are display operations. **Review Merge in Proofreading** transfers the selected IDs to the cytoplasmic session for explicit review.
