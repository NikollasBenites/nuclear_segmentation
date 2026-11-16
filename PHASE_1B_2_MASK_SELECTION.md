# NuCytoSeg Phase 1B.2 mask selection

> Phase 1B.3 supersedes the highlight implementation and extends Review picking
> to 3D. See `PHASE_1B_3_UNIFIED_SELECTION.md` for current behavior.

Phase 1B.2 adds non-destructive 2D mask selection to the independent Qt6
interface. It does not implement geometry editing, measurements, or scientific
workflow integration.

## Behavior

- Empty layer groups are hidden. The former `Review Layers` presentation group
  is now `Annotations & Overlays`.
- Review / Select is an application-owned tool for the explicitly active Labels
  layer. It does not invoke napari's global action manager.
- Clicking a displayed label selects its Mask ID. Clicking background clears it.
- The contextual panel reports the selected Mask ID and Labels layer.
- Selection is stored separately for each Labels layer and persists across Z
  navigation and active-layer changes.
- The highlight uses napari's public selected-label display and contour
  properties. Prior selected-label, contour, and display-filter values are
  restored when Review mode is left, the active layer changes, selection is
  cleared, or the application closes.
- Paint, Erase, Pick, and Pan/Zoom do not execute the Review callback.
- No mask data, spatial scale, color map, scientific result, or export is
  modified.

## Native macOS verification

Update the feature branch and launch the calibrated demonstration:

```bash
git fetch origin feature/nucytoseg-qt6-shell
git switch feature/nucytoseg-qt6-shell
git pull --ff-only
QT_API=pyqt6 nucytoseg --demo
```

Verify in 2D:

1. `Annotations & Overlays` is absent when it contains no layers. Add a Points
   or Shapes layer if convenient and confirm the group appears without changing
   underlying layer data.
2. Select `Editable demonstration labels`, activate Review / Select, and click
   several different objects. Confirm the contextual Mask ID changes correctly.
3. Confirm only the selected object is emphasized with a clear contour and the
   mask array and original color mapping remain unchanged.
4. Click background. Confirm Mask ID clears and the original Labels display is
   restored.
5. Select an object, move through Z planes, and return. Confirm its Mask ID
   remains selected and its contour is shown wherever that object intersects
   the current plane.
6. Add or load a second Labels layer. Switch active layers, select different IDs,
   and confirm each layer retains its independent application selection.
7. Switch from Review to Paint and Erase. Confirm the Review callback and contour
   are disabled, normal editing works, and clicking does not change Mask ID.
8. Return to Review and confirm the stored Mask ID and highlight return.
9. Repeat launch and shutdown three times and confirm no lingering callbacks,
   abort messages, or Python processes.
10. Start `nuclear-segmentation` and confirm the legacy interface remains intact.

## 3D limitation

Review picking is deliberately restricted to 2D. A click while the viewer is in
3D does not change mask selection. Native 3D ray-based object picking has not
been validated and is not claimed in Phase 1B.2.
