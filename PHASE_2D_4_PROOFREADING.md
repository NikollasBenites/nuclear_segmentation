# Phase 2D.4.4.2 proofreading

## Working with masks

Filter the active session, then open **Proofreading → Start Editing**. Live filtered masks shows the retained objects. Start Editing opens the existing protected preview. Save Changes validates the transaction and immediately updates the same working layer. Another Start Editing continues with the accepted objects, including split children. Pause, Resume, Stop, the selection tools and the calibrated brush operate on that preview as before.

The Layers panel reports the working result's scientific status. **Automatically recalculate measurements after saving edits** is a desktop preference, initially off. Use **Recalculate Measurements** after a batch of corrections, or enable the preference to run the existing measurement worker after each save. A failed calculation reports the error and offers a retry. Each nuclear geometry change requires another ROI review before final quantification and export.

**Advanced Layers & History** provides the complete committed geometry, the retained view immediately before the latest saved edit when available, a revision history and undo. Returning to the working view releases hidden comparisons. With no retained selection, the editor explains the all-mask scope. Saving in this scope can display the complete correction as a labeled diagnostic until a retained view is available.

### Quick Undo and Redo

During an edit, use **Undo** and **Redo** beside Paint and Erase. On macOS, press **⌘Z** or **⌘⇧Z**. On Windows and Linux, press **Ctrl+Z** to undo and **Ctrl+Y** or **Ctrl+Shift+Z** to redo. Text and numeric fields keep their normal keyboard behavior.

Each brush stroke is one step. Remove accepts the whole-object removal immediately. For Split and Merge, **Apply Operation** accepts the proposal and **Cancel Operation** restores the preview at the start of that operation. After Apply, the complete structural operation is one Undo step. During a pending Split, Undo and Redo work on its individual strokes. **Escape** leaves Paint or Erase for Review / Select, including during Split. Erase restores child voxels to their parent.

Pause, switching slice views, and collapsing Geometry Editing preserve the transaction. A successful scope change starts a fresh undo range to protect the newly hidden labels. Earlier accepted operations remain in the transaction. A new accepted edit clears the redo branch. Save requires all pending proposals to be applied or canceled. Stop can discard a transaction with no net voxel changes.

Outside a transaction, Undo in Proofreading or Mask Review invokes **Undo Last Saved Edit** for the active session. One saved revision covers the entire batch, including any objects created and later removed within it.

## Phase 2D.4.4.2 implementation and verification

**Navigation.** Project → Preprocessing → Segmentation → Mask Review → Proofreading → Matching → Quantification → Quality Control → Export. Mask Review contains Filtering and ROI & Counting. Proofreading hosts the existing selection, geometry editing, visualization and object-inspection widgets. Previous desktop layouts that opened Mask Editing reopen in Proofreading. Context-menu properties and Start Editing route to the same panel and viewer.

**Split reproduction.** On baseline `73d9bb7`, a 3-pixel brush centered on the corner of parent 1 next to object 2 wrote child voxels over IDs 0, 1 and 2. Save rejected that stroke with “Paint only part of the parent with the proposed new ID.” The baseline also evaluated the structural operation against the transaction-wide delta. Each proposal now captures its own starting preview. Both mask-based and indexed napari writes intersect the proposal with the captured parent before writing data or recording stroke history. Apply checks that the parent and child each retain voxels and that their union equals the original parent. Applying a Split or Merge clears its parameters and merge selection and returns to Review / Select.

**Geometry and provenance.** One mutable preview holds all corrections. Ordered sparse entries record the affected flat voxel indices, old and new labels, operation, parameters, scope and affected IDs. Ordinary strokes use napari's grouped paint events and native undo queues where available. Across operation boundaries the same strokes replay from their sparse entries. Save verifies the original session revision and retained selection, replays the complete sequence on authoritative geometry, checks scope and compares the result with the displayed preview. Only a successful save publishes one revision and invalidates dependent scientific results. All-mask membership is propagated in order. An ambiguous transfer or merge requires recalculation. Advanced Layers & History lists the operations within each saved batch.

**Checkpoint and export.** Native v2 single-operation records keep their existing representation. Opening a checkpoint with mixed history requires a build that understands the transaction record. A mixed record adds the ordered steps to the net transaction delta. A child that is created and then merged away still appears in provenance and remains reserved for ID allocation. Loading verifies intermediate geometry, final geometry, affected IDs and retained membership. Undo Last Saved Edit uses the existing net delta. Scientific export reads current data-model results once recalculation and any required nuclear ROI review are complete.

**Memory and limitations.** Storage for the accepted journal and redo branch scales with the sum of changed voxels across steps. Overlapping strokes can record the same voxel more than once. A pending Split or Merge temporarily holds one additional full preview for validation and cancellation. Apply or Cancel releases it. Save uses a full replay buffer which becomes the committed geometry. Structural validation, save, and checkpoint verification can take longer on large stacks. Split clipping uses the napari 0.10 Labels write path. Recheck the real canvas after changing napari versions. Native v2 checkpoints save committed work. The live preview and its Undo/Redo branch last only for the open transaction. Split remains manual.

**Automated regressions.** On Linux, 170 tests passed across geometry editing, context menus, desktop navigation, mask review, filtering, native checkpoints, quantification and export, and napari embedding. The real Qt/napari canvas ran under Xorg with software OpenGL. The suite exercised provisional export rejection, recalculated raw and filtered TIFFs, checkpoint reload on both sides of recalculation, and whole-transaction saved Undo.

### Files changed in Phase 2D.4.4.2

| Area | Files |
| --- | --- |
| Navigation, inspection and layouts | `workflow_navigation.py`, `context_panel.py`, `desktop_window.py`, `mask_review_panel.py`, `mask_context_menu.py`, `workspace_settings.py` |
| Editing, stroke integration and saved history | `geometry_edit.py`, `geometry_panel.py`, `preview_history.py` |
| Regression coverage | `test_geometry_edit.py`, `test_desktop_shell.py`, `test_mask_context_menu.py`, `test_mask_review_gui.py`, `test_quantification_gui.py` |
| Workflow and native validation | `PHASE_2D_4_PROOFREADING.md` |

### Native macOS validation checklist

- [ ] Restore a saved Mask Editing layout and verify the Proofreading page, panel widths, collapse states and session selection. Open object properties from Segmentation and Start Editing from Filtering.
- [ ] On a retained object beside another object, paint across its edge in XY and an orthogonal slice. Erase part of the child, consume the whole parent, correct the proposal and apply it. Inspect the neighbor, background and hidden labels.
- [ ] Paint, remove, merge, split and paint a new child in one transaction. Cancel a second proposal. Use ⌘Z and ⌘⇧Z across the applied operations while paused and with the panel collapsed. Check Escape in Paint, Erase, a pending Split and a text field.
- [ ] Save once with automatic recalculation off. Inspect the single revision and its steps, reopen the checkpoint and undo the saved batch. Repeat in a cytoplasmic session and confirm the nuclear session retains its geometry and settings.
- [ ] Enable automatic recalculation and save another batch. Verify one measurement run, the retained layer appearance and export gating. Review the nuclear ROI where required, then compare the filtered TIFF, raw TIFF and retained-object table.

## State ownership and scientific integrity

| Record | Owner and lifetime |
| --- | --- |
| Original Cellpose labels | The segmentation result keeps the read-only original ZYX array. The raw comparison layer references this array. |
| Complete geometry | The existing geometry transaction validates the preview, restores hidden objects and publishes the committed array and voxel delta. Undo creates a new revision. |
| Provisional retained IDs | The latest edit record stores the accepted selection. The session's pending geometry revision determines when that selection can seed proofreading. An empty list represents a known empty selection. An absent selection requires reconciliation by measurement. |
| Validated filtering | The measurement worker reads complete effective geometry. The filtering result holds its properties, retained IDs, settings and mask array. Compatible thresholds run against every surviving object. Excluded objects can qualify again. |
| Scientific status | The session stores its pending revision and any measurement error. The controller derives running status from the worker owner and measurement revision. Successful compatible publication clears the pending state. |
| Layer identity | The filtering controller owns one napari Labels object for the active session. It updates its data, revision metadata and read-only setting in place. The display controller retains opacity, visibility and selection. Session changes rebuild views from the selected session's records. |
| Checkpoint | Native v2 stores full geometry, history, pending controls, scientific results and session display preferences. Loading an earlier v2 checkpoint maps its pending revision and retained-layer preferences to the working view. A legacy filtered edit's before-selection can be recovered from its reviewed, new and removed IDs. |

A saved all-mask removal drops vanished IDs from the prior selection. A split inherits its parent's membership. A merge of retained and excluded objects, a new freehand ID, or a brush transfer across those groups leaves the selection unresolved. Recalculate to establish membership in those cases. History also records the pre-edit IDs so undo can restore the appropriate proofreading scope.

The measurement path reuses applied measurement-channel pixels to avoid repeating image preprocessing. For a checkpoint carrying correction reports, the first measurement can materialize those pixels from the verified source grid and saved per-plane gains, using the original float32 multiplication. Subsequent measurements share the read-only array. Segmentation-only correction leaves measurement intensities in their original domain. Regression tests compare both routes against the existing scientific input preparation.

Scientific export reads the model's current filtering and quantification records. Its filtered TIFF uses the validated mask array, its retained table uses the validated IDs, and its all-object table describes complete committed geometry. The raw TIFF uses the original labels. The nuclear density gate additionally requires a reviewed ROI and eligible counting results. Tests exercise export while a historical comparison is visible, rejection after a save, and successful TIFF and CSV export after recalculation and ROI review. Cytoplasmic sessions retain their existing measurement and export capabilities.

## Memory and limits

A filtered edit still needs its writable preview and a complete validated commit. A provisional display with excluded labels needs one ZYX working array and plane-sized temporary masks. When every label is retained, the view shares committed geometry. Complete comparison shares the committed array. Opening a historical comparison materializes one ZYX volume from the latest delta. The checkpoint stores IDs for these projections. Voxel history still grows with the number of changed voxels. A measurement-routed corrected channel can retain one float32 ZYX array until its correction or project is invalidated.

## Phase 2D.4.4 baseline verification

The complete Linux regression run covered 302 distinct pytest tests and 13 scientific subtests using Python 3.12.14, Qt 6.11.0 with PyQt6, and napari 0.10.0. The rendered 2D/3D canvas test ran under Xorg with software OpenGL.

| Test group | Result |
| --- | --- |
| Geometry editing, mask review and context menu, native checkpoints, scientific inputs and quantification/export | 116 passed |
| Remaining modules outside live plots and legacy workflow | 171 passed, plus 13 subtests |
| Live plots | 4 passed |
| Legacy workflow, including launcher preview | 10 passed together and 1 passed separately |

A combined run aborted in the legacy launcher preview while processing Qt events. Combined runs also exposed timing-sensitive first renders in the live-plot tests. The final groups ran the live-plot module and the legacy launcher preview in fresh processes with their existing assertions. Native macOS validation of this phase is pending.

## Phase 2D.4.4 native macOS validation checklist

Use a duplicate calibrated project with nonzero crop offset, anisotropic spacing, and nuclear and cytoplasmic sessions containing overlapping numeric IDs.

- [ ] Before filtering, check that only raw segmentation is listed. Filter out a known object. Verify raw is hidden, the retained layer is visible, and ROI appears with the overlays. Record the thresholds, layer order and opacity.
- [ ] With automatic recalculation off, paint, erase, split, merge and remove across successive saves. Check IDs, counts, layer order, opacity, selected object and pending status. Attempt to paint a hidden object or use its ID. Compare complete and raw geometry in 2D and 3D.
- [ ] Pause and resume in a different slice plane. Verify pixel and µm brush readouts, Stop dialog choices and cleanup after discard. Undo twice and verify that subsequent splits allocate unused IDs.
- [ ] Save before recalculation, quit and reopen. Check the corrected working view, status, settings, history and blocked export. Repeat with a baseline v2 checkpoint and a legacy import.
- [ ] Recalculate and review the nuclear ROI. Compare retained IDs, measured volumes, intensities, counting eligibility, density, CSV rows and raw and filtered TIFFs with the expected geometry. Save and reopen the current result.
- [ ] Enable automatic recalculation, save another correction, and watch the transition through running to current. Verify an intentional mixed-membership all-mask merge requires recalculation. Compare the prior retained view and return to the working layer.
- [ ] Repeat in the cytoplasmic session. Verify independent edits, thresholds and display choices, then return to Nuclear and confirm its ROI and density. Switch sessions, close projects and quit the native viewer while idle.

## Files changed in Phase 2D.4.4

Under `src/nuclear_segmentation/`, geometry and persistence changes are in `geometry_edit.py`, `project_state.py` and `native_project.py`. Measurement routing and worker handling are in `filtering_workspace.py`, `scientific_input.py` and `z_correction_workspace.py`. Desktop and display changes are in `desktop_window.py`, `geometry_panel.py`, `layer_panel.py`, `mask_context_menu.py`, `mask_review_panel.py`, `segmentation_execution.py` and `session_display.py`.

Regression changes are in `tests/test_geometry_edit.py`, `tests/test_mask_review_gui.py`, `tests/test_native_project.py`, `tests/test_quantification_gui.py` and `tests/test_scientific_input.py`. This document contains the workflow and native validation procedure.

---

## Earlier phases — native macOS validation

Use a duplicate of a validated nuclear project and a calibrated multichannel image with a cytoplasmic session. Include a cropped image with nonzero XY offset, anisotropic spacing, and at least two objects per session. Record the commit, macOS, Qt and napari versions, image and series, session names, raw mask signatures, spacing, offset, filter settings and nuclear ROI. Save the starting project in a separate folder for comparison.

1. **Project navigation.** Launch the Qt6 desktop. Confirm Project offers image loading, session setup and Save & Resume. Verify Analysis Mode is absent. Navigate to Mask Review → Mask Editing. Verify Start Editing becomes available with a current segmentation.
2. **Calibrated slice editing.** Select a known label, start editing, and use Paint and Erase on a single slice. Check alignment with the source image and adjacent Z slices. Switch to 3D, rotate the preview, return to 2D and resume painting. Discard. Compare raw voxels, committed geometry, measurements, ROI, density and session revision with the starting project.
3. **Commit and invalidation.** Repeat a small, countable brush edit. Commit. Verify the committed layer displays the edit and Show Raw Masks restores the original image geometry. Check the raw and committed layers are read-only. Check that the old measurements, retained IDs and density are unavailable. A same-grid nuclear ROI should be available again after recalculation and require review.
4. **Merge and split.** In a fresh preview, click two or more objects with nonconsecutive IDs, using Add Selected Object to Merge after each click. Explicitly choose the target, inspect the preview through Z and commit. Verify the target occupies exactly the union of its inputs, with background and all other IDs unchanged. Start a split on that target and paint part of its voxels with the proposed child ID. Try including a background voxel and verify Commit reports the error. Correct or discard and repeat. Accept a valid split and check that both labels are nonempty and together reproduce the parent.
5. **Removal and undo.** Remove a selected object and check its full Z extent disappears only after commit in the authoritative geometry. Undo successive commits and compare voxels, IDs and revision changes after each undo. Remove the last object in a small session and verify recalculation reports zero objects.
6. **Scientific comparison.** For one edited nucleus, independently count its voxels and multiply by Z × Y × X spacing for volume. Compare its Z bounds, shape and mean measured-channel intensity with the committed voxel region. Change a filter threshold and verify retained IDs and counts. Review and accept the nuclear ROI and recalculate density. Compare counting eligibility and retained count divided by the effective ROI volume with the reference calculation for the same counting settings.
7. **Session independence.** In the cytoplasmic session repeat a geometry edit on an ID also present in Nuclear. Verify descriptive volume, shape, channel intensity and filtered IDs. Confirm cytoplasmic density is unavailable. Switch back and check nuclear geometry, display opacity, selected-only display, review status and density. Repeat the switch in 3D.
8. **Conflicts and failed edits.** During a preview try Save, Open, Close, session switching and another scientific calculation. Finish or discard the preview to resume those operations. Try starting an edit during measurement or project loading. Try committing an unchanged preview. Verify failure leaves the prior geometry and project file intact.
9. **Checkpoint and history.** Save with the edited session active, quit fully, then reopen the v2 project. Verify active session, committed and raw geometry, display settings, revision, measurement status and undo. Compare before/after signatures, timestamp, session, IDs and changed voxels for a known merge. Undo after reopening and compare with the prior committed state. Reopen a Phase 2D.2, 2D.3, 2D.3.1 and v1 checkpoint plus a legacy nuclear import. Check masks, ROI and scientific records against the corresponding reference prior to editing.
10. **Native rendering and shutdown.** With the real napari canvas, repeat raw, retained, committed and selected-only display in 2D and 3D. Check that repeated start/discard, commit, session switches and project reopening remove obsolete layers. Quit and relaunch. Record any rendering artifacts, crashes or discrepancies with the session and revision where they occurred.

## Phase 2D.4.1 interaction checks on macOS

- [ ] Clear the current selection, start editing and click background. Selected Object ID should show None. Click two different masks on a calibrated slice. Check the highlight, Selected Object ID and Brush Label ID after each click.
- [ ] Collapse and expand Layers. Select an image and adjust its opacity. Show and inspect raw masks, then use Return to Editing Preview. Verify the preview becomes visible and active and retains its object selection. Confirm Paint and Erase are unavailable on the comparison layers.
- [ ] Switch between Review / Select and Paint on the preview. Paint one voxel and discard. Confirm the original and committed geometry are intact. Repeat in 3D, rotate the view and click a visible surface. Paint and Erase should be unavailable until returning to 2D.
- [ ] Add two nonconsecutive IDs to the merge list by clicking each object. Add a duplicate, remove an entry, clear the list and rebuild it. Choose the target explicitly. Check Preview Merge, Commit and Undo against the original voxel union.
- [ ] Click a parent and choose Split Selected Object. Verify Selected Object ID stays on the parent and Brush Label ID changes to the proposed child. Paint part of the parent, inspect in 3D, return to 2D and commit. Repeat once with Discard.
- [ ] Switch to a second session that uses the same numeric IDs. Check its raw, committed, filtered and selected-only displays. Save, quit and resume. Verify geometry, reviewed measurements and undo in each session. Start and discard another preview and verify each click produces a single selection and highlight.

### Secondary-click menu on macOS

- [ ] With a visible raw, filtered or committed Labels layer active, use Pan or Review / Select. Secondary-click a known object on an offset, anisotropic slice. Verify the layer and ID in the menu. Dismiss it and check that selection, brush and voxels are unchanged.
- [ ] Try Select Mask, Show Selected Only and Show Mask Properties. Compare the ID and voxel count with the clicked layer. Use Start Editing and verify the same object is selected in the effective preview. A raw ID previously removed from committed geometry should be available for inspection only.
- [ ] In a fresh preview, secondary-click different objects to add them to the merge list. Test Split and Remove separately, inspecting the preview before Commit or Discard. Verify Undo restores committed geometry. After a brush stroke, confirm the menu omits whole-object operations until the transaction finishes.
- [ ] Test background clicks, modified clicks, secondary-button drags, paint and erase. Verify native gestures continue without opening the menu. Rotate a 3D volume with two masks along one ray and check the nearest ID from both directions. Repeat after switching sessions and reopening the project.

Record pass/fail and observations per step. Native macOS sign-off is pending.

## Phase 2D.4.2 native macOS sign-off

Use the same calibrated image and reference measurements as above. Record the commit, macOS, PyQt6 and napari versions and the session for each check.

- [ ] Narrow the context dock, expand Geometry Editing and scroll to Preview Merge. Add two IDs and choose a target. Collapse both sections, change a comparison layer's opacity and reopen the sections. Verify the preview, selection, brush, merge IDs and target. Check the collapsed status after a brush stroke, split and merge.
- [ ] Filter out one known object while retaining another. Select the retained object in Live Filtered Masks and start editing. Check the selected ID, brush ID and Edit Filtered Masks indicator. Use Edit All Masks to inspect the excluded object's position, then switch back.
- [ ] Paint across that hidden position. Attempt Commit and switch to Edit All Masks. Verify both report the conflict and preserve the preview. Undo the stroke or discard. Repeat with a valid stroke over true background. Toggle scope twice, commit and verify that the excluded object's complete voxel set is identical to the reference. Check the original Cellpose masks and the history's affected IDs.
- [ ] In Edit All Masks, erase part of an excluded object. Verify a switch to filtered scope reports the pending change. Commit intentionally, recalculate measurements and filtering, then compare volumes and nuclear counts using the procedure above. Repeat in the cytoplasmic session and check the nuclear session's results.
- [ ] Select a committed object and start editing again. Inspect the all-mask fallback after invalidation and the filtered default after recalculation. Repeat with zero retained objects and with a previously removed object selected in raw masks.
- [ ] With a trackpad secondary click, verify the biological role and ID on raw, filtered, committed and preview layers. Compare Filtering, ROI & Counting, Mask Editing, Segmentation and Project. Check that brush strokes, camera drags, split and merge previews expose only applicable actions. Repeat the two-direction 3D ray check above.
- [ ] Save and quit after a filtered edit, reopen the v2 checkpoint, and compare excluded voxels, geometry signatures and Undo against the initial reference. Repeat the earlier legacy checkpoint checks and record any differences.

### Phase 2D.5 naming plan

Inventory legacy marker-specific matching entry points and their callers. Introduce role-based cytoplasmic split and nuclear–cytoplasmic matching module names behind compatibility imports. Verify matching outputs against existing fixtures and retain acquisition channel names and persisted scientific fields throughout the migration.

## Phase 2D.4.3 native macOS sign-off

Record macOS and Apple Silicon model, Python, PyQt6 and napari versions, acquisition, session, crop offset and calibrated ZYX spacing. Use a duplicate checkpoint with one known excluded object and at least two retained objects. Record observations and the expected and actual IDs for each check.

- [ ] Open Mask Editing with no transaction. Check Start Editing and available undo history. Start from a selected retained object, then repeat with no selection. Narrow the dock and collapse both editor sections during an active edit.
- [ ] Switch Paint, Erase and Review / Select from both toolbars. Set the brush to 1 and 3 px. Use a native napari brush shortcut, then verify the numeric input, slider and actual stroke. For XY spacing of 0.56818 µm/pixel, 3 px should read 1.70 × 1.70 µm. Rotate the slice order to XZ and YZ on anisotropic data, verify both nominal dimensions and inspect which slice changed. Check that 3D rotation leaves painting disabled.
- [ ] Begin a split, paint part of the parent with the child, then pause. Inspect raw and committed comparison layers using Layers. Try Paint, Erase, session switching, calculations, Save Project and project close. Resume and verify the original preview, parent and child IDs, brush size, slice edits and scope. Repeat with a prepared merge and explicit target.
- [ ] Stop an unchanged preview and check the revision. For a changed preview, try Cancel, Discard Changes and Save Changes in separate trials while editing and while paused. Try saving a stroke across an excluded object. Check the conflict, retained preview and unchanged revision. Save Changes applies valid edits in memory. Verify the project still indicates unsaved changes until Save Project succeeds.
- [ ] In filtered scope, save a split, merge, removal and brush edit. Record the retained and excluded IDs before each operation. Compare Committed — Retained with the reviewed objects and their descendants. Toggle Committed — All and inspect every excluded object's voxels. Verify Raw Cellpose Masks against the original and repeat an edit in all-mask scope.
- [ ] Save Project before recalculation, quit and reopen. Verify retained IDs, provisional status, opacity, authoritative geometry, session history and Undo. Switch Nuclear and Cytoplasmic sessions with overlapping numeric IDs and verify independent display and measurement state.
- [ ] Recalculate measurements with a known intensity threshold, volume cutoff, marker setting and nuclear guard zone. Check the reapplied thresholds even if the new observed intensity range is narrower. Inspect the Live Filtered Masks, re-review the nuclear ROI and compare density with the reference calculation above. Verify the provisional layer disappears and cytoplasmic density remains unavailable.
- [ ] Repeat the v2, v1 and legacy opening checks above, including a Phase 2D.4.2 checkpoint. Repeat editing cycles in the real viewer, secondary-click objects and inspect for obsolete layers or duplicate callbacks.

The brush readout reports nominal dimensions along the displayed axes. Pixel rounding and napari's rasterization determine the actual footprint. Paused previews live in memory until Save Changes or Discard Changes. The checkpoint stores complete committed geometry, edit history and the provisional retained IDs. Scientific measurements always use complete committed geometry.

### Phase 2D.4.3.1 comparison layers on native macOS

- [ ] In Nuclear and Cytoplasmic sessions with overlapping IDs, retain one known object and exclude another. Record the filtered view in each session.
- [ ] Save a filtered brush correction, split, merge or removal. Confirm only **Committed — Retained (Provisional)** is visible among the mask layers. Inspect **Filtered — Before Editing (Outdated)**, **Committed — All (Diagnostic)** and Raw Cellpose masks separately. Compare the input, accepted correction, excluded object and original voxels.
- [ ] Switch sessions, then save and reopen with the outdated comparison visible. Verify the session, display choice and object coordinates. Undo a saved edit and check that scientific filtering still requires recalculation.
- [ ] Repeat in both editing directions. Recalculate successfully and verify Live Filtered Masks replaces both comparison layers. Check retained IDs, nuclear ROI review and density availability in the correct session.
