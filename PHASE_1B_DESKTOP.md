# NuCytoSeg Phase 1B desktop shell

NuCytoSeg is the provisional display name for the independent Qt6 interface.
The Python package remains `nuclear_segmentation`, and the legacy
`nuclear-segmentation` launcher remains available. Phase 1B contains no
scientific workflow migration and does not read or write analysis exports.

## Install on macOS Apple Silicon

Use Python 3.12 in a fresh virtual environment from the repository root:

```bash
python3.12 -m venv .venv-nucytoseg
source .venv-nucytoseg/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-embedding-smoke.txt
```

The reproducible smoke-test requirements select napari 0.10.0, PyQt6 6.11.0,
and qtpy 2.4.3. Cellpose is not required for this interface-only milestone.

## Start the interface

Start with calibrated synthetic layers for native viewer verification:

```bash
QT_API=pyqt6 nucytoseg --demo
```

Start an empty shell without synthetic data:

```bash
QT_API=pyqt6 nucytoseg
```

The existing application remains independently available:

```bash
QT_API=pyqt6 nuclear-segmentation
```

## Architecture

- `desktop.py` owns opt-in application startup and Qt backend validation.
- `desktop_window.py` composes the application-owned `QMainWindow`, menus,
  status information, splitter layout, Focus Mode, and shutdown sequence.
- `napari_embedding.py` retains sole ownership of `ViewerModel` and `QtViewer`.
- `workflow_navigation.py` provides the collapsible workflow rail.
- `context_panel.py` provides non-functional Phase 1B workflow placeholders.
- `viewer_toolbar.py` controls the selected layer through direct supported APIs.
- `layer_panel.py` adapts public layer-list events to a grouped `QTreeView`.
- `workspace_settings.py` stores versioned layout preferences in `QSettings`.

The `ViewerModel` is created once per window. Changing workflows changes only
the contextual page; it does not reconstruct the viewer or reload layer data.
Workspace preferences are separate from `analysis_config.json` and all analysis
exports.

## Workspace behavior

- Collapse the workflow navigation to an icon rail with its bottom button.
- Hide or restore contextual parameters from its close button or the View menu.
- Toggle Focus Mode with `Ctrl+Shift+F`.
- Restore the previous saved layout or reset defaults from the Workspace menu.
- Window geometry, splitter sizes, visibility, collapsed navigation state, and
  selected workflow are saved when the normal workspace closes.
- Focus Mode is temporary and is never saved over the normal arrangement.
- Invalid or off-screen saved geometry recovers onto an available display.

## Native macOS verification checklist

Run `QT_API=pyqt6 nucytoseg --demo`, then verify:

1. Only the NuCytoSeg window opens; no separate napari application window opens.
2. All eight workflows select correctly and change the right placeholder page
   without changing or reloading the viewer layers.
3. Collapse the workflow sidebar. Its icon rail remains usable and every icon
   has a tooltip. Expand it again and confirm labels are not clipped.
4. Hide and restore the contextual panel. Resize both splitter boundaries at a
   normal laptop resolution and with the window maximized.
5. Select Image and Labels rows in the layer tree. Confirm selection, visibility,
   rename, layer addition/removal, and externally triggered ordering changes are
   reflected without duplicated rows or tracebacks.
6. With an Image layer selected, confirm Paint, Erase, and Pick are disabled.
   With the demonstration Labels layer selected, verify Pick, Paint, and Erase.
7. Verify Z navigation, 2D/3D switching, camera interaction, physical scaling,
   multiple channels, and the multiscale demonstration layer.
8. Enter Focus Mode. Confirm the icon rail remains, contextual parameters hide,
   and the viewer toolbar remains usable. Exit and confirm the prior sizes and
   visibility return exactly.
9. Close and reopen the application to confirm layout persistence. Exercise
   Restore Previous Layout and Reset Layout. If available, test after disconnecting
   a secondary display to confirm geometry recovery.
10. Repeat launch, 3D interaction, and shutdown three times. Confirm there are no
    abort messages, lingering Python processes, or unexpected napari windows.
11. Launch `nuclear-segmentation` separately and confirm the legacy application
    still starts normally.

Report the macOS version, Python version, napari/PyQt6/qtpy versions, checklist
failures, and any screenshots showing clipping or rendering defects.

## Automated tests

Headless checks can be repeated with:

```bash
QT_API=pyqt6 QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_napari_embedding.py tests/test_desktop_shell.py -q
```

Headless Qt tests verify ownership, synchronization, persistence, and lifecycle.
They do not establish native OpenGL correctness.

## Phase 1B limitations

- Workflow panels are intentional placeholders and execute no analysis.
- The layer tree groups layers for presentation only. Groups do not change data
  or imply biological identity; generic `mask_type` metadata may describe
  `nuclear` or `cytoplasmic` roles in future phases.
- Routine native napari window menus and action-manager commands are not exposed.
- No final logo, installer, macOS application bundle, or permanent package rename
  is included.
- Scientific workflow integration begins only after Phase 2 approval.
