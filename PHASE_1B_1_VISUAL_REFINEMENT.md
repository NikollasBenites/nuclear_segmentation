# NuCytoSeg Phase 1B.1 visual refinement

Phase 1B.1 refines the independent Qt6 shell without connecting scientific
workflows. The legacy `nuclear-segmentation` launcher, analysis calculations,
configuration schemas, exports, and resume behavior are unchanged.

## Changes

- A high-DPI painter-based vector icon system replaces provisional letters and
  emoji throughout workflow navigation, viewer tools, layer management, panel
  controls, and workspace actions.
- The application header now presents NuCytoSeg branding, current workflow,
  compact layout controls, and a visibly disabled reserved Open Image action.
- Typography, toolbar dimensions, layer row heights, spacing, dark-theme
  contrast, hover states, selected states, and disabled states are clearer.
- The viewer toolbar includes Fit to View using `ViewerModel.reset_view()`. This
  changes camera framing only; it does not resample data or alter calibration.
- The grouped layer tree has distinct layer-type and group icons.
- Each layer has an explicit eye icon in the Visible column. The turquoise open
  eye means visible; the muted crossed eye means hidden. Its tooltip describes
  the action: `Hide layer` or `Show layer`.

The visibility cell writes only the corresponding public `layer.visible`
property. It does not select that layer. External visibility events update the
icon and tooltip without polling.

## Native macOS verification

Install or update the feature branch, then run:

```bash
QT_API=pyqt6 nucytoseg --demo
```

Verify the following on the Apple Silicon Mac:

1. Header text, workflow icons, toolbar icons, eye icons, layer-type icons, and
   status text are sharp and comfortably sized on the Retina display.
2. Collapse the navigation rail and confirm every workflow remains recognizable
   and has a tooltip. Expand it and confirm no text is clipped.
3. For every Image and Labels layer, click its eye icon. Only that layer changes
   visibility; the active row and active-layer status do not change.
4. Add or load another supported napari layer type when available and repeat the
   visibility check.
5. Change `layer.visible` from Python or another control and confirm the eye icon
   and Show/Hide tooltip update immediately.
6. Rename a layer with a slow double-click or F2. Confirm the name updates in
   napari and visibility remains unchanged.
7. Select an Image layer and confirm editing tools remain disabled. Select the
   demonstration Labels layer and verify Pick, Paint, and Erase still work.
8. Use Fit to View in 2D and 3D. Confirm framing resets while image proportions,
   voxel calibration, multiscale rendering, and layer data remain unchanged.
9. Exercise panel collapse, Focus Mode, Restore Previous Layout, Reset Layout,
   restart persistence, and three repeated application shutdowns.
10. Start `nuclear-segmentation` independently and confirm the legacy interface
    still launches normally.

Please report the macOS version, display scaling, Python/napari/PyQt6 versions,
any clipped text, indistinct icons, unexpected active-layer changes, and any
OpenGL or shutdown errors. Headless tests do not validate Retina rendering.
