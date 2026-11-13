# NuCytoSeg napari 0.10 embedding smoke test

This Phase 1 proof of concept embeds napari's public `QtViewer` inside an
application-owned Qt6 `QMainWindow`. It uses synthetic data and does not start,
modify, or export a scientific analysis.

## napari 0.10 embedding compatibility

The native napari layer-list context menu and native Labels mode buttons depend
on objects supplied by napari's own main-window dependency providers. A bare
`QtViewer(ViewerModel)` intentionally has no native napari main window, so those
controls are not safe in the independent host.

The smoke test therefore uses an application-owned layer list and explicitly
bound Pan, Pick, Paint, and Erase controls. Right-clicking the owned layer list
does not open a context menu in Phase 1A. Visibility toggles and active-layer
selection remain available. Pick directly sets the owned Labels layer to
napari's public Pick mode; it does not invoke the global action manager.

## macOS Apple Silicon requirements

- macOS 13 or later on Apple Silicon
- Python 3.12, installed as an arm64 build
- A terminal launched in a normal logged-in desktop session
- napari 0.10.0, PyQt6 6.11.0, and QtPy 2.4.3

From the repository root, create a clean environment and install the pinned
embedding stack:

```bash
python3.12 -m venv .venv-nucytoseg-smoke
source .venv-nucytoseg-smoke/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-embedding-smoke.txt
python -c "import platform, napari, qtpy; print(platform.machine(), napari.__version__, qtpy.API_NAME)"
```

The final command must print `arm64 0.10.0 PyQt6`.

## Run

```bash
QT_API=pyqt6 python -m nuclear_segmentation.embedding_smoke_test
```

## Expected window and interaction checklist

The application must open one window titled
`NuCytoSeg — napari 0.10 embedding smoke test`. It should show cyan nuclear
signal, magenta cytoplasmic signal, colored demonstration labels, a Z slider,
and NuCytoSeg-owned layer and label controls on the right.

Verify all of the following:

1. Move the Z slider and scroll over the canvas. The visible optical plane changes.
2. Toggle the two synthetic fluorescence layers. Cyan and magenta channels can
   be shown independently and together.
3. Right-click each layer-list row. No menu should open, no traceback should be
   printed, and the process must remain responsive. Left-click selection and
   visibility checkboxes must continue to work.
4. Select `Editable demonstration labels`, click `Pick`, and click each existing
   colored object. The `Selected label` value must change to the clicked object.
5. Click `Paint` and draw on several Z
   planes. Click `Erase` and remove part of the new label. Existing synthetic
   objects and the new paint must remain aligned with the images. Clicking or
   drawing on this layer must also update the status bar with
   `Labels mouse callback: ZYX ...`; this confirms custom callback dispatch.
6. Change `Selected label` and confirm newly painted
   voxels use that label.
7. Click `3D`. The calibrated volume renders in 3D and can be rotated and zoomed.
   Click `2D` to return to slice view.
8. Make `Multiscale demonstration` visible and confirm it renders without an error.
   This layer is a visualization check and is intentionally not editable.
9. Resize the main window and the viewer-tools panel. The canvas must continue to
   receive mouse and keyboard input.
10. Close the window while in 3D mode. The process must exit cleanly without a
   crash, abort message, or lingering Python process.
11. Launch and close the command three times to exercise viewer lifecycle cleanup.

Please report the macOS version, Python version, the three-version verification
line, and any failed checklist item. A screenshot is useful for visual defects
but is not required.
