import numpy as np
import pytest

from nuclear_segmentation.filtering_workspace import calculate_filtering_prerequisites
from nuclear_segmentation.geometry_edit import GeometryEdit, effective_masks, validate_history
from nuclear_segmentation.project_state import ProjectState
from test_desktop_normalization import _dataset
from test_mask_review_gui import qapp, retained_second_mask, review_window
from test_native_project import _segment, open_saved, project, save


def test_preview_discard_worker_conflicts_and_2d_3d(review_window, tmp_path):
    window, raw = review_window
    panel = window.geometry_panel
    original = raw.copy()
    assert "analysis_mode" not in window.context_panel.project_page.sections
    panel.start()
    assert not window.segmentation.result_layer.editable
    window.mask_selection.select_mask(panel.layer, 2)
    panel.layer.brush_size = 1
    panel.layer.paint((0, 4, 4), 2)
    panel.layer.paint((0, 1, 1), 0)
    assert panel.layer.data[0, 4, 4] == 2 and panel.layer.data[0, 1, 1] == 0
    assert np.array_equal(raw, original)
    assert not window.save_project(tmp_path / "blocked")
    assert not window.close_project() and not window.filtering.calculate()
    window.viewer_toolbar.set_ndisplay(3)
    assert not panel.layer.editable
    window.viewer_toolbar.set_ndisplay(2)
    assert panel.layer.editable
    panel.discard()
    assert np.array_equal(effective_masks(window.project_state.segmentation_result), original)
    assert window.segmentation.result_layer.visible


def test_merge_split_remove_validation_and_repeated_undo(review_window):
    window, raw = review_window
    state = window.project_state
    start = state.active_session.geometry_revision
    edit = GeometryEdit(state)
    with pytest.raises(ValueError):
        edit.merge([1, 2], 3)
    edit.merge([1, 2], 2)
    edit.commit()
    merged = effective_masks(state.segmentation_result)
    assert np.array_equal(merged, np.where(raw, 2, 0))
    assert state.segmentation_result["edit_history"][0]["parameters"]["result_count"] == np.count_nonzero(raw)
    edit = GeometryEdit(state)
    new = edit.split(2)
    edit.preview[raw == 1] = new
    edit.preview[0, 0, 0] = new
    with pytest.raises(ValueError):
        edit.commit()
    assert effective_masks(state.segmentation_result) is merged
    edit.preview[0, 0, 0] = 0
    edit.commit()
    split = effective_masks(state.segmentation_result)
    assert new > 2 and np.array_equal(split, np.where(raw == 1, new, raw))
    edit = GeometryEdit(state)
    edit.remove(2)
    edit.commit()
    assert np.array_equal(effective_masks(state.segmentation_result), np.where(raw == 1, new, 0))
    for expected in (split, merged, raw):
        GeometryEdit.undo(state)
        assert np.array_equal(effective_masks(state.segmentation_result), expected)
    assert state.active_session.geometry_revision == start + 6
    validate_history(state.segmentation_result, state.active_session)


def test_stale_preview_and_same_ids_across_sessions(tmp_path):
    state = ProjectState()
    state.set_dataset(_dataset(tmp_path / "sessions.tif"))
    raw = np.array([[[0, 1, 2]]], dtype=np.uint16)
    state.set_segmentation_result(dict(masks=raw))
    first = GeometryEdit(state)
    first.remove(1)
    other = state.create_session("MAP2", scientific_role="cytoplasmic", channel_index=None)
    state.select_session(other.session_id)
    state.set_segmentation_result(dict(masks=raw))
    with pytest.raises(ValueError):
        first.commit()
    second = GeometryEdit(state)
    second.remove(2)
    second.commit()
    state.select_session("nuclear")
    first.commit()
    assert effective_masks(state.segmentation_result).tolist() == [[[0, 0, 2]]]
    assert effective_masks(other.segmentation_result).tolist() == [[[0, 1, 0]]]


def test_native_edited_geometry_measurement_and_undo(project, tmp_path):
    window, app, _, _, warnings = project
    state = window.project_state
    raw = _segment(window)
    edit = GeometryEdit(state)
    child = edit.split(90000)
    edit.preview[0][raw[0] == 90000] = child
    expected = edit.preview.copy()
    state.set_quantification_result({"previous": True})
    edit.commit()
    assert state.quantification_result is None and not state.filtering_is_current
    result = calculate_filtering_prerequisites(state, state.segmentation_result["scientific_config"])
    rows = result["properties"]
    for label in (90000, child):
        region = expected == label
        assert rows.loc[label, "size_voxels"] == region.sum()
        assert rows.loc[label, "volume_um3"] == pytest.approx(region.sum() * np.prod(state.voxel_spacing_zyx_um))
        assert rows.loc[label, "z_extent_planes"] == np.any(region, axis=(1, 2)).sum()
        assert rows.loc[label, "mean_intensity_" + state.channels[0].name] == pytest.approx(
            state.working_dataset.data_czyx[0][region].mean())
    window.filtering._calculated(result)
    folder = tmp_path / "edited"
    save(window, app, folder)
    open_saved(window, app, folder)
    assert not warnings
    assert state.filtering_is_current and np.array_equal(effective_masks(state.segmentation_result), expected)
    assert np.array_equal(state.segmentation_result["masks"], raw)
    assert state.segmentation_result["edit_history"][-1]["operation"] == "split"
    GeometryEdit.undo(state)
    assert np.array_equal(effective_masks(state.segmentation_result), raw)


def test_remove_last_object_and_recalculate_zero_density(project):
    window, _, _, _, _ = project
    state = window.project_state
    _segment(window)
    config = state.segmentation_result["scientific_config"]
    window.filtering._calculated(calculate_filtering_prerequisites(state, config))
    window.filtering.accept_roi()
    roi = state.scientific_roi.copy()
    edit = GeometryEdit(state)
    edit.remove(90000)
    edit.commit()
    assert not state.quantification_is_current and not state.scientific_roi_reviewed
    result = calculate_filtering_prerequisites(state, config)
    window.filtering._calculated(result)
    assert np.array_equal(state.scientific_roi, roi)
    assert result["properties"].empty and result["retained_count"] == 0
    window.filtering.accept_roi()
    assert state.quantification_is_current, window.context_panel.quantification_page.status.text()
    assert state.quantification_result["retained_nuclei_per_mm3"] == 0


def test_corrupt_history_and_changed_preview_transform_fail_safely(review_window):
    window, raw = review_window
    panel = window.geometry_panel
    panel.start()
    panel.layer.paint((1, 2, 2), 0)
    panel.layer.translate = (1, 0, 0)
    with pytest.raises(ValueError, match="transform"):
        panel.commit()
    assert window.project_state.segmentation_result["masks"] is raw
    panel.discard()
    edit = GeometryEdit(window.project_state)
    edit.remove(1)
    edit.commit()
    result = window.project_state.segmentation_result
    damaged = dict(result, edit_history=[dict(result["edit_history"][0], after_signature="damaged")])
    with pytest.raises(ValueError):
        validate_history(damaged, window.project_state.active_session)


def test_rendered_geometry_preview(qapp, tmp_path):
    from qtpy.QtCore import QSettings
    from nuclear_segmentation.desktop_window import DesktopWindow

    if qapp.platformName() in {"offscreen", "minimal"}:
        pytest.skip("The rendered canvas requires a window system with OpenGL.")
    window = DesktopWindow(settings=QSettings(str(tmp_path / "render.ini"), QSettings.Format.IniFormat),
                           restore_workspace=False)
    panel = window.geometry_panel
    try:
        window.show()
        window.project_state.set_dataset(_dataset(tmp_path / "render.tif"))
        raw = _segment(window)
        window.set_workflow("mask_review")
        window.navigation.set_subpage("mask_editing", emit=True)
        panel.buttons["start"].click()
        assert panel.busy
        _click_mask(panel.layer, (0, 1, 1))
        panel.split()
        panel.layer.paint((0, 1, 1), panel.brush_id)
        window.viewer_toolbar.set_ndisplay(3)
        qapp.processEvents()
        assert not panel.layer.editable
        window.viewer_toolbar.set_ndisplay(2)
        qapp.processEvents()
        panel.commit()
        edited = window.mask_display.layers["edited"]
        assert edited.visible and not edited.editable
        assert edited.data[0, 1, 1] != raw[0, 1, 1]
        window.mask_display.show("raw")
        assert window.segmentation.result_layer.visible and not edited.visible
    finally:
        if panel.busy:
            panel.discard()
        window.close()
        qapp.processEvents()


def test_edited_cytoplasmic_geometry_drives_worker_filter_and_display(project):
    from test_desktop_normalization import _wait

    window, app, _, _, _ = project
    state = window.project_state
    nuclear_raw = _segment(window)
    cyto = state.create_session("MAP2", scientific_role="cytoplasmic", channel_index=1)
    state.select_session(cyto.session_id)
    window.context_panel.segmentation_page.model_name.setText("test-model")
    raw = _segment(window)
    edit = GeometryEdit(state)
    child = edit.split(90000)
    edit.preview[0][raw[0] == 90000] = child
    expected = edit.preview.copy()
    edit.commit()
    assert window.filtering.calculate()
    _wait(app, lambda: not window.filtering.busy)
    result = state.filtering_result
    assert result["session_id"] == cyto.session_id
    assert result["geometry_revision"] == cyto.geometry_revision
    assert result["retained_ids"] == (90000, child)
    assert result["properties"].loc[child, "size_voxels"] == (expected == child).sum()
    assert np.array_equal(result["filtered_masks"], expected)
    assert state.quantification_result is None
    window.mask_display.show("filtered")
    filtered = window.filtering.filtered_layer
    assert filtered.visible and not window.segmentation.result_layer.visible
    window.viewer_toolbar.activate_tool("review")
    window.mask_selection.select_mask(filtered, child)
    window.mask_display.show("selected")
    assert filtered.show_selected_label and filtered.selected_label == child
    state.select_session("nuclear")
    assert np.array_equal(effective_masks(state.segmentation_result), nuclear_raw)
    state.select_session(cyto.session_id)
    assert window.filtering.filtered_layer.show_selected_label
    assert window.filtering.filtered_layer.selected_label == child


def test_pre_proofreading_checkpoint_with_edited_geometry(project, tmp_path):
    window, app, _, _, warnings = project
    state = window.project_state
    raw = _segment(window)
    window.filtering._calculated(calculate_filtering_prerequisites(state, state.segmentation_result["scientific_config"]))
    # Model a 2D.3 record with raw-derived measurements and a different saved edit.
    state.segmentation_result["edited_masks"][0, 1, 1] = 17
    state.filtering_result.pop("geometry_revision")
    state.filtering_result.pop("session_id")
    folder = tmp_path / "previous"
    save(window, app, folder)
    open_saved(window, app, folder)
    assert not warnings
    assert state.segmentation_is_current and not state.filtering_is_current
    assert not state.scientific_roi_reviewed and not state.quantification_is_current
    assert np.array_equal(state.segmentation_result["masks"], raw)
    assert effective_masks(state.segmentation_result)[0, 1, 1] == 17
    assert state.segmentation_result["edited_object_count"] == 2
    assert "original_manual_review" in state.segmentation_result


def _click_mask(layer, data_position, *, volume=False):
    from types import SimpleNamespace
    from napari.utils.interactions import mouse_press_callbacks
    event = SimpleNamespace(position=layer.data_to_world(data_position), type="mouse_press",
                            view_direction=(1, 0, 0) if volume else None,
                            dims_displayed=(0, 1, 2) if volume else (1, 2))
    mouse_press_callbacks(layer, event)


def test_interactive_selection_layers_and_split(review_window):
    window, raw = review_window
    panel = window.geometry_panel
    viewer = window.embedding.viewer
    image = viewer.add_image(np.zeros_like(raw), name="Reference channel")
    template = viewer.add_labels(raw.copy(), name="Other editable labels")
    panel.start()
    preview = panel.layer
    callback = window.mask_selection._on_mouse_press
    assert callback in preview.mouse_drag_callbacks
    assert panel.selected_id is None and panel.brush_id is None
    assert not window.viewer_toolbar.activate_tool("paint")
    _click_mask(preview, (0, 1, 1))
    assert panel.selected_object.text() == panel.brush_label.text() == "1"
    assert window.context_panel.mask_review_page.mask_id.text().endswith("ID: 1")
    _click_mask(preview, (0, 0, 0))
    assert panel.selected_object.text() == "None" and panel.brush_label.text() == ""
    assert not preview.editable
    _click_mask(preview, (0, 5, 6))
    assert panel.selected_id == panel.brush_id == preview.selected_label == 2
    assert window.viewer_toolbar.activate_tool("paint")
    assert window.viewer_toolbar.activate_tool("review")
    assert panel.selected_id == panel.brush_id == 2
    window.layers_section.header.click()
    expanded = window.layers_section.expanded
    window.layers_section.header.click()
    assert window.layers_section.expanded != expanded
    tree = window.layer_panel
    tree.tree.setCurrentIndex(tree.model.item_for_layer(image).index())
    window.image_display_panel.opacity.setValue(.36)
    assert image.opacity == .36
    comparison = window.segmentation.result_layer
    tree.model.toggle_visibility(comparison)
    tree.tree.setCurrentIndex(tree.model.item_for_layer(comparison).index())
    window.image_display_panel.opacity.setValue(.29)
    assert window.mask_display.preferences["raw"]["opacity"] == .29
    tree.tree.setCurrentIndex(tree.model.item_for_layer(template).index())
    assert viewer.layers.selection.active is template
    assert not template.editable and not window.viewer_toolbar.activate_tool("erase")
    window.image_display_panel.opacity.setValue(.43)
    assert template.opacity == .43
    tree.model.toggle_visibility(preview)
    assert not preview.visible
    panel.buttons["return_preview"].click()
    assert preview.visible and viewer.layers.selection.active is preview
    assert panel.selected_id == 2
    window.viewer_toolbar.set_ndisplay(3)
    assert not window.viewer_toolbar.activate_tool("paint")
    _click_mask(preview, (-1, 1, 1), volume=True)
    assert panel.selected_id == 1
    panel.buttons["split"].click()
    child = panel.edit.parameters["new_id"]
    assert panel.selected_id == 1 and panel.brush_id == preview.selected_label == child
    assert viewer.dims.ndisplay == 2 and str(preview.mode) == "paint"
    preview.selected_label = 0
    assert preview.selected_label == child
    preview.paint((0, 1, 1), child)
    panel.buttons["commit"].click()
    assert template.editable and callback not in preview.mouse_drag_callbacks
    assert effective_masks(window.project_state.segmentation_result)[0, 1, 1] == child
    assert raw[0, 1, 1] == 1


def test_click_merge_nonconsecutive_targets_and_undo(review_window):
    window, original = review_window
    raw = original.copy()
    raw[raw == 2] = 97
    state = window.project_state
    state.set_segmentation_result(dict(state.segmentation_result, masks=raw, maximum_label_id=97))
    panel = window.geometry_panel
    panel.start()
    for position in ((0, 1, 1), (0, 5, 6), (0, 5, 6)):
        _click_mask(panel.layer, position)
        panel.buttons["add_merge"].click()
    assert [panel.merge_list.item(i).text() for i in range(panel.merge_list.count())] == ["1", "97"]
    assert not panel.buttons["merge"].isEnabled()
    panel.merge_target.setCurrentIndex(1)
    panel.buttons["remove_merge"].click()
    assert panel.merge_target.currentIndex() == -1 and panel.merge_list.count() == 1
    panel.buttons["clear_merge"].click()
    assert panel.merge_list.count() == 0
    for position in ((0, 5, 6), (0, 1, 1)):
        _click_mask(panel.layer, position)
        panel.buttons["add_merge"].click()
    panel.merge_target.setCurrentIndex(panel.merge_target.findData(97))
    panel.buttons["merge"].click()
    assert np.array_equal(panel.layer.data, np.where(raw, 97, 0))
    panel.commit()
    panel.undo()
    assert np.array_equal(effective_masks(state.segmentation_result), raw)


@pytest.mark.parametrize("change", ["remove", "replace", "session"])
def test_invalid_preview_can_only_be_discarded(review_window, change):
    window, raw = review_window
    panel = window.geometry_panel
    state = window.project_state
    panel.start()
    layer = panel.layer
    _click_mask(layer, (0, 1, 1))
    if change == "remove":
        panel.viewer.layers.remove(layer)
    elif change == "replace":
        layer.data = raw.copy()
    else:
        other = state.create_session("Other", scientific_role="cytoplasmic", channel_index=0)
        state.select_session(other.session_id)
    with pytest.raises(ValueError):
        panel.commit()
    assert not layer.editable and not panel.buttons["commit"].isEnabled()
    panel.discard()
    assert window.mask_selection._on_mouse_press not in layer.mouse_drag_callbacks
    assert raw[0, 1, 1] == 1


def test_filtered_selection_scope_and_hidden_geometry(retained_second_mask):
    window, raw, filtered = retained_second_mask
    state = window.project_state
    panel = window.geometry_panel
    window.mask_display.show("filtered")
    window.viewer_toolbar.activate_tool("review")
    window.mask_selection.select_mask(filtered, 2)
    assert panel.selected_object.text() == "2"
    revision = state.session_revision()
    panel.start()
    assert panel.edit.filtered and panel.selected_id == panel.brush_id == 2
    preview = panel.layer.data
    np.testing.assert_array_equal(preview, filtered.data)
    hidden = raw == 1
    assert hidden.any() and not preview[hidden].any()
    for pos, value in [((0, 1, 1), 2), ((0, 0, 0), 1)]:
        preview[pos] = value
        panel.run("commit")
        assert "excluded object" in panel.status.text()
        assert state.session_revision() == revision
        panel.scope.setCurrentIndex(1)
        assert panel.edit.filtered and panel.scope.currentIndex() == 0
        assert preview[pos] == value
        preview[pos] = 0
    preview[0, 0, 0] = 2
    panel.scope.setCurrentIndex(1)
    np.testing.assert_array_equal(preview[hidden], raw[hidden])
    preview[0, 1, 1] = 0
    panel.scope.setCurrentIndex(0)
    assert not panel.edit.filtered and panel.scope.currentIndex() == 1
    preview[0, 1, 1] = 1
    panel.scope.setCurrentIndex(0)
    assert panel.edit.filtered and not preview[hidden].any() and preview[0, 0, 0] == 2
    panel.commit()
    expected = raw.copy()
    expected[0, 0, 0] = 2
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), expected)
    assert state.segmentation_result["edit_history"][-1]["affected_ids"] == [2]
    assert not state.filtering_is_current and panel.scope.currentIndex() == 1
    assert not panel.scope.model().item(0).isEnabled()
    committed = window.mask_display.layers["edited"]
    window.mask_display.show("edited")
    window.viewer_toolbar.activate_tool("review")
    window.mask_selection.select_mask(committed, 1)
    panel.start()
    assert panel.selected_id == panel.brush_id == 1
    panel.discard()
    panel.undo()
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), raw)


def test_collapsing_editor_preserves_transaction_and_merge(review_window, qapp):
    from qtpy.QtWidgets import QScrollArea

    window, raw = review_window
    window.set_workflow("mask_review")
    window.navigation.set_subpage("mask_editing", emit=True)
    panel = window.geometry_panel
    window.resize(800, 520)
    window.show()
    qapp.processEvents()
    assert not panel.merge_section.expanded and panel.merge_list.isHidden()
    panel.start()
    preview = panel.layer
    panel.merge_section.header.click()
    for pos in ((0, 1, 1), (0, 5, 6)):
        _click_mask(preview, pos)
        panel.add_merge()
    panel.merge_target.setCurrentIndex(panel.merge_target.findData(1))
    panel.merge_section.header.click()
    panel.header.click()
    assert panel.content.isHidden() and panel.status.isVisible()
    assert panel.layer is preview and panel.selected_id == panel.brush_id == 2
    assert panel.merge_list.count() == 2 and panel.merge_target.currentData() == 1
    np.testing.assert_array_equal(preview.data, raw)
    panel.header.click()
    panel.merge_section.header.click()
    scroll = panel.parentWidget()
    while scroll is not None and not isinstance(scroll, QScrollArea):
        scroll = scroll.parentWidget()
    assert scroll is not None
    scroll.ensureWidgetVisible(panel.buttons["merge"])
    qapp.processEvents()
    assert scroll.verticalScrollBar().maximum() > 0
    assert panel.buttons["merge"].isVisible()
    assert panel.buttons["merge"].width() >= panel.buttons["merge"].minimumSizeHint().width()
    panel.merge()
    panel.discard()


def test_filtered_commit_resumes_with_excluded_geometry_and_undo(project, tmp_path):
    window, app, _, _, warnings = project
    state = window.project_state
    raw = _segment(window)
    first = GeometryEdit(state)
    child = first.split(90000)
    first.preview[0][raw[0] == 90000] = child
    first.commit()
    before = effective_masks(state.segmentation_result)
    record = calculate_filtering_prerequisites(state, state.segmentation_result["scientific_config"])
    window.filtering._calculated(dict(record, retained_ids=(child,), retained_count=1,
                                      filtered_masks=np.where(before == child, before, 0)))
    edit = GeometryEdit(state, filtered=True)
    edit.remove(child)
    edit.commit()
    folder = tmp_path / "filtered-edit"
    save(window, app, folder)
    open_saved(window, app, folder)
    assert not warnings
    np.testing.assert_array_equal(state.segmentation_result["masks"], raw)
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), np.where(before == child, 0, before))
    assert state.segmentation_result["edit_history"][-1]["removed_ids"] == [child]
    GeometryEdit.undo(state)
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), before)


def test_empty_and_outdated_filtering_never_removes_objects(retained_second_mask):
    window, raw, _ = retained_second_mask
    state = window.project_state
    panel = window.geometry_panel
    record = dict(state.filtering_result, retained_ids=(), retained_count=0,
                  filtered_masks=np.zeros_like(raw))
    window.filtering._calculated(record)
    panel.start()
    assert panel.edit.filtered and not panel.layer.data.any() and panel.selected_id is None
    panel.run("commit")
    assert "no changes" in panel.status.text() and panel.busy
    panel.scope.setCurrentIndex(1)
    np.testing.assert_array_equal(panel.layer.data, raw)
    panel.scope.setCurrentIndex(0)
    state.invalidate_filtering()
    assert not panel.buttons["commit"].isEnabled()
    panel.discard()
    panel.start()
    assert not panel.edit.filtered and panel.selected_id is None
    np.testing.assert_array_equal(panel.layer.data, raw)
    panel.discard()
