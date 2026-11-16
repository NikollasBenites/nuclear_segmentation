# NuCytoSeg Phase 1B.3 unified mask selection

Phase 1B.3 extends the existing Review tool to 2D and 3D and moves selection
information into a persistent panel below Layers. It does not implement mask
editing, measurements, or scientific workflow integration.

## Picking policy

Review forwards each click to napari 0.10's public `Labels.get_value` API with
the event's world position, view direction, and displayed dimensions. In 3D,
napari transforms that ray into layer data coordinates, intersects it with the
volume bounds, and returns the first non-background label encountered from the
camera-facing intersection. This is NuCytoSeg's documented deterministic policy:
the nearest nonzero object on the active Labels layer's picking ray.

The event ray is calculated by napari from the actual canvas transform and click
position. It therefore changes with camera rotation and, for perspective views,
with screen position. Picking respects layer transforms and anisotropic scale.
Hidden, removed, inactive, non-Labels, and background data are ignored safely.

This policy describes geometry within the active Labels layer. It does not claim
to reproduce visual occlusion across blended Image or other Labels layers.

## Highlighting

The selected object is shown using an internal cropped Labels overlay:

- The original Labels array, IDs, color map, selected editing label, and display
  properties remain untouched.
- Other masks stay visible and available for consecutive Review clicks.
- Only the selected object's bounding box is copied; the full volume is not.
- The overlay transform is derived from public `data_to_world` coordinates, so
  it remains aligned under anisotropic scale and layer transforms.
- It uses a turquoise contour in 2D and a translucent turquoise volume in 3D.
- It is excluded from the custom layer tree, cannot become a Review target, and
  is removed on clear, tool/layer changes, hiding/removal, and shutdown.

## Right sidebar

The persistent right-sidebar order is Parameters, Layers, then Selected Mask.
The compact Selected Mask panel is independent of workflow pages and reports the
Mask ID and source Labels layer, with an explicit empty state.

## Native macOS acceptance checklist

```bash
git fetch origin feature/nucytoseg-qt6-shell
git switch feature/nucytoseg-qt6-shell
git pull --ff-only
QT_API=pyqt6 nucytoseg --demo
```

1. Confirm Selected Mask appears directly below Layers and remains there while
   switching every workflow and entering/exiting Focus Mode.
2. In 2D Review, click several masks and background. Confirm IDs, highlighting,
   clearing, Z persistence, and unchanged mask data.
3. Switch to 3D with Review still active. Click visible objects consecutively and
   confirm the nearest object on the cursor ray is selected.
4. Rotate the camera substantially and repeat selection from several sides.
5. Test both orthographic (`perspective = 0`) and a modest perspective setting.
   Confirm clicks near the center and edges select the intended visible object.
6. Switch repeatedly between 2D and 3D. Confirm selection identity persists,
   2D Z navigation remains functional, and the highlight changes presentation.
7. Hide and restore the active Labels layer, rename it, then remove it. Confirm
   the highlight and Selected Mask panel update without stale callbacks.
8. Select an Image layer and confirm Review cannot select from it.
9. Switch to Paint and Erase. Confirm Review highlighting disappears, editing
   works normally, and original mask IDs/data remain valid.
10. Close and relaunch three times without crashes or lingering highlight layers.

## Remaining native validation boundary

Headless tests validate ray geometry, nearest-hit ordering, diagonal camera rays,
anisotropic spacing, transform alignment, lifecycle, and data immutability. They
cannot validate which translucent object appears visually foremost in OpenGL or
confirm all GPU/driver-specific perspective rendering behavior. Those items are
part of the native checklist above.
