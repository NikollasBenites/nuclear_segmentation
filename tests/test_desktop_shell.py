"""Headless contract tests for the independent Phase 1B desktop shell."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QByteArray, QSettings, Qt
from qtpy.QtWidgets import QApplication, QWidget
import pytest

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.layer_panel import LAYER_ROLE
from nuclear_segmentation.ui_icons import icon
from nuclear_segmentation.workspace_settings import SETTINGS_GROUP


class FakeQtViewer(QWidget):
    def __init__(self, viewer, show_welcome_screen=False):
        super().__init__()
        self.viewer = viewer
        self.close_count = 0

    def closeEvent(self, event):
        self.close_count += 1
        event.accept()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    value = QSettings(str(tmp_path / "workspace.ini"), QSettings.Format.IniFormat)
    value.clear()
    return value


@pytest.fixture
def window(qapp, settings):
    value = DesktopWindow(
        load_demo=True,
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    value.show()
    qapp.processEvents()
    yield value
    value.close()
    value.deleteLater()
    qapp.processEvents()


def test_main_window_initialization_and_navigation(window):
    assert window.windowTitle().startswith("NuCytoSeg")
    assert window.header_workflow.text() == "PROJECT"
    assert not window.open_image_button.isEnabled()
    assert window.embedding.viewer.layers
    original_viewer = window.embedding.viewer
    window.navigation._buttons["segmentation"].click()
    assert window.navigation.current_workflow == "segmentation"
    assert window.context_panel.heading.text() == "Segmentation"
    assert window.header_workflow.text() == "SEGMENTATION"
    assert window.embedding.viewer is original_viewer


def test_panel_collapse_expansion_and_context_visibility(window, qapp):
    window.navigation.set_collapsed(True)
    assert window.navigation.width() == 64
    assert all(
        button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        for button in window.navigation._buttons.values()
    )
    window.navigation.set_collapsed(False)
    assert window.navigation.width() == 210
    window.hide_context_panel()
    qapp.processEvents()
    assert not window.context_container.isVisible()
    window.toggle_context_action.setChecked(True)
    qapp.processEvents()
    assert window.context_container.isVisible()


def test_focus_mode_restores_unsaved_normal_layout(window, qapp):
    window.navigation.set_collapsed(False)
    window.toggle_context_action.setChecked(True)
    window.splitter.setSizes([210, 760, 320])
    normal_sizes = window.splitter.sizes()
    window.focus_action.setChecked(True)
    qapp.processEvents()
    assert window.navigation.collapsed
    assert not window.context_container.isVisible()
    assert window.viewer_toolbar.isVisible()
    window.focus_action.setChecked(False)
    qapp.processEvents()
    assert not window.navigation.collapsed
    assert window.context_container.isVisible()
    assert window.splitter.sizes() == normal_sizes


def test_workspace_save_restore_history_and_reset(window):
    window.set_workflow("matching")
    window.navigation.set_collapsed(True)
    window.save_layout()
    window.set_workflow("export")
    window.navigation.set_collapsed(False)
    window.save_layout()
    assert window.restore_previous_layout()
    assert window.navigation.current_workflow == "matching"
    assert window.navigation.collapsed
    window.reset_layout()
    assert window.navigation.current_workflow == "project"
    assert not window.navigation.collapsed
    assert window.workspace_settings.settings.value(SETTINGS_GROUP) is None


def test_invalid_settings_recover_to_functional_default(qapp, settings):
    settings.setValue(f"{SETTINGS_GROUP}/current/geometry", QByteArray(b"invalid"))
    settings.setValue(f"{SETTINGS_GROUP}/current/splitter_sizes", ["bad"])
    value = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=True,
    )
    assert value.width() >= value.minimumWidth()
    assert value.height() >= value.minimumHeight()
    assert value.navigation.current_workflow == "project"
    value.close()


def test_layer_tree_selection_visibility_rename_add_remove_and_reorder(window, qapp):
    viewer = window.embedding.viewer
    panel = window.layer_panel
    image = viewer.layers[0]
    item = panel.model.item_for_layer(image)
    panel.tree.setCurrentIndex(item.index())
    assert viewer.layers.selection.active is image
    active = viewer.layers.selection.active
    visibility_item = panel.model.visibility_item_for_layer(image)
    assert visibility_item.toolTip() == "Hide layer"
    assert not visibility_item.icon().isNull()
    panel.tree.clicked.emit(visibility_item.index())
    assert not image.visible
    assert viewer.layers.selection.active is active
    assert visibility_item.toolTip() == "Show layer"

    image.visible = True
    qapp.processEvents()
    assert visibility_item.toolTip() == "Hide layer"
    assert viewer.layers.selection.active is active

    visibility_before_rename = image.visible
    item.setText("Renamed image")
    assert image.name == "Renamed image"
    assert image.visible is visibility_before_rename
    added = viewer.add_labels(image.data.astype("uint16"), name="Nuclear Masks")
    qapp.processEvents()
    assert panel.model.item_for_layer(added) is not None
    other = viewer.add_points([[1, 2, 3]], name="Review Points")
    qapp.processEvents()
    viewer.layers.selection.active = image
    other_visibility = panel.model.visibility_item_for_layer(other)
    panel.tree.clicked.emit(other_visibility.index())
    assert not other.visible
    assert viewer.layers.selection.active is image
    viewer.layers.move(0, 1)
    qapp.processEvents()
    assert panel.model.item_for_layer(added) is not None
    viewer.layers.remove(added)
    viewer.layers.remove(other)
    qapp.processEvents()
    assert panel.model.item_for_layer(added) is None


def test_vector_icons_render_at_multiple_pixel_sizes(qapp):
    for name in ("eye", "eye_off", "folder", "paint", "fit"):
        value = icon(name)
        assert not value.isNull()
        assert not value.pixmap(16, 16).isNull()
        assert not value.pixmap(64, 64).isNull()


def test_viewer_toolbar_validates_active_layer_and_commands(window):
    viewer = window.embedding.viewer
    toolbar = window.viewer_toolbar
    image = viewer.layers[0]
    labels = window.embedding.demo_labels()
    data_identity = id(labels.data)
    physical_scale = tuple(labels.scale)
    viewer.layers.selection.active = image
    assert not toolbar.buttons["paint"].isEnabled()
    assert not toolbar.activate_tool("paint")
    assert toolbar.activate_tool("pan_zoom")
    viewer.layers.selection.active = labels
    assert toolbar.buttons["paint"].isEnabled()
    assert toolbar.activate_tool("paint")
    assert str(labels.mode) == "paint"
    assert toolbar.activate_tool("erase")
    assert str(labels.mode) == "erase"
    assert toolbar.activate_tool("pick")
    assert str(labels.mode) == "pick"
    assert toolbar.set_ndisplay(3)
    assert viewer.dims.ndisplay == 3
    assert toolbar.set_ndisplay(2)
    toolbar.fit_to_view()
    assert id(labels.data) == data_identity
    assert tuple(labels.scale) == physical_scale


def test_active_layer_external_change_selects_tree_row(window):
    layer = window.embedding.viewer.layers[1]
    window.embedding.viewer.layers.selection.active = layer
    current = window.layer_panel.tree.currentIndex()
    assert current.data(LAYER_ROLE) is layer


def test_cleanup_and_repeated_window_construction(qapp, tmp_path):
    for index in range(3):
        settings = QSettings(
            str(tmp_path / f"workspace-{index}.ini"), QSettings.Format.IniFormat
        )
        value = DesktopWindow(
            load_demo=True,
            qt_viewer_factory=FakeQtViewer,
            settings=settings,
            restore_workspace=False,
        )
        qt_viewer = value.embedding.qt_viewer
        value.close()
        assert qt_viewer.close_count == 1
        assert len(value.embedding.viewer.layers) == 0
        value.close()
        assert qt_viewer.close_count == 1
        value.deleteLater()
        qapp.processEvents()
