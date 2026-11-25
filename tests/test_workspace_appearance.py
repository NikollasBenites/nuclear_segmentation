from qtpy.QtCore import QSettings
from qtpy.QtTest import QTest

from nuclear_segmentation.desktop_window import DesktopWindow
from test_desktop_shell import qapp


def test_theme_preserves_embedded_canvas_and_scientific_state(tmp_path, qapp):
    window = DesktopWindow(load_demo=True, settings=QSettings(str(tmp_path / "theme.ini"), QSettings.Format.IniFormat))
    try:
        window.resize(1024, 680)
        window.show()
        QTest.qWait(80)
        viewer = window.embedding.viewer
        canvas = window.embedding.qt_viewer.canvas
        frame = canvas.screenshot()
        state = window.project_state
        image = viewer.layers[0]
        def snapshot():
            return (state.active_session_id, state.session_revision(), window.project_dirty,
                    tuple(viewer.layers), viewer.layers.selection.active, tuple(image.contrast_limits),
                    image.colormap.name, id(image.data), tuple(image.scale), viewer.dims.point,
                    viewer.camera.center, viewer.camera.zoom, tuple(window.splitter.sizes()),
                    window.context_panel.segmentation_page.pending_config())
        before = snapshot()
        for name in ("Light", "Dark"):
            window.theme_combo.setCurrentText(name)
            QTest.qWait(80)
            assert window.embedding.qt_viewer.canvas is canvas
            assert not frame.isNull() and frame == canvas.screenshot()
            assert snapshot() == before
    finally:
        window.close()
