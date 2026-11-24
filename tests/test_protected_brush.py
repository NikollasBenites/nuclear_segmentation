"""Filtered proofreading at the napari Labels write boundary."""
import numpy as np
import pytest

from nuclear_segmentation.filtering_workspace import calculate_filtering_prerequisites
from nuclear_segmentation.geometry_edit import effective_masks, validate_history
from test_geometry_edit import _click_mask
from test_mask_review_gui import qapp, review_window


def adjacent_objects(window, shape, cytoplasmic):
    state = window.project_state
    if cytoplasmic:
        session = state.create_session("Cytoplasm", scientific_role="cytoplasmic", channel_index=state.channels[-1].index)
        state.select_session(session.session_id)
        window.context_panel.segmentation_page.model_name.setText("test-model")
    config = window.context_panel.segmentation_page.scientific_config()
    raw = np.zeros(shape, dtype=np.uint16)
    raw[:, 2:5, 2:5] = 49
    raw[:, 2:5, 5:8] = 73
    raw[:, 5, 4] = 81
    state.set_segmentation_result(dict(masks=raw, object_count=3, mask_shape=shape,
                                       device="cpu", scientific_config=config))
    measured = calculate_filtering_prerequisites(state, config)
    kept = np.where(raw == 73, 0, raw)
    window.filtering._calculated(dict(measured, retained_ids=(49, 81), retained_count=2, filtered_masks=kept))
    return raw, kept


@pytest.mark.parametrize("cytoplasmic", [True, False])
@pytest.mark.parametrize("provisional", [False, True])
def test_filtered_brush_history_and_save(review_window, cytoplasmic, provisional, monkeypatch):
    window, masks = review_window
    state, panel = window.project_state, window.geometry_panel
    raw, _ = adjacent_objects(window, masks.shape, cytoplasmic)
    working = window.filtering.filtered_layer
    if provisional:
        panel.start()
        _click_mask(panel.layer, (0, 2, 4))
        panel.layer.brush_size = 1
        panel.layer.paint((1, 0, 0), 49)
        panel.commit()
        assert state.provisional_ids == [49, 81] and not state.filtering_is_current
    before = effective_masks(state.segmentation_result).copy()
    panel.start()
    layer = panel.layer
    _click_mask(layer, (0, 2, 4))
    assert panel.brush_id == 49 and panel.edit.filter_current
    start = layer.data.copy()
    np.testing.assert_array_equal(start, np.where(before == 73, 0, before))
    layer.brush_size = 3
    def unexpected_scan(*args):
        pytest.fail("A brush stamp traversed the complete working geometry")

    with monkeypatch.context() as patch:
        for method in ("_prepare_filtered_scope", "_visible_planes", "has_changes"):
            patch.setattr(panel.edit, method, unexpected_scan)
        with layer.block_history():
            layer.paint((0, 3, 6), 49)
            layer.paint((0, 2, 4), 49)
            layer.paint((0, 3, 6), 49)
    changed = layer.data.copy()
    assert np.all(changed[0, 1, 3:6] == 49) and not changed[raw == 73].any()
    assert "Protected excluded-object voxels were skipped" in window.statusBar().currentMessage()
    step, = panel.edit.steps
    np.testing.assert_array_equal(step["indexes"], np.flatnonzero(changed != start))
    assert len(layer._undo_history) == len(layer._undo_history[-1]) == 1
    panel.undo_preview()
    np.testing.assert_array_equal(layer.data, start)

    layer.brush_size = 1
    with layer.block_history():
        layer.paint((0, 2, 5), 49)
        layer.data_setitem((np.array([0]), np.array([2]), np.array([5])), 49)
    assert not layer._undo_history and len(layer._redo_history) == 1
    assert not panel.edit.steps and len(panel.edit.redo_steps) == 1
    panel.redo_preview()
    np.testing.assert_array_equal(layer.data, changed)

    # A mixed indexed write erases a retained voxel, skips the hidden voxel,
    # and rejects an excluded destination ID on genuine background.
    coords = (np.array([0, 0, 0]), np.array([2, 2, 0]), np.array([4, 5, 1]))
    layer.data_setitem(coords, [0, 0, 73])
    assert list(layer.data[coords]) == [0, 0, 0]
    assert panel.edit.steps[-1]["indexes"].tolist() == [np.ravel_multi_index((0, 2, 4), raw.shape)]
    layer.paint((0, 0, 0), 73)
    assert len(panel.edit.steps) == 2 and "brush ID is excluded" in window.statusBar().currentMessage()
    panel.undo_preview()
    np.testing.assert_array_equal(layer.data, changed)
    panel.redo_preview()
    layer.data_setitem(coords, [49, 49, 49])
    assert list(layer.data[coords]) == [49, 0, 49]
    np.testing.assert_array_equal(panel.edit.steps[-1]["indexes"], np.sort(np.ravel_multi_index(coords, raw.shape)[[0, 2]]))

    panel.brush_label.setText("73")
    panel._brush_entered()
    assert panel.brush_id is None and not layer.editable
    assert "ID is excluded" in panel.status.text()
    panel.brush_label.setText("49")
    panel._brush_entered()
    assert panel.brush_id == 49 and layer.editable
    # Painting over a different retained ID still follows napari's normal rules.
    layer.paint((0, 5, 4), 49)
    assert raw[0, 5, 4] == 81 and layer.data[0, 5, 4] == 49
    expected = np.where(raw == 73, raw, layer.data)
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), before)
    panel.commit()
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), expected)
    np.testing.assert_array_equal(state.segmentation_result["masks"], raw)
    assert window.filtering.filtered_layer is working and not working.data[raw == 73].any()
    assert "_paint_region_with_mask" not in vars(layer) and "data_setitem" not in vars(layer)
    validate_history(state.segmentation_result, state.active_session)


@pytest.mark.parametrize("order", [(1, 0, 2), (2, 1, 0)])
def test_orthogonal_erase_and_split_lifecycle(review_window, order):
    window, masks = review_window
    state, panel = window.project_state, window.geometry_panel
    raw, kept = adjacent_objects(window, masks.shape, False)
    panel.start()
    layer = panel.layer
    _click_mask(layer, (1, 2, 4))
    panel.viewer.dims.order = order
    layer.brush_size = 3
    # XZ crosses the hidden boundary. On YZ the hidden X plane is entirely protected.
    layer.paint((1, 2, 4 if order[0] == 1 else 5), 0)
    if order[0] == 1:
        assert panel.edit.steps and np.any(layer.data[raw == 49] == 0)
        panel.undo_preview()
    else:
        assert not panel.edit.steps
    np.testing.assert_array_equal(layer.data, kept)
    panel.viewer.dims.order = (0, 1, 2)
    panel.split()
    layer.paint((1, 2, 4), panel.brush_id)
    panel.cancel_operation()
    panel.split()
    child = panel.brush_id
    layer.paint((1, 2, 4), child)
    panel.apply_operation()
    layer.brush_size = 3
    panel.toggle_pause()
    layer.paint((0, 2, 4), child)
    assert len(panel.edit.steps) == 1
    panel.toggle_pause()
    layer.paint((0, 2, 4), child)
    assert len(panel.edit.steps) == 2 and layer.data[0, 1, 4] == child
    assert not layer.data[raw == 73].any()
    panel.commit()
    assert np.all(effective_masks(state.segmentation_result)[raw == 73] == 73)
    validate_history(state.segmentation_result, state.active_session)


def test_scope_switch_and_inconsistent_selection(review_window):
    window, masks = review_window
    state, panel = window.project_state, window.geometry_panel
    raw, kept = adjacent_objects(window, masks.shape, False)
    panel.start()
    layer = panel.layer
    _click_mask(layer, (0, 2, 4))
    panel.scope.setCurrentIndex(1)
    assert "data_setitem" not in vars(layer) and layer.data[0, 2, 5] == 73
    layer.brush_size = 1
    layer.paint((0, 2, 5), 49)
    assert layer.data[0, 2, 5] == 49
    panel.undo_preview()
    panel.scope.setCurrentIndex(0)
    layer.paint((0, 2, 5), 49)
    assert not panel.edit.steps and layer.data[0, 2, 5] == 0
    panel.discard()
    assert "data_setitem" not in vars(layer)
    inconsistent = kept.copy()
    inconsistent[0, 2, 5] = 73
    state.set_filtering_result(dict(state.filtering_result, filtered_masks=inconsistent))
    with pytest.raises(ValueError, match="Retained selection disagrees"):
        panel.start()
    assert not panel.busy


def test_rendered_canvas_clips_before_history_and_texture(qapp, tmp_path):
    from dataclasses import replace

    from qtpy.QtCore import QSettings, Qt
    from qtpy.QtGui import QKeySequence
    from qtpy.QtTest import QTest

    from nuclear_segmentation.desktop_window import DesktopWindow
    from test_desktop_normalization import _dataset

    if qapp.platformName() in {"offscreen", "minimal"}:
        pytest.skip("Physical Qt mouse input requires an OpenGL canvas.")
    window = DesktopWindow(settings=QSettings(str(tmp_path / "canvas.ini"), QSettings.Format.IniFormat),
                           restore_workspace=False)
    panel = window.geometry_panel
    try:
        window.show()
        state = window.project_state
        dataset = replace(_dataset(tmp_path / "canvas.tif"), voxel_spacing_zyx_um=(2., .5, .5))
        state.set_dataset(dataset)
        raw, kept = adjacent_objects(window, dataset.standardized_shape[1:], True)
        window.set_workflow("proofreading")
        panel.start()
        layer = panel.layer
        _click_mask(layer, (0, 2, 4))
        panel.buttons["paint"].click()
        layer.brush_size = 3
        viewer = panel.viewer
        viewer.dims.set_current_step(0, 0)
        viewer.reset_view()
        viewer.scene.camera.center = layer.data_to_world((0, 2, 4))
        widget = window.embedding.qt_viewer.canvas.native
        widget.setFocus()
        QTest.qWait(120)
        center = widget.rect().center()
        pos = viewer.canvas_to_world((center.y(), center.x()))
        np.testing.assert_array_equal(np.rint(layer.world_to_data(pos)), (0, 2, 4))
        texture = layer._slice.image.view.copy()
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=center)
        qapp.processEvents()
        expected = kept.copy()
        expected[0, 1, 3:6] = 49
        np.testing.assert_array_equal(layer.data, expected)
        texture[1, 3:6] = texture[2, 4]
        np.testing.assert_array_equal(layer._slice.image.view, texture)
        step, = panel.edit.steps
        np.testing.assert_array_equal(step["indexes"], np.flatnonzero(expected != kept))
        QTest.keyClick(widget, Qt.Key.Key_Escape)
        QTest.keySequence(widget, QKeySequence("Ctrl+Z"))
        np.testing.assert_array_equal(layer.data, kept)
        QTest.keySequence(widget, QKeySequence("Ctrl+Shift+Z"))
        panel.commit()
        np.testing.assert_array_equal(effective_masks(state.segmentation_result), np.where(raw == 73, raw, expected))
    finally:
        if panel.busy:
            panel.discard()
        window.close()
        qapp.processEvents()
