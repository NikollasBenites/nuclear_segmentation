"""Headless contract tests for the independent Phase 1B desktop shell."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtpy.QtCore import QByteArray, QSettings, Qt
from qtpy.QtWidgets import QApplication, QWidget

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
    assert window.open_image_button.isEnabled()
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


def test_project_sections_toggle_independently_preserve_values_and_workflow(window):
    panel = window.context_panel.project_page
    panel.axes_override.setText("TCZYX")
    assert all(section.expanded for section in panel.sections.values())
    panel.sections["provenance"].header.click()
    panel.sections["channels"].header.click()
    assert not panel.sections["provenance"].expanded
    assert panel.sections["calibration"].expanded
    assert not panel.sections["channels"].expanded
    assert panel.axes_override.text() == "TCZYX"
    window.set_workflow("preprocessing")
    window.set_workflow("project")
    assert not panel.sections["provenance"].expanded
    assert not panel.sections["channels"].expanded
    assert panel.axes_override.text() == "TCZYX"
    panel.sections["provenance"].header.click()
    panel.sections["channels"].header.click()


def test_project_section_states_restore_and_invalid_values_expand(
    qapp, settings
):
    first = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=False,
    )
    first.context_panel.project_page.sections["calibration"].set_expanded(False)
    first.context_panel.project_page.sections["channels"].set_expanded(False)
    first.close()
    second = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=True,
    )
    panel = second.context_panel.project_page
    assert not panel.sections["calibration"].expanded
    assert not panel.sections["channels"].expanded
    panel.spacing_fields[0].setText("1")
    with pytest.raises(ValueError, match="all three"):
        panel.import_options()
    assert panel.sections["calibration"].expanded
    panel.sections["channels"].set_expanded(False)
    second._update_channel_configuration(99, "name", "invalid")
    assert panel.sections["channels"].expanded
    second.close()


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
    group_names = [panel.model.item(row, 0).text() for row in range(panel.model.rowCount())]
    assert "Annotations & Overlays" not in group_names
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
    group_names = [panel.model.item(row, 0).text() for row in range(panel.model.rowCount())]
    assert "Annotations & Overlays" in group_names
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
    group_names = [panel.model.item(row, 0).text() for row in range(panel.model.rowCount())]
    assert "Annotations & Overlays" not in group_names


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


def test_review_tool_selects_clears_and_highlights_masks_without_data_changes(
    window,
):
    viewer = window.embedding.viewer
    labels = window.embedding.demo_labels()
    viewer.layers.selection.active = labels
    original_data = labels.data.copy()
    original_selected = labels.selected_label
    original_contour = labels.contour
    original_show_selected = labels.show_selected_label

    assert window.viewer_toolbar.activate_tool("review")
    assert str(labels.mode) == "pan_zoom"
    assert window.mask_selection._on_mouse_press in labels.mouse_drag_callbacks

    first_position = tuple(np.argwhere(labels.data == 1)[0])
    second_position = tuple(np.argwhere(labels.data == 2)[0])
    background_position = tuple(np.argwhere(labels.data == 0)[0])
    viewer.dims.set_current_step(0, first_position[0])
    assert window.mask_selection.select_at_data_position(labels, first_position) == 1
    assert window.mask_selection.selected_mask_id == 1
    assert labels.selected_label == original_selected
    assert labels.show_selected_label == original_show_selected
    assert labels.contour == original_contour
    highlight = window.mask_selection._highlight_layer
    assert highlight is not None
    assert highlight.metadata["nucytoseg_selection_highlight"]
    assert highlight.data.size < labels.data.size
    assert viewer.layers.selection.active is labels
    assert window.selected_mask_panel.mask_id_label.text() == "Mask ID: 1"

    viewer.dims.set_current_step(0, min(first_position[0] + 1, labels.data.shape[0] - 1))
    assert window.mask_selection.selected_mask_id == 1
    viewer.dims.set_current_step(0, second_position[0])
    assert window.mask_selection.select_at_data_position(labels, second_position) == 2
    assert window.selected_mask_panel.mask_id_label.text() == "Mask ID: 2"

    viewer.dims.set_current_step(0, background_position[0])
    assert window.mask_selection.select_at_data_position(labels, background_position) is None
    assert window.mask_selection.selected_mask_id is None
    assert window.mask_selection._highlight_layer is None
    assert window.selected_mask_panel.mask_id_label.text() == "Mask ID: —"
    assert labels.selected_label == original_selected
    assert labels.contour == original_contour
    assert labels.show_selected_label == original_show_selected
    np.testing.assert_array_equal(labels.data, original_data)


def test_review_mouse_callback_uses_active_labels_layer(window, monkeypatch):
    viewer = window.embedding.viewer
    labels = window.embedding.demo_labels()
    viewer.layers.selection.active = labels
    assert window.viewer_toolbar.activate_tool("review")
    monkeypatch.setattr(type(labels), "get_value", lambda self, *args, **kwargs: 2)
    event = SimpleNamespace(
        position=(0.0, 0.0, 0.0),
        view_direction=(1.0, 0.0, 0.0),
        dims_displayed=(1, 2),
    )
    window.mask_selection._on_mouse_press(labels, event)
    assert window.mask_selection.selected_mask_id == 2
    assert window.selected_mask_panel.mask_id_label.text() == "Mask ID: 2"


def test_review_selection_switches_labels_layers_and_isolates_editing(window):
    viewer = window.embedding.viewer
    first = window.embedding.demo_labels()
    second_data = np.zeros_like(first.data)
    second_data[2:5, 8:14, 9:15] = 9
    second = viewer.add_labels(second_data, name="Cytoplasmic Masks")
    viewer.layers.selection.active = first
    assert window.viewer_toolbar.activate_tool("review")
    first_position = tuple(np.argwhere(first.data == 3)[0])
    viewer.dims.set_current_step(0, first_position[0])
    assert window.mask_selection.select_at_data_position(first, first_position) == 3

    viewer.layers.selection.active = second
    assert window.mask_selection.selected_mask_id is None
    second_position = tuple(np.argwhere(second.data == 9)[0])
    viewer.dims.set_current_step(0, second_position[0])
    assert window.mask_selection.select_at_data_position(second, second_position) == 9
    assert window.selected_mask_panel.mask_id_label.text() == "Mask ID: 9"

    viewer.layers.selection.active = first
    assert window.mask_selection.selected_mask_id == 3
    assert window.mask_selection._highlight_layer is not None
    assert window.viewer_toolbar.activate_tool("paint")
    assert str(first.mode) == "paint"
    assert window.mask_selection._on_mouse_press not in first.mouse_drag_callbacks
    assert window.mask_selection._highlight_layer is None
    assert window.mask_selection.select_at_data_position(first, first_position) is None
    assert window.mask_selection.selected_mask_id == 3
    assert window.viewer_toolbar.activate_tool("erase")
    assert str(first.mode) == "erase"

    assert window.viewer_toolbar.activate_tool("review")
    window.viewer_toolbar.set_ndisplay(3)
    assert window.mask_selection.select_at_data_position(first, first_position) is None
    assert window.mask_selection.selected_mask_id == 3
    window.viewer_toolbar.set_ndisplay(2)
    viewer.layers.remove(second)


def test_review_3d_ray_picking_nearest_rotation_spacing_and_persistence(window):
    viewer = window.embedding.viewer
    data = np.zeros((10, 12, 12), dtype=np.uint16)
    data[2:4, 2:4, 2:4] = 11
    data[7:9, 7:9, 7:9] = 22
    original = data.copy()
    labels = viewer.add_labels(
        data,
        name="Anisotropic masks",
        scale=(3.0, 0.7, 0.7),
    )
    viewer.layers.selection.active = labels
    assert window.viewer_toolbar.activate_tool("review")
    assert window.viewer_toolbar.set_ndisplay(3)
    labels.refresh(data_displayed=True)

    center = np.array((5.0, 5.0, 5.0))
    world_position = np.asarray(labels.data_to_world(center), dtype=float)
    forward = np.asarray(labels.data_to_world(center + (1, 1, 1)), dtype=float)
    forward = (forward - world_position) / np.linalg.norm(forward - world_position)
    assert (
        window.mask_selection.pick_from_ray(
            labels, world_position, forward, (0, 1, 2), world=True
        )
        == 11
    )
    assert window.mask_selection.selected_mask_id == 11
    highlight = window.mask_selection._highlight_layer
    assert highlight is not None
    assert highlight.contour == 0
    assert window.layer_panel.model.item_for_layer(highlight) is None
    np.testing.assert_allclose(
        highlight.data_to_world((1, 1, 1)), labels.data_to_world((2, 2, 2))
    )

    assert (
        window.mask_selection.pick_from_ray(
            labels, world_position, -forward, (0, 1, 2), world=True
        )
        == 22
    )
    assert window.selected_mask_panel.mask_id_label.text() == "Mask ID: 22"
    assert window.viewer_toolbar.set_ndisplay(2)
    assert window.mask_selection.selected_mask_id == 22
    assert window.mask_selection._highlight_layer.contour == 2
    assert window.viewer_toolbar.set_ndisplay(3)
    assert window.mask_selection.selected_mask_id == 22

    window.set_workflow("export")
    assert window.selected_mask_panel.isVisible()
    assert window.selected_mask_panel.mask_id_label.text() == "Mask ID: 22"
    sidebar_layout = window.context_container.layout()
    assert sidebar_layout.indexOf(window.selected_mask_panel) > sidebar_layout.indexOf(
        window.layer_panel
    )

    empty_position = np.asarray(labels.data_to_world((5, 1, 1)), dtype=float)
    assert (
        window.mask_selection.pick_from_ray(
            labels, empty_position, forward, (0, 1, 2), world=True
        )
        is None
    )
    assert window.mask_selection.selected_mask_id is None
    np.testing.assert_array_equal(labels.data, original)

    window.mask_selection.pick_from_ray(
        labels, world_position, forward, (0, 1, 2), world=True
    )
    labels.visible = False
    assert window.mask_selection.selected_layer is None
    assert window.mask_selection._highlight_layer is None
    assert window.selected_mask_panel.mask_id_label.text() == "Mask ID: —"
    labels.visible = True
    assert window.mask_selection.selected_mask_id == 11
    callback = window.mask_selection._on_mouse_press
    viewer.layers.remove(labels)
    assert callback not in labels.mouse_drag_callbacks
    assert window.selected_mask_panel.mask_id_label.text() == "Mask ID: —"


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
        labels = value.embedding.demo_labels()
        value.embedding.viewer.layers.selection.active = labels
        assert value.viewer_toolbar.activate_tool("review")
        callback = value.mask_selection._on_mouse_press
        assert callback in labels.mouse_drag_callbacks
        value.close()
        assert callback not in labels.mouse_drag_callbacks
        assert qt_viewer.close_count == 1
        assert len(value.embedding.viewer.layers) == 0
        value.close()
        assert qt_viewer.close_count == 1
        value.deleteLater()
        qapp.processEvents()
