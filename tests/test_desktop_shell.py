"""Headless contract tests for the independent Phase 1B desktop shell."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtpy.QtCore import QByteArray, QEvent, QSettings, Qt
from qtpy.QtGui import QHelpEvent
from qtpy.QtWidgets import QApplication, QStyle, QStyleOptionComboBox, QToolTip, QWidget
from qtpy.QtTest import QTest

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.layer_panel import LAYER_ROLE
from nuclear_segmentation.project_state import ChannelDescriptor, ImportedDataset
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
    assert window.context_title.text() == "Segmentation Parameters"


@pytest.mark.parametrize("size", [(1024, 680), (1440, 900)])
def test_hierarchical_navigation_at_supported_sizes(window, size):
    window.resize(*size)
    window.splitter.setSizes([200, size[0] - 528, 320])
    navigation = window.navigation
    context = window.context_panel
    workflows = (
        ("preprocessing", ("crop_roi", "z_correction", "normalization")),
        ("mask_review", ("filtering", "roi_counting")),
        ("export", ("scientific_results", "multichannel_images")),
    )
    for workflow, keys in workflows:
        navigation._buttons[workflow].click()
        assert navigation.current_subpage == keys[0]
        assert [key for key, submenu in navigation._submenus.items() if submenu.isVisible()] == [workflow]
        buttons = navigation._subpage_buttons[workflow]
        assert tuple(buttons) == keys
        for key in keys:
            button = buttons[key]
            button.setFocus()
            QTest.keyClick(button, Qt.Key.Key_Space)
            QTest.qWait(25)
            assert navigation.current_subpage == key and button.isChecked()
            assert button.width() >= button.sizeHint().width()
            assert button.toolTip()
            scroll = context._subpage_stacks[workflow].currentWidget()
            assert scroll.widget() is context.stack.currentWidget().pages[key]
            assert scroll.viewport().height() >= 105
            assert scroll.horizontalScrollBar().maximum() == 0

    navigation._buttons["preprocessing"].click()
    assert navigation.current_subpage == "normalization"
    assert window.context_title.text() == "Preprocessing Parameters"
    assert context.heading.text() == "Segmentation Input Normalization"
    navigation.collapse_button.click()
    assert navigation.collapsed and navigation.width() == 44
    assert navigation.stage_bar.isVisible() and navigation._submenus["preprocessing"].isVisible()
    navigation._buttons["mask_review"].click()
    assert navigation.current_subpage == "roi_counting"
    navigation.collapse_button.click()
    assert navigation._submenus["mask_review"].isVisible()
    assert navigation._subpage_buttons["mask_review"]["roi_counting"].isChecked()


@pytest.mark.parametrize("size", [(1024, 680), (1440, 900)])
@pytest.mark.parametrize("parameters_width", [320, 540])
def test_scientific_dropdowns_fit_panel_and_long_popup_options(window, tmp_path, size, parameters_width):
    window.resize(*size)
    window.splitter.setSizes([200, size[0] - 208 - parameters_width, parameters_width])
    names = ("DAPI nuclear reference", "Acquisition channel — " + "long fluorescence name " * 3)
    pixels = np.zeros((2, 2, 8, 8), dtype=np.uint16)
    window.project_state.set_dataset(ImportedDataset(
        source_path=tmp_path / "channels.tif", source_format="TIFF",
        series_index=0, series_identifier="0", series_name="Channels",
        reported_axes="CZYX", reported_shape=pixels.shape, standardized_shape=pixels.shape,
        dtype=str(pixels.dtype), data_czyx=pixels, voxel_spacing_zyx_um=(1., 1., 1.),
        calibration_source="test",
        channels=tuple(ChannelDescriptor(i, name, name) for i, name in enumerate(names)),
    ))
    context = window.context_panel
    projection = context.preprocessing_page
    z_panel = projection.z_correction_panel
    review = context.mask_review_page
    review.add_marker()
    export = context.export_page
    preparation = export.image_preparation_panel
    groups = (
        ("preprocessing", "crop_roi", (projection.channel_combo, projection.method_combo)),
        ("preprocessing", "z_correction", (z_panel.inspect_combo, z_panel.results_channel_combo)),
        ("preprocessing", "normalization", (projection.normalization_channel_combo, projection.normalization_mode_combo)),
        ("mask_review", "filtering", (review.marker_rows[0].channel,)),
        ("export", "multichannel_images", (preparation.inspect_combo, preparation.scope_combo, export.pixel_type_combo)),
    )
    for workflow, subpage, combos in groups:
        window.set_workflow(workflow)
        window.navigation.set_subpage(subpage, emit=True)
        QTest.qWait(30)
        assert window.context_container.width() == parameters_width
        scroll = context._subpage_stacks[workflow].currentWidget()
        assert scroll.horizontalScrollBar().maximum() == 0
        if subpage == "crop_roi":
            assert projection.channel_combo.width() == projection.method_combo.width()
        for combo in combos:
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            edit = combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, combo,
            )
            assert combo.width() >= scroll.viewport().width() - 65
            assert edit.width() >= combo.fontMetrics().horizontalAdvance(combo.currentText())

        combo = combos[0]
        data = combo.currentData()
        scroll.ensureWidgetVisible(combo, 0, 0)
        combo.showPopup()
        try:
            QTest.qWait(20)
            view = combo.view()
            assert view.isVisible()
            assert view.viewport().width() >= view.sizeHintForColumn(0)
            assert not view.horizontalScrollBar().isVisible()
            assert view.window().frameGeometry().right() <= combo.screen().availableGeometry().right()
        finally:
            combo.hidePopup()
        point = combo.rect().center()
        QApplication.sendEvent(combo, QHelpEvent(QEvent.Type.ToolTip, point, combo.mapToGlobal(point)))
        assert combo.toolTip() == combo.currentText()
        QToolTip.hideText()
        assert combo.currentData() == data


def test_z_correction_sections_collapse_to_headers_and_expand_in_place(window):
    window.set_workflow("preprocessing")
    window.navigation.set_subpage("z_correction", emit=True)
    context = window.context_panel
    scroll = context._subpage_stacks["preprocessing"].currentWidget()
    panel = context.preprocessing_page.z_correction_panel
    correction = panel.layout().itemAt(0).widget()
    results = panel.results
    QTest.qWait(30)
    for section in (correction, results):
        assert section.height() > section.header.height()
        section.header.click()
    QTest.qWait(30)
    assert not correction.expanded and not results.expanded
    assert all(section.height() == section.header.height() for section in (correction, results))
    assert correction.y() == 0
    assert results.y() == correction.height() + panel.layout().spacing()
    assert results.geometry().bottom() < scroll.viewport().height() - 1
    assert scroll.verticalScrollBar().maximum() == 0
    results.header.click()
    QTest.qWait(30)
    assert correction.height() == correction.header.height()
    assert results.expanded and results.height() > results.header.height()
    correction.header.click()
    QTest.qWait(30)
    assert correction.expanded and results.expanded
    assert scroll.verticalScrollBar().maximum() > 0
    assert panel.results is results


def test_quality_control_uses_one_persistent_plot_widget(window, qapp):
    plots = window.context_panel.quality_control_page
    graphs = window.graph_window
    window.set_workflow("quality_control")
    QTest.qWait(350)
    assert window.context_panel.heading.text() == "Quality Control"
    assert plots.isVisible()
    assert window.results.currentWidget() is window.results.graph_page
    window.context_panel.show_graphs_button.click()
    assert graphs.isVisible() and graphs.parent() is window
    assert "no current segmentation" in plots.status.text().lower()
    canvas, toolbar = plots.canvas, plots.toolbar
    plots.kind.setCurrentText("Intensity")
    plots.column.addItems(["Channel A", "Channel B"])
    plots.column.setCurrentIndex(1)
    window.set_workflow("project")
    assert graphs.isHidden() and not plots.timer.isActive()
    window.set_workflow("quality_control")
    qapp.processEvents()
    assert window.context_panel.quality_control_page is plots
    assert plots.canvas is canvas and plots.toolbar is toolbar
    assert plots.kind.currentText() == "Intensity"
    assert plots.column.currentText() == "Channel B"
    graphs.close()
    assert graphs.isHidden()
    window.context_panel.show_graphs_button.click()
    assert window.graph_window is graphs and graphs.isVisible()
    window.embedding.viewer.add_image(np.ones((2, 3, 4)))
    assert window.close_project(confirm=False)
    assert graphs.isHidden() and not plots.z_profile
    assert not plots.timer.isActive() and not plots.z_timer.isActive()


def test_panel_collapse_expansion_and_context_visibility(window, qapp):
    window.navigation.set_collapsed(True)
    assert window.navigation.width() == 44
    assert window.navigation.stage_bar.isVisible()
    assert not window.viewer_toolbar.isVisible()
    window.navigation.set_collapsed(False)
    assert window.viewer_toolbar.isVisible()
    window.hide_context_panel()
    qapp.processEvents()
    assert not window.context_container.isVisible()
    assert window.parameters_button.isVisible()
    assert window.parameters_button.text() == "Show Parameters"
    assert window.parameters_button.defaultAction() is window.toggle_context_action
    assert window.toggle_context_action.shortcut().toString() == "Ctrl+Shift+R"
    window.parameters_button.click()
    qapp.processEvents()
    assert window.context_container.isVisible()
    assert window.toggle_context_action.isChecked()
    assert window.parameters_button.text() == "Hide Parameters"


def test_layers_collapse_reuses_left_panel_independently_of_parameters(window, qapp):
    window.resize(1440, 1050)
    viewer = window.embedding.viewer
    layers = tuple(viewer.layers)
    layer_panel = window.layer_panel
    selected = layers[-1]
    viewer.layers.selection.active = selected
    selected.visible = False
    QTest.qWait(30)
    parameters_height = window.context_panel.height()
    display = window.image_display_panel.section
    display_state = display.expanded

    window.layers_section.header.click()
    QTest.qWait(30)
    assert not window.layers_section.expanded
    assert not layer_panel.isVisible()
    assert window.selected_mask_panel.isVisible()
    assert window.context_panel.height() == parameters_height
    assert display.expanded == display_state
    window.set_workflow("export")
    assert not window.layers_section.expanded
    display.header.click()
    assert not window.layers_section.expanded
    window.layers_section.header.click()
    qapp.processEvents()
    assert display.expanded != display_state
    assert window.layer_panel is layer_panel and layer_panel.isVisible()
    assert tuple(viewer.layers) == layers
    assert viewer.layers.selection.active is selected and not selected.visible


def test_focus_mode_restores_unsaved_normal_layout(window, qapp):
    window.navigation.set_collapsed(False)
    window.toggle_context_action.setChecked(True)
    window.splitter.setSizes([210, 760, 320])
    normal_sizes = window.splitter.sizes()
    window.focus_action.setChecked(True)
    qapp.processEvents()
    assert window.navigation.collapsed
    assert not window.context_container.isVisible()
    assert window.embedding.isVisible() and window.navigation.stage_bar.isVisible()
    assert not window.navigation.isVisible() and not window.results.isVisible()
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
    first.set_workflow("export")
    first.navigation.set_subpage("multichannel_images", emit=True)
    first.set_workflow("preprocessing")
    first.navigation.set_subpage("normalization", emit=True)
    first.close()
    second = DesktopWindow(
        qt_viewer_factory=FakeQtViewer,
        settings=settings,
        restore_workspace=True,
    )
    panel = second.context_panel.project_page
    assert second.navigation.current_workflow == "preprocessing"
    assert second.context_panel.heading.text() == "Segmentation Input Normalization"
    second.set_workflow("export")
    assert second.navigation.current_subpage == "multichannel_images"
    second.set_workflow("project")
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
    sidebar_layout = window.navigation.tools
    assert sidebar_layout.indexOf(window.selected_mask_panel) > sidebar_layout.indexOf(
        window.layers_section
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


def test_proofreading_page_and_old_layout_migration(window):
    from dataclasses import replace
    navigation, ctx = window.navigation, window.context_panel
    assert list(navigation._buttons) == ["project", "preprocessing", "segmentation", "mask_review",
                                         "matching", "quantification", "quality_control", "export"]
    window.set_workflow("proofreading")
    assert ctx.stack.currentWidget().widget() is ctx.mask_review_page.proofreading_page
    assert window.geometry_panel.isVisible()
    window.geometry_panel.set_expanded(False)
    window.geometry_panel.merge_section.set_expanded(True)
    snapshot = replace(window.workspace_snapshot(), workflow="mask_review",
                       workflow_subpages=(("mask_review", "mask_editing"),))
    window.workspace_settings.write(snapshot)
    window.geometry_panel.set_expanded(True)
    window.geometry_panel.merge_section.set_expanded(False)
    window.set_workflow("mask_review")
    window.restore_layout()
    assert navigation.current_workflow == "proofreading" and not window.geometry_panel.expanded
    assert window.geometry_panel.merge_section.expanded
    window.set_workflow("mask_review")
    assert navigation.current_subpage == "filtering"
