# Phase 2C.3.1 — Mask Review GUI Integration

## Architecture and reused components

The Mask Review workflow now presents the existing segmentation and selection state in the NuCytoSeg Context Panel. It does not introduce another selection, highlight, mask-ID, review-decision, or measurement implementation.

Reused unchanged:

- `MaskSelectionController` remains the only 2D/3D picking and selected-ID owner.
- Its existing `selection_changed` signal updates both the persistent `SelectedMaskPanel` and the workflow page.
- Its existing cropped highlight layer remains the only selection highlight.
- `ProjectState.segmentation_result` and `segmentation_is_current` remain the authoritative raw-result lifecycle.
- `SegmentationController` remains the owner of the read-only raw Labels layer.
- napari `Labels.selected_label` and `show_selected_label` provide selected-only visualization without modifying label data.
- `ManualReview` remains the authoritative future implementation for whole-object keep/exclude decisions.

The existing Review / Select toolbar mode, direct 2D clicking, 3D ray picking, background clearing, active-layer separation, Paint/Erase isolation, and physical transforms are unchanged.

## New GUI integration

`MaskReviewPanel` is the only new source module. A dedicated widget is necessary to replace the workflow placeholder while keeping presentation separate from selection and scientific state. It contains compact collapsible sections for:

- Selection: selected Mask ID, active segmentation layer, segmentation status, and total detected objects.
- Selected Mask Information: mask role plus an explicit placeholder for measurements that are not yet integrated.
- Visualization: show all masks, show selected mask only, and clear selection.
- Review Status: current/missing/outdated state and disabled manual-decision controls.

The panel stores no napari layer or mask-array reference. Button handlers query the existing controllers at activation time, preventing stale layer ownership. Showing a selected mask changes only napari display properties. Raw arrays remain immutable and are not copied.

`ContextPanel` now installs this page in its existing scrollable Mask Review workspace. `DesktopWindow` forwards existing selection signals and implements the three small visualization connections. The persistent Selected Mask summary remains visible below the Layers panel and is not duplicated or replaced.

`SegmentationController.result_layer` is a read-only property that returns its existing layer only while it remains registered with the viewer. It creates no additional result state.

## Scientific safeguards

The legacy `ManualReview.apply()` combines manual choices with automatic filtering and mandatory eligibility. Its panel also requires valid filtering state and an accepted MNTB ROI before recording decisions. The independent desktop does not yet provide integrated measurements, automatic filters, reviewed scientific ROI, guard-zone eligibility, or `current_keep`.

Therefore Keep, Exclude, Automatic, and Undo controls are deliberately disabled. No `ManualReview` object or decision record is created in this phase. This avoids bypassing ROI, Z-guard, or counting safeguards. The polygon crop mask remains distinct from a reviewed scientific ROI.

## Lifecycle synchronization

The page updates when segmentation succeeds, becomes outdated, is replaced, or is cleared. Failed reruns emit no replacement result and therefore preserve the prior summary and mask layer. Project closure and image replacement clear selection and result presentation. Removed layers are never retained by the page.

## Automated validation

Focused tests cover page activation, signal integration, changing Mask IDs, background clearing, selected-only/all-mask visualization, outdated state, failed-rerun preservation, result replacement, project closure, immutable shared mask storage, and absence of additional selection callbacks.

The existing desktop selection tests continue to validate both 2D and native napari 3D picking behavior, physical scaling, active-layer preservation, paint/erase isolation, and highlight cleanup.

- Focused Mask Review, selection, and execution tests: 24 passed.
- Desktop and scientific regression suite excluding `test_workflow.py`: 145 passed, plus 13 subtests.
- Non-crashing legacy workflow tests: 9 passed; the two known native-viewer shutdown tests were deselected and remain documented separately.

## Known limitations

- Morphology and fluorescence measurements are not yet connected and are shown as unavailable rather than calculated again.
- Manual keep/exclude decisions remain disabled until the legacy filtering, eligibility, and reviewed-ROI contracts are available.
- Split/merge, painting, erasing, deletion, smoothing, matching, stereology, density, and scientific export are outside this milestone.
- Cloud tests do not claim native OpenGL verification. The known headless napari shutdown abort remains separate from scientific assertion results.

## Native macOS verification

1. Open a project and run a small Cellpose segmentation.
2. Select **Mask Review** and confirm current status and detected-mask count.
3. Activate the raw mask Labels layer and the existing **Review / Select** tool.
4. Click different objects in 2D and confirm both Mask Review and the persistent Selected Mask summary update.
5. Click background and confirm the Mask ID clears.
6. Use **Show selected mask only**, then **Show all masks**, and verify the underlying label data does not change.
7. Repeat selection in 3D and confirm the existing highlight and Mask ID update.
8. Change a segmentation parameter and confirm the result is marked outdated but remains inspectable.
9. Rerun segmentation and confirm the old layer is replaced without duplicate masks.
10. Trigger a failed rerun and confirm the preceding valid result remains available.
11. Close the project, then open another image and confirm no selection, layer, or mask count leaks into it.
12. Confirm manual keep/exclude buttons remain disabled with the scientific-prerequisite explanation.

Native macOS behavior must be confirmed by the user before the next integration milestone.
