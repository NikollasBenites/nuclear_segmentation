from types import SimpleNamespace

import numpy as np
import pytest
from napari.utils.interactions import mouse_move_callbacks, mouse_press_callbacks, mouse_release_callbacks
from qtpy.QtCore import QSettings, Qt
from qtpy.QtTest import QTest

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.geometry_edit import effective_masks
from test_desktop_normalization import _dataset
from test_mask_review_gui import qapp, retained_second_mask, review_window


def click(layer, position, *, axes=(1, 2), direction=None, modifiers=(), drag=False):
    event = SimpleNamespace(position=tuple(layer.data_to_world(position)), dims_displayed=axes,
                            view_direction=direction, pos=(50, 50), type="mouse_press",
                            button=2, modifiers=modifiers)
    mouse_press_callbacks(layer, event)
    if drag:
        event.type = "mouse_move"
        event.is_dragging = True
        event.pos = (100, 100)
        mouse_move_callbacks(layer, event)
        event.pos = (50, 50)
    event.type = "mouse_release"
    mouse_release_callbacks(layer, event)


def editing_page(window):
    window.set_workflow("proofreading")


def actions(window):
    menu = window.mask_context_menu.menu
    assert menu is not None and menu.isVisible()
    return {action.text(): action for action in menu.actions() if not action.isSeparator()}


def test_menu_selection_properties_and_transactions(review_window):
    window, raw = review_window
    editing_page(window)
    state = window.project_state
    layer = window.segmentation.result_layer
    window.embedding.viewer.layers.selection.active = layer
    window.viewer_toolbar.activate_tool("review")
    click(layer, (0, 5, 6))
    choices = actions(window)
    assert window.mask_selection.selected_mask_id is None
    assert {name for name, action in choices.items() if action.isEnabled()} == {
        "Select Mask", "Show Selected Only", "Start Editing", "Show Mask Properties"}
    choices["Show Mask Properties"].trigger()
    review = window.context_panel.mask_review_page
    assert "Mask 2 · 16 voxels" in review.measurements.text()
    assert review.information_section.expanded
    click(layer, (0, 5, 6))
    actions(window)["Show Selected Only"].trigger()
    assert layer.show_selected_label and layer.selected_label == 2
    click(layer, (0, 1, 1))
    assert window.mask_context_menu.menu is None
    click(layer, (0, 5, 6))
    actions(window)["Start Editing"].trigger()
    panel = window.geometry_panel
    preview = panel.layer
    assert panel.selected_id == panel.brush_id == 2
    click(preview, (0, 1, 1))
    choices = actions(window)
    assert choices["Add to Merge Selection"].isEnabled()
    assert "Start Editing" not in choices and "Show Selected Only" not in choices
    choices["Add to Merge Selection"].trigger()
    assert panel.merge_list.item(0).text() == "1"
    assert panel.selected_id == panel.brush_id == 1
    click(preview, (0, 5, 6))
    actions(window)["Remove Object"].trigger()
    assert panel.edit.steps[-1]["operation"] == "remove" and not (preview.data == 2).any()
    assert effective_masks(state.segmentation_result) is raw
    panel.commit()
    assert not (effective_masks(state.segmentation_result) == 2).any()
    panel.undo()
    assert np.array_equal(effective_masks(state.segmentation_result), raw)
    assert window.mask_context_menu._gesture not in preview.mouse_drag_callbacks


def test_menu_split_and_native_event_conflicts(review_window):
    window, raw = review_window
    editing_page(window)
    panel = window.geometry_panel
    panel.start()
    preview = panel.layer
    for position, options in [((0, 0, 0), {}), ((0, 1, 1), {"drag": True}),
                              ((0, 1, 1), {"modifiers": ("Shift",)})]:
        click(preview, position, **options)
        assert window.mask_context_menu.menu is None and panel.selected_id is None
    click(preview, (0, 1, 1))
    actions(window)["Split Object"].trigger()
    child = panel.edit.parameters["new_id"]
    assert panel.selected_id == 1 and panel.brush_id == child and str(preview.mode) == "paint"
    # A native brush-mode event belongs to napari. The menu callback exits before picking.
    for mode in ("paint", "erase", "pick"):
        window.viewer_toolbar.activate_tool(mode)
        assert list(window.mask_context_menu._gesture(preview, SimpleNamespace(button=2, modifiers=()))) == []
    window.viewer_toolbar.activate_tool("review")
    click(preview, (0, 5, 6))
    assert {key for key, action in actions(window).items() if action.isEnabled()} == {"Show Object Properties"}
    actions(window)["Show Object Properties"].trigger()
    assert panel.selected_id == 1 and panel.brush_id == child
    panel.discard()
    assert np.array_equal(window.project_state.segmentation_result["masks"], raw)


def test_menu_3d_camera_ray_selects_nearest_id_in_each_direction(review_window):
    window, raw = review_window
    editing_page(window)
    masks = np.zeros_like(raw)
    masks[0, 2, 2] = 7
    masks[-1, 2, 2] = 91
    state = window.project_state
    state.set_segmentation_result(dict(state.segmentation_result, masks=masks, maximum_label_id=91))
    layer = window.segmentation.result_layer
    window.viewer_toolbar.set_ndisplay(3)
    layer.refresh(data_displayed=True)
    for direction, expected in [((1, 0, 0), 7), ((-1, 0, 0), 91)]:
        click(layer, (1, 2, 2), axes=(0, 1, 2), direction=direction)
        actions(window)["Select Mask"].trigger()
        assert window.mask_selection.selected_mask_id == expected
    click(layer, (1, 2, 2), axes=(0, 1, 2))
    assert window.mask_context_menu.menu is None


def test_foreign_layer_and_stale_context_cannot_edit(review_window):
    window, raw = review_window
    editing_page(window)
    viewer = window.embedding.viewer
    state = window.project_state
    layer = window.segmentation.result_layer
    click(layer, (0, 1, 1))
    start = actions(window)["Start Editing"]
    foreign = viewer.add_labels(raw.copy(), name="Other labels")
    click(foreign, (0, 1, 1))
    assert window.mask_context_menu.menu is None
    start.trigger()
    assert not window.geometry_panel.busy
    viewer.layers.selection.active = layer
    click(layer, (0, 1, 1))
    stale = actions(window)["Select Mask"]
    second = state.create_session("Other", channel_index=0)
    state.select_session(second.session_id)
    stale.trigger()
    assert window.mask_selection.selected_mask_id is None
    assert window.mask_context_menu._gesture not in layer.mouse_drag_callbacks
    assert window.close_project(confirm=False)
    assert window.mask_context_menu.layer is None


def test_rendered_canvas_secondary_click_opens_menu(qapp, tmp_path):
    if qapp.platformName() in {"offscreen", "minimal"}:
        pytest.skip("This test sends a physical Qt mouse event to an OpenGL canvas.")
    window = DesktopWindow(settings=QSettings(str(tmp_path / "menu.ini"), QSettings.Format.IniFormat),
                           restore_workspace=False)
    try:
        window.show()
        editing_page(window)
        state = window.project_state
        state.set_dataset(_dataset(tmp_path / "menu.tif"))
        masks = np.full(state.working_dataset.standardized_shape[1:], 13, dtype=np.uint16)
        state.set_segmentation_result(dict(masks=masks, mask_shape=masks.shape, object_count=1, device="cpu"))
        window.embedding.viewer.reset_view()
        QTest.qWait(120)
        widget = window.embedding.qt_viewer.canvas.native
        QTest.mouseClick(widget, Qt.MouseButton.RightButton, pos=widget.rect().center())
        assert actions(window)["Select Mask"].isEnabled()
        actions(window)["Select Mask"].trigger()
        assert window.mask_selection.selected_mask_id == 13

        planes = np.zeros_like(masks)
        planes[0], planes[-1] = 7, 91
        state.set_segmentation_result(dict(masks=planes, mask_shape=planes.shape, object_count=2, device="cpu"))
        viewer = window.embedding.viewer
        window.viewer_toolbar.set_ndisplay(3)
        viewer.reset_view()
        for direction, expected in [((1, 0, 0), 7), ((-1, 0, 0), 91)]:
            viewer.scene.camera.set_view_direction(direction)
            QTest.qWait(120)
            QTest.mouseClick(widget, Qt.MouseButton.RightButton, pos=widget.rect().center())
            actions(window)["Select Mask"].trigger()
            assert window.mask_selection.selected_mask_id == expected
    finally:
        window.close()
        qapp.processEvents()


def test_raw_context_for_removed_id_and_dirty_preview(review_window):
    window, _ = review_window
    editing_page(window)
    panel = window.geometry_panel
    panel.start()
    click(panel.layer, (0, 1, 1))
    actions(window)["Remove Object"].trigger()
    panel.commit()
    raw = window.segmentation.result_layer
    window.mask_display.show("raw")
    click(raw, (0, 1, 1))
    choices = actions(window)
    assert choices["Select Mask"].isEnabled() and "Start Editing" not in choices
    choices["Select Mask"].trigger()
    click(raw, (0, 5, 6))
    actions(window)["Start Editing"].trigger()
    preview = panel.layer
    preview.brush_size = 1
    preview.paint((0, 0, 0), 2)
    click(preview, (0, 5, 6))
    choices = actions(window)
    assert set(choices) == {"Select Object", "Show Object Properties", "Split Object", "Remove Object",
                            "Add to Merge Selection"}
    panel.discard()
    assert window.mask_context_menu.menu is None
    assert window.mask_context_menu._gesture not in preview.mouse_drag_callbacks


def test_workflow_and_scientific_role_menu(review_window):
    window, masks = review_window
    state = window.project_state
    result = state.segmentation_result.copy()
    second = state.create_session("Other channel", scientific_role="cytoplasmic", channel_index=0)
    state.select_session(second.session_id)
    state.set_segmentation_result(result)
    raw = window.segmentation.result_layer
    for workflow in ("project", "preprocessing", "matching", "export"):
        window.set_workflow(workflow)
        click(raw, (0, 1, 1))
        assert window.mask_context_menu.menu is None
    for workflow in ("segmentation", "quality_control", "quantification"):
        window.set_workflow(workflow)
        click(raw, (0, 1, 1))
        assert set(actions(window)) == {"Select Mask", "Show Mask Properties"}
        menu = window.mask_context_menu.menu
        assert menu.actions()[0].text() == "Cytoplasmic · Mask 1"
        menu.hide()
        assert window.navigation.current_workflow == workflow


def test_filtered_menu_respects_scope_and_review_subpage(retained_second_mask):
    window, _, filtered = retained_second_mask
    window.set_workflow("mask_review")
    window.mask_display.show("raw")
    raw = window.segmentation.result_layer
    click(raw, (0, 1, 1))
    assert "Start Editing" not in actions(window)
    window.mask_context_menu.menu.hide()
    window.mask_display.show("filtered")
    window.navigation.set_subpage("roi_counting", emit=True)
    click(filtered, (0, 5, 6))
    assert "Start Editing" not in actions(window)
    window.mask_context_menu.menu.hide()
    window.navigation.set_subpage("filtering", emit=True)
    click(filtered, (0, 5, 6))
    actions(window)["Start Editing"].trigger()
    assert window.navigation.current_workflow == "proofreading"
    assert window.geometry_panel.selected_id == window.geometry_panel.brush_id == 2
    window.geometry_panel.discard()
