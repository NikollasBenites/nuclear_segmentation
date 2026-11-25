# NuCytoSeg Phase 2 completion handoff

Repository: `NikollasBenites/nuclear_segmentation` · feature branch: `feature/nucytoseg-qt6-shell`

## Revision ledger

| Milestone | Commit | Verified behavior |
| --- | --- | --- |
| Original Phase 2D.5.3 baseline | `7a6be0a78dd24c7bbc1eec9b3bb5b85d16c126b8` | The developer passed all nine native macOS checks. |
| Phase 2D.5.4 and starting commit for this continuation | `534ed13040b94b9494102388c790f09c65015878` | Calibrated Matching highlight, explicit parent handoff, marker-independent watershed, sparse geometry history and read-only proposal. |
| Phase 2D.5.5 | `e86481c09534b84834f5d71ff3a676ad9d7fda59` | Independent manual decisions and undo in Proofreading. |
| Phase 2D.5.6 | `f479fe397036cdda76027c13a474419ae4a58032` | Native association checkpoint validation and reviewed QC plots. |
| Phase 2D.6, final scientific implementation | `aa2cf78669569ff5036300a298eded6eea67b58b` | Eligible-population matched nuclear quantification and opt-in scientific export. |

A separate documentation commit carries this handoff. The feature work targeted the feature branch exclusively. The verified main reference was `2fed532706aa56122fe7091586722d643fc189b7`.

## Scientific and checkpoint contracts

- **Geometry.** The calibrated 3D local watershed uses every qualifying retained nucleus, compact markers and the complete parent. Seedless components retain the original ID. Preview, Apply and Save are separate. Native replay verifies compact seed evidence and volume conservation after independent reference edits.
- **Manual review.** An explicit decision records the selected sessions, object IDs, grid and population provenance, revisions, automatic evidence at decision time, UTC timestamp and reviewer comment. Only the user records a manual link. Decisions survive compatible threshold reruns. Incompatible geometry or retained membership requires review. Pair-specific Undo preserves the event audit trail.
- **Persistence.** The v2 native format has an optional association block. The open worker validates selected source geometry signatures, overlap counts and classifier outputs before publishing a compatible result. Manual history replays independently. Checkpoints from earlier phases open without association records. Earlier readers can lose association records on resave. Use the new version for reviewed projects. Older readers reject geometry history containing the new watershed operation.
- **QC.** Automatic and manual summaries use all retained nuclei. Multiple-object counts use retained cytoplasmic IDs with two or more links. Reviewed completion includes explicit Matched, Unmatched and Uncertain decisions. Volumes and ECDFs use within-group denominators and display sample counts.
- **Quantification.** The eligible set must agree with current nuclear Quantification on the reviewed ROI. Each matched nucleus counts once. Automatic and reviewed densities independently divide their matched counts by the same measured effective sampling volume in mm³. Cytoplasmic objects contribute only to link and multiplicity statistics. Empty eligible populations have zero density and undefined association fractions.
- **Export.** Matched quantification is an explicit scientific export option. Exact-ID CSV tables carry automatic evidence, final decisions, linked retained measurements, sparse intersections and separate multiple flags. The JSON summary records units, dependencies, sampling provenance and decision history. Final target IDs require current manual Matched decisions. Unreviewed or uncertain associations remain unresolved. Missing cytoplasmic association does not establish biological absence.

## Changed files

Application module paths below are relative to `src/nuclear_segmentation/`. Each phase commit contains the exact diff.

- **2D.5.4** changed `desktop_window.py`, `geometry_edit.py`, `geometry_panel.py`, `guided_panel.py`, `guided_split.py`, `label_geometry.py`, `map2_split.py`, `mask_selection.py`, `matching_explorer.py`, `matching_inspection.py`, `matching_panel.py`, `preview_history.py`, `segmentation_execution.py` and `watershed.py`. Tests are in `tests/test_guided_split.py` and `tests/test_matching_explorer.py`. Reports are in `PHASE_2D_4_PROOFREADING.md` and `PHASE_2D_5_MATCHING.md`.
- **2D.5.5** changed `association_review.py`, `association_panel.py`, `desktop_window.py`, `geometry_panel.py`, `matching_pair.py`, `project_state.py`, `tests/test_association_review.py` and `PHASE_2D_5_MATCHING.md`.
- **2D.5.6** changed `association_checkpoint.py`, `association_review.py`, `desktop_window.py`, `live_plots.py`, `matching_pair.py`, `matching_panel.py`, `native_project.py`, `reviewed_qc.py`, `tests/test_association_checkpoint.py` and `PHASE_2D_5_MATCHING.md`.
- **2D.6** changed `association_review.py`, `image_export_panel.py`, `image_export_workspace.py`, `matched_quantification.py`, `quantification_panel.py`, `reviewed_qc.py`, `tests/test_matched_quantification.py` and `PHASE_2D_6_MATCHED_QUANTIFICATION.md`.

## Automated verification and limitations

Linux verification used PyQt6 6.11.0, napari 0.10.0 and an Xorg OpenGL display. Results are distinct tests per phase across processes; the phase totals overlap.

| Phase | Linux gate |
| --- | --- |
| 2D.5.4 | 224 previously verified tests. All 19 watershed and explorer checks passed again at entry. |
| 2D.5.5 | 406 tests and 13 subtests. |
| 2D.5.6 | 409 tests and 13 subtests. The corrected checkpoint loader passed the native/project and matching regressions and all focused association tests. |
| 2D.6 | 413 distinct tests and 13 subtests passed. The broad run passed 408 tests and 13 subtests, all four live-plot tests passed, and the launcher worker passed. |

The legacy launcher and live-plot tests ran in separate processes. An intermediate Linux run reported a short viewport at the minimum window size. The targeted layout group and final broad run both passed. Include this size in native validation. Native macOS and Windows validation of the new phases remains outstanding. No unresolved scientific implementation blocker was found. Watershed refinement is read-only. Historical decisions on absent objects remain audit records. Dense crops can approach full-volume working memory. Checkpoint validation scans retained geometry and hashes source masks, so large projects may take longer to open. Scientific IDs beyond spreadsheet precision should be imported explicitly as integers or strings.

## Consolidated native macOS validation

Use a copy of a calibrated real acquisition with a nonzero crop offset, anisotropic spacing, multiple candidate targets, an ambiguous nucleus, an unmatched nucleus and an unrelated session. Record OS, hardware, Qt/napari versions, branch commit, sessions, revisions, settings and observed counts.

1. Run automatic matching. Focus a parent and a candidate in 2D and 3D. Switch dimensions while focused. Isolate, restore, replace an input and leave Matching. Check appearance, active session and highlight cleanup.
2. Use Review in Proofreading. Verify the selected parent, nuclear fractions and seed threshold. Preview a parent with two or more seeds and a seedless component. Inspect the seed overlay, calibrated volumes and exact support. Cancel, regenerate, Apply, Undo, Redo, Discard, then Save. Recalculate measurements and rerun Matching.
3. Navigate and filter manual review. Confirm an automatic link, select another retained target, enter comments and exercise all four review states. Inspect both object highlights, isolation and Undo. Check that masks, automatic rows and measurement revisions remain stable.
4. Change thresholds and rerun. Verify compatible manual decisions survive. Change source geometry or retained IDs, recalculate and confirm stale decisions require review. Switch to another pair and back. Try project replacement and invalid inputs.
5. Save, quit and reopen a reviewed project. Compare sources, thresholds, IDs, automatic counts, historical comments, grid and Undo. Open an older checkpoint. On a test copy, verify an incompatible or damaged association checkpoint cannot publish a current result.
6. Open QC and compare automatic/reviewed counts, changed decisions, uncertainty, completion, multiple associations, histograms and ECDFs. Repeat with partial review, no links and empty retained populations. Change a decision with QC previously open and check the updated graph.
7. Accept the nuclear ROI. Compare eligible IDs, guard settings, directional counting and effective volume with nuclear Quantification. Independently divide the two matched counts by the effective mm³. Verify two nuclei on one target count twice in nuclear density and once as a multiple cytoplasmic object.
8. Export matched outputs alone and with existing scientific outputs. Join the two measurement tables using role/session and exact ID. Compare the JSON provenance, final nullable targets and both sets of flags with the GUI. Invalidate a source and verify export recovery after recalculation.

## Windows and next steps

Repeat the scientific checks on Windows with the supported Python/Qt/napari environment. Check OpenGL rendering and 2D/3D transitions, DPI scaling and scroll bars at the supported minimum window size, keyboard navigation and Undo, Unicode and long destination paths, existing-folder protection, checkpoint close/reopen and worker cleanup at exit. Archive observed results against the exact feature commit before release. Phase 3 remains deferred.
