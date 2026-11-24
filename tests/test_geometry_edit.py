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
    window.filtering.filtered_layer.opacity = .58
    # A pre-unification project can still carry a previous provisional layer preference.
    state.active_session.display_state["retained"] = {"opacity": .71}
    folder = tmp_path / "edited"
    save(window, app, folder)
    open_saved(window, app, folder)
    assert not warnings
    assert window.filtering.filtered_layer.opacity == pytest.approx(.58)
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
        panel.layer.paint((0, 1, 2), panel.brush_id)
        from qtpy.QtTest import QTest
        from qtpy.QtGui import QKeySequence
        canvas = window.embedding.qt_viewer.canvas.native
        canvas.setFocus()
        panel.set_expanded(False)
        QTest.keySequence(canvas, QKeySequence("Ctrl+Z"))
        assert panel.layer.data[0, 1, 1] == panel.brush_id
        assert panel.layer.data[0, 1, 2] == raw[0, 1, 2]
        panel.toggle_pause()
        QTest.keySequence(canvas, QKeySequence("Ctrl+Shift+Z"))
        assert panel.layer.data[0, 1, 2] == panel.brush_id and not panel.layer.editable
        panel.toggle_pause()
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
        assert window.segmentation.result_layer.visible and edited not in panel.viewer.layers

        state = window.project_state
        child = int(edited.data[0, 1, 1])
        record = calculate_filtering_prerequisites(state, state.segmentation_result["scientific_config"])
        source = np.where(edited.data == child, edited.data, 0)
        window.filtering._calculated(dict(record, retained_ids=(child,), retained_count=1,
                                         filtered_masks=source))
        panel.start()
        _click_mask(panel.layer, (0, 1, 1))
        panel.layer.brush_size = 1
        panel.layer.paint((0, 0, 0), child)
        panel.buttons["commit"].click()
        for dimensions in (2, 3):
            window.viewer_toolbar.set_ndisplay(dimensions)
            qapp.processEvents()
            layers = window.mask_display.layers
            assert {role for role, layer in layers.items() if layer.visible} == {"filtered"}
            assert layers["filtered"].data[0, 0, 0] == child
            panel.buttons["show_before"].click()
            qapp.processEvents()
            assert layers["before"].visible and layers["before"].data[0, 0, 0] == 0
            assert not layers["filtered"].visible and not layers["raw"].visible
            panel.buttons["show_retained"].click()
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
    layer.brush_size = 1
    layer.paint((0, 0, 0), 1)
    assert panel.buttons["undo_preview"].isEnabled()
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
    for action in ("undo_preview", "redo_preview"):
        assert not panel.buttons[action].isEnabled()
        with pytest.raises(ValueError, match="changed"):
            getattr(panel, action)()
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
    assert not state.filtering_is_current and panel.scope.currentIndex() == 0
    assert panel.scope.model().item(0).isEnabled()
    assert "edited" not in window.mask_display.layers
    panel.show_edited()
    panel.scope.setCurrentIndex(1)
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


@pytest.mark.parametrize("old_v2", [False, True])
def test_filtered_commit_resumes_with_excluded_geometry_and_undo(project, tmp_path, monkeypatch, old_v2):
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
    retained = window.mask_display.layers["filtered"]
    assert retained.visible and not retained.data.any()
    retained.opacity = .39
    window.geometry_panel.buttons["show_before"].click()
    if old_v2:
        from nuclear_segmentation import desktop_window
        snapshot_state = desktop_window.snapshot_state

        def previous_format(project_state):
            snapshot = snapshot_state(project_state)
            saved = snapshot["sessions"]["nuclear"]
            saved.pop("measurement_error")
            saved["display_state"] = dict(saved["display_state"], mode="retained",
                                          pending_geometry_revision=saved.pop("pending_geometry_revision"))
            saved["display_state"]["retained"] = saved["display_state"].pop("filtered")
            saved["display_state"]["filtered"] = {"opacity": .71}
            geometry = dict(saved["segmentation_result"])
            history = [dict(item) for item in geometry["edit_history"]]
            history[-1].pop("previous_reviewed_ids")
            saved["segmentation_result"] = dict(geometry, edit_history=history)
            return snapshot
        monkeypatch.setattr(desktop_window, "snapshot_state", previous_format)
    folder = tmp_path / "filtered-edit"
    save(window, app, folder)
    open_saved(window, app, folder)
    assert not warnings
    np.testing.assert_array_equal(state.segmentation_result["masks"], raw)
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), np.where(before == child, 0, before))
    retained = window.mask_display.layers["filtered"]
    assert state.provisional_ids == [] and not state.filtering_is_current
    assert state.filtering_result is None and state.quantification_result is None
    if old_v2:
        assert retained.visible and "before" not in window.mask_display.layers
        window.geometry_panel.show_before()
    snapshot = window.mask_display.layers["before"]
    assert snapshot.visible and not retained.visible and not snapshot.editable
    np.testing.assert_array_equal(snapshot.data, np.where(before == child, before, 0))
    window.geometry_panel.buttons["show_retained"].click()
    assert retained.visible and not retained.data.any() and retained.opacity == pytest.approx(.39)
    assert state.active_session.filter_controls == record["settings"]
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
    assert window.filtering.filtered_layer.visible and not window.segmentation.result_layer.visible
    assert window.filtering.working_status == "Recalculation required"
    panel.start()
    assert not panel.edit.filtered and panel.selected_id is None
    np.testing.assert_array_equal(panel.layer.data, raw)
    panel.discard()


def test_editing_controls_brush_planes_and_paused_split(review_window, tmp_path):
    window, raw = review_window
    panel = window.geometry_panel
    state = window.project_state
    toolbar = window.viewer_toolbar
    assert panel.transaction_controls.isHidden() and not panel.buttons["start"].isHidden()
    assert panel.buttons["undo"].isHidden()
    panel.set_expanded(False)
    panel.start()
    preview = panel.layer
    assert panel.expanded and not panel.transaction_controls.isHidden()
    assert panel.brush_size.value() == preview.brush_size == 3
    assert "1.50 × 1.50 µm (nominal XY)" in panel.brush_readout.text()
    _click_mask(preview, (0, 1, 1))
    panel.buttons["paint"].click()
    assert toolbar.buttons["paint"].isChecked() and str(preview.mode) == "paint"
    toolbar.activate_tool("erase")
    assert panel.buttons["erase"].isChecked()
    panel.brush_slider.setValue(1)
    assert panel.brush_size.value() == preview.brush_size == 1
    preview.brush_size = 52
    assert panel.brush_size.value() == panel.brush_slider.value() == 52
    panel.brush_size.setValue(3)
    for order, plane in [((1, 0, 2), "XZ"), ((2, 0, 1), "YZ")]:
        panel.viewer.dims.order = order
        assert f"1.50 × 6.00 µm (nominal {plane})" in panel.brush_readout.text()
        before_stroke = preview.data.copy()
        preview.paint((2, 4, 4), 1)
        changed = np.nonzero(preview.data != before_stroke)
        assert changed[0].size and set(changed[order[0]]) == {4}
        preview.undo()
    panel.viewer.dims.order = (0, 1, 2)
    calibration = state.calibration_source
    state.calibration_source = None
    preview.brush_size = 1
    assert "physical size unknown" in panel.brush_readout.text()
    state.calibration_source = calibration
    panel.buttons["review"].click()
    assert panel.selected_id == panel.brush_id == 1
    panel.split()
    child = panel.brush_id
    preview.paint((0, 1, 1), child)
    expected = preview.data.copy()
    owner = state.session_revision()
    panel.buttons["toggle_pause"].click()
    assert panel.paused and not preview.editable and "Paused" in panel.status.text()
    preview.brush_size = 9
    assert preview.brush_size == 1
    with pytest.raises(ValueError):
        panel.edit.set_filtered(True)
    assert not panel.buttons["split"].isEnabled() and not toolbar.activate_tool("erase")
    assert not window.save_project(tmp_path / "busy")
    assert not window.close_project(confirm=False) and not window.filtering.calculate()
    panel.set_expanded(False)
    panel.viewer.layers.selection.active = window.segmentation.result_layer
    panel.toggle_pause()
    assert not panel.paused and panel.layer is preview and preview.editable
    assert panel.selected_id == 1 and panel.brush_id == child == preview.selected_label
    assert panel.brush_size.value() == 1 and toolbar.buttons["review"].isChecked()
    assert state.session_revision() == owner
    np.testing.assert_array_equal(preview.data, expected)
    panel.commit()
    assert panel.transaction_controls.isHidden() and not panel.buttons["undo"].isHidden()
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), expected)


@pytest.mark.parametrize("choice", ["Cancel", "Discard Changes", "Save Changes"])
def test_stop_dialog_from_pause(review_window, choice):
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QApplication

    window, raw = review_window
    state = window.project_state
    panel = window.geometry_panel
    initial = state.session_revision()
    panel.start()
    panel.stop()
    assert not panel.busy and state.session_revision() == initial
    panel.start()
    preview = panel.layer
    preview.data[0, 0, 0] = 2
    panel.toggle_pause()

    def respond():
        dialog = QApplication.activeModalWidget()
        assert dialog.windowTitle() == "Stop Editing?"
        next(button for button in dialog.buttons() if button.text().replace("&", "") == choice).click()

    QTimer.singleShot(0, respond)
    panel.buttons["stop"].click()
    if choice == "Cancel":
        assert panel.paused and panel.layer is preview and preview.data[0, 0, 0] == 2
        panel.discard()
    assert not panel.busy
    assert state.active_session.geometry_revision == initial.geometry_revision + (choice == "Save Changes")
    assert effective_masks(state.segmentation_result)[0, 0, 0] == (2 if choice == "Save Changes" else 0)
    assert raw[0, 0, 0] == 0


def test_filtered_save_split_recalculate_same_thresholds(retained_second_mask, qapp):
    from test_desktop_normalization import _wait

    window, raw, filtered = retained_second_mask
    state = window.project_state
    panel = window.geometry_panel
    settings = state.filtering_result["settings"]
    panel.start()
    _click_mask(panel.layer, (0, 5, 6))
    panel.split()
    child = panel.brush_id
    panel.layer.paint((0, 5, 6), child)
    expected = panel.edit.preview.copy()
    panel.commit()
    retained = window.mask_display.layers["filtered"]
    assert "edited" not in window.mask_display.layers and "before" not in window.mask_display.layers
    assert retained.visible and not retained.editable
    assert retained is filtered and retained.name == "Live filtered masks"
    assert window.mask_selection.selected_layer is retained and window.mask_selection.selected_mask_id == 2
    np.testing.assert_array_equal(retained.data, expected)
    complete = effective_masks(state.segmentation_result)
    assert complete[0, 1, 1] == 1 and complete[0, 5, 6] == child
    assert raw[0, 5, 6] == 2 and state.filtering_result is None
    assert state.active_session.filter_controls == settings and state.active_session.filter_controls is not settings
    assert state.segmentation_result["edit_history"][-1]["reviewed_ids"] == [2, child]
    assert window.filtering.calculate()
    _wait(qapp, lambda: not window.filtering.busy)
    assert state.filtering_is_current and state.filtering_result["settings"] == settings
    assert "before" not in window.mask_display.layers
    assert retained is window.filtering.filtered_layer and retained.visible
    assert window.mask_display.preferences["mode"] == "filtered"
    assert set(state.filtering_result["properties"].index) == {1, 2, child}


def test_filtered_merge_retains_target_and_isolates_sessions(retained_second_mask):
    window, raw, _ = retained_second_mask
    state = window.project_state
    # Retain IDs 1 and 2 while excluding a third label on the same grid.
    masks = raw.copy()
    masks[:, 0, 0] = 83
    result = dict(state.segmentation_result, masks=masks, object_count=3)
    filters = dict(state.filtering_result, retained_ids=(1, 2), retained_count=2,
                   filtered_masks=raw.copy())
    state.set_segmentation_result(result)
    window.filtering._calculated(filters)
    panel = window.geometry_panel
    panel.start()
    for pos in ((0, 1, 1), (0, 5, 6)):
        _click_mask(panel.layer, pos)
        panel.add_merge()
    panel.merge_target.setCurrentIndex(panel.merge_target.findData(2))
    panel.merge()
    panel.toggle_pause()
    panel.toggle_pause()
    panel.commit()
    retained = window.mask_display.layers["filtered"]
    np.testing.assert_array_equal(retained.data, np.where(raw, 2, 0))
    assert state.segmentation_result["edit_history"][-1]["reviewed_ids"] == [2]
    assert (effective_masks(state.segmentation_result)[:, 0, 0] == 83).all()
    retained.opacity = .42
    other = state.create_session("Second channel", scientific_role="cytoplasmic", channel_index=0)
    state.select_session(other.session_id)
    state.set_segmentation_result(result)
    assert retained not in panel.viewer.layers and "retained" not in window.mask_display.layers
    panel.start()
    _click_mask(panel.layer, (0, 5, 6))
    panel.remove()
    panel.commit()
    assert window.mask_display.layers["edited"].visible and "retained" not in window.mask_display.layers
    state.select_session("nuclear")
    restored = window.mask_display.layers["filtered"]
    assert restored.visible and restored.opacity == pytest.approx(.42)
    np.testing.assert_array_equal(restored.data, np.where(raw, 2, 0))
    panel.undo()
    assert state.provisional_ids == [1, 2]
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), masks)


def test_changed_filter_definition_requires_review(retained_second_mask, qapp):
    from test_desktop_normalization import _wait

    window, raw, _ = retained_second_mask
    state = window.project_state
    panel = window.geometry_panel
    panel.start()
    panel.edit.preview[0, 0, 0] = 2
    panel.commit()
    channel = next(iter(state.active_session.filter_controls["intensity_filters"]))
    # Simulate a saved threshold defined on a different intensity measurement.
    state.active_session.filter_controls["intensity_filters"][channel]["column"] = "previous_measurement"
    assert window.filtering.calculate()
    _wait(qapp, lambda: not window.filtering.busy)
    review = window.context_panel.mask_review_page
    assert not state.filtering_is_current and state.quantification_result is None
    assert channel in review.measurement_status.text() and "incompatible" in review.measurement_status.text()
    assert window.mask_display.layers["filtered"].visible and "before" not in window.mask_display.layers
    assert window.filtering.calculate()
    _wait(qapp, lambda: not window.filtering.busy)
    assert state.filtering_is_current and window.filtering.filtered_layer.visible
    assert "before" not in window.mask_display.layers and "retained" not in window.mask_display.layers


def test_thresholds_survive_narrower_measured_range(retained_second_mask, qapp):
    from test_desktop_normalization import _wait

    window, _, _ = retained_second_mask
    state = window.project_state
    review = window.context_panel.mask_review_page
    settings = review.filter_settings()
    controls = review.intensity_control_settings()
    panel = window.geometry_panel
    panel.start()
    _click_mask(panel.layer, (0, 5, 6))
    panel.remove()
    panel.commit()
    assert not window.mask_display.layers["filtered"].data.any()
    assert window.filtering.calculate()
    _wait(qapp, lambda: not window.filtering.busy)
    assert state.filtering_is_current and state.filtering_result["retained_count"] == 0
    assert state.filtering_result["intensity_definitions"][0]["maximum"] < controls[0][2]
    assert review.filter_settings() == settings
    assert review.intensity_control_settings() == controls


@pytest.mark.parametrize("edit_cytoplasmic", [True, False])
def test_filtered_before_and_after_are_session_owned(retained_second_mask, edit_cytoplasmic, qapp):
    from test_desktop_normalization import _wait

    window, nuclear_raw, _ = retained_second_mask
    state = window.project_state
    display = window.mask_display
    nuclear = state.active_session
    config = state.segmentation_result["scientific_config"]
    display.show("filtered")
    cyto = state.create_session("MAP2", scientific_role="cytoplasmic", channel_index=0)
    state.select_session(cyto.session_id)
    # Both sessions use labels 1 and 2, with opposite identities in the bright region.
    cyto_raw = np.where(nuclear_raw, 3 - nuclear_raw, 0).astype(nuclear_raw.dtype)
    state.set_segmentation_result(dict(masks=cyto_raw, mask_shape=cyto_raw.shape, object_count=2,
                                       device="cpu", scientific_config=config))
    assert window.filtering.calculate()
    _wait(qapp, lambda: not window.filtering.busy)
    review = window.context_panel.mask_review_page
    review.intensity_master.setChecked(True)
    _, enabled, lower, _, _ = review.intensity_controls[0]
    enabled.setChecked(True)
    lower.editor.setValue(lower.value() + 1)
    _wait(qapp, lambda: not review._filter_timer.isActive())
    assert cyto.filtering_result["retained_ids"] == (1,)
    display.show("filtered")
    active, other = (cyto, nuclear) if edit_cytoplasmic else (nuclear, cyto)
    state.select_session(active.session_id)
    source = active.filtering_result["filtered_masks"]
    other_filter = other.filtering_result
    other_revision = other.geometry_revision
    raw = active.segmentation_result["masks"].copy()
    kept, = active.filtering_result["retained_ids"]
    excluded = (raw != kept) & (raw != 0)
    assert excluded.any()
    panel = window.geometry_panel
    panel.start()
    _click_mask(panel.layer, (0, 5, 6))
    panel.layer.brush_size = 1
    panel.layer.paint((0, 5, 6), 0)
    panel.layer.paint((0, 0, 0), kept)
    expected = source.copy()
    expected[0, 5, 6] = 0
    expected[0, 0, 0] = kept
    panel.buttons["commit"].click()
    qapp.processEvents()
    assert "before" not in display.layers and "edited" not in display.layers
    assert {role for role, layer in display.layers.items() if layer.visible} == {"filtered"}
    np.testing.assert_array_equal(display.layers["filtered"].data, expected)
    np.testing.assert_array_equal(effective_masks(active.segmentation_result)[excluded], raw[excluded])
    np.testing.assert_array_equal(display.layers["raw"].data, raw)
    assert active.filtering_result is None and active.quantification_result is None
    panel.buttons["show_before"].click()
    snapshot = display.layers["before"]
    np.testing.assert_array_equal(snapshot.data, source)
    assert not snapshot.editable and not snapshot.data.flags.writeable
    assert snapshot.metadata["source_geometry_revision"] == active.geometry_revision - 1
    assert snapshot.metadata["mask_type"] == active.scientific_role
    state.select_session(other.session_id)
    assert other.filtering_result is other_filter and other.filtering_is_current
    assert other.geometry_revision == other_revision and display.layers["filtered"].visible
    assert display.layers["filtered"].data is other_filter["filtered_masks"]
    assert "before" not in display.layers and "retained" not in display.layers
    state.select_session(active.session_id)
    assert display.layers["before"].visible and "edited" not in display.layers
    np.testing.assert_array_equal(display.layers["before"].data, source)
    panel.undo()
    assert not active.filtering_is_current and "before" not in display.layers
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), raw)


def test_repeated_provisional_edits_keep_scope_and_allocate_unused_ids(retained_second_mask):
    window, raw, working = retained_second_mask
    state, panel = window.project_state, window.geometry_panel
    original = raw.copy()
    working.opacity = .38
    tree = window.layer_panel.model
    primary = tree.item_for_layer(working).parent()
    assert primary.rowCount() == 2
    assert tree.item_for_layer(window.filtering.roi_layer).parent().text() == "Annotations & Overlays"
    index = panel.viewer.layers.index(working)
    assert not panel.auto_recalculate.isChecked() and not panel.advanced.expanded
    for position, expected_child in [((0, 5, 6), 3), ((1, 5, 6), 4)]:
        panel.start()
        assert panel.edit.filtered and panel.edit.next_id == expected_child
        assert not panel.edit.preview[raw == 1].any()
        panel.edit.preview[0, 1, 1] = 2
        with pytest.raises(ValueError, match="excluded object"):
            panel.commit()
        panel.edit.preview[0, 1, 1] = 0
        assert not panel.edit.allows_label(1)
        assert panel.edit.split(2) == expected_child
        panel.edit.preview[position] = expected_child
        panel.commit()
        assert working is window.filtering.filtered_layer and working.visible
        assert working.opacity == pytest.approx(.38) and not working.editable
        assert panel.viewer.layers.index(working) == index
        assert not window.segmentation.result_layer.visible
        assert state.filtering_result is None and not state.filtering_is_current
        assert not window.filtering.busy and "recalculation required" in panel.scientific_status.text()
        np.testing.assert_array_equal(effective_masks(state.segmentation_result)[raw == 1], raw[raw == 1])
    assert state.provisional_ids == [2, 3, 4]
    panel.undo()
    assert state.provisional_ids == [2, 3]
    panel.undo()
    assert state.provisional_ids == [2]
    panel.start()
    assert panel.edit.next_id == 5 and panel.edit.filtered
    panel.discard()
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), original)
    np.testing.assert_array_equal(raw, original)
    assert not raw.flags.writeable


def test_all_scope_reconciliation_and_ambiguous_merge(retained_second_mask):
    window, raw, working = retained_second_mask
    state = window.project_state
    edit = GeometryEdit(state)
    edit.preview[0, 0, 0] = 1
    edit.commit()
    assert state.provisional_ids == [2] and not working.data[0, 0, 0]
    edit = GeometryEdit(state)
    edit.merge([1, 2], 2)
    edit.commit()
    assert state.provisional_ids is None and state.filtering_result is None
    assert window.filtering.filtered_layer is None
    assert "establish retained objects" in window.filtering.working_status
    assert effective_masks(state.segmentation_result)[0, 0, 0] == 2 and raw[0, 0, 0] == 0
    GeometryEdit.undo(state)
    assert state.provisional_ids == [2] and window.filtering.filtered_layer.visible


def test_auto_recalculate_failure_and_retry(retained_second_mask, qapp, monkeypatch):
    from nuclear_segmentation import filtering_workspace
    from test_desktop_normalization import _wait

    window, _, working = retained_second_mask
    state, panel = window.project_state, window.geometry_panel
    settings = state.filtering_result["settings"].copy()
    statuses = []
    window.filtering.status_changed.connect(statuses.append)
    panel.auto_recalculate.setChecked(True)
    with monkeypatch.context() as scoped:
        def fail(*args):
            raise ValueError("test measurement failure")
        scoped.setattr(filtering_workspace, "_measure", fail)
        panel.start()
        panel.edit.preview[0, 0, 0] = 2
        panel.commit()
        assert window.filtering.busy and "Recalculating" in panel.scientific_status.text()
        _wait(qapp, lambda: not window.filtering.busy)
    committed = effective_masks(state.segmentation_result)
    assert committed[0, 0, 0] == 2 and working.data[0, 0, 0] == 2
    assert state.filtering_result is None and state.provisional_ids == [2]
    assert "test measurement failure" in panel.scientific_status.text()
    assert panel.buttons["recalculate"].isEnabled()
    panel.buttons["recalculate"].click()
    _wait(qapp, lambda: not window.filtering.busy)
    assert state.filtering_is_current and state.filtering_result["settings"] == settings
    assert effective_masks(state.segmentation_result) is committed and state.provisional_ids is None
    assert window.filtering.filtered_layer is working and not state.scientific_roi_reviewed
    assert "Measurements up to date" in panel.scientific_status.text()
    panel.start()
    panel.edit.preview[0, 0, 1] = 2
    panel.commit()
    _wait(qapp, lambda: not window.filtering.busy)
    assert state.filtering_is_current and statuses.count("Recalculating measurements and filters…") == 3


def test_remeasurement_can_retain_previously_excluded_object(retained_second_mask, qapp):
    from test_desktop_normalization import _wait

    window, raw, working = retained_second_mask
    state = window.project_state
    # Erase half the bright object, leaving its bright pixels available to a
    # subsequent all-mask brush edit. Both IDs survive each transaction.
    edit = GeometryEdit(state, filtered=True)
    edit.preview[0:2][raw[0:2] == 2] = 0
    edit.commit()
    edit = GeometryEdit(state)
    edit.preview[0:2][raw[0:2] == 2] = 1
    edit.commit()
    assert state.provisional_ids == [2] and not working.data[raw == 1].any()
    assert window.filtering.calculate()
    _wait(qapp, lambda: not window.filtering.busy)
    assert state.filtering_is_current and set(state.filtering_result["retained_ids"]) == {1, 2}
    np.testing.assert_array_equal(working.data, effective_masks(state.segmentation_result))


def test_quick_stroke_history_and_pending_status(review_window, monkeypatch):
    window, raw = review_window
    panel, state = window.geometry_panel, window.project_state
    panel.start()
    layer = panel.layer
    _click_mask(layer, (0, 1, 1))
    layer.brush_size = 1
    undo, redo = (panel.buttons[name + "_preview"] for name in ("undo", "redo"))
    assert not undo.isEnabled() and not redo.isEnabled()
    assert not undo.icon().isNull() and undo.toolTip() and redo.toolTip()
    revision = state.session_revision()

    def no_scan():
        pytest.fail("A brush stroke or replay scanned the volume")

    monkeypatch.setattr(panel.edit, "has_changes", no_scan)
    with layer.block_history():
        layer.paint((0, 0, 0), 1)
        layer.paint((0, 0, 1), 1)
    layer.paint((0, 1, 1), 0)
    assert undo.isEnabled() and not redo.isEnabled()
    panel.buttons["erase"].click()
    window.viewer_toolbar.set_ndisplay(3)
    assert not layer.editable and undo.isEnabled()
    window.viewer_toolbar.set_ndisplay(2)
    panel.viewer.dims.order = (1, 0, 2)
    undo.click()
    assert layer.data[0, 1, 1] == 1 and layer.data[0, 0, 0] == 1
    undo.click()
    np.testing.assert_array_equal(layer.data, raw)
    assert not undo.isEnabled() and redo.isEnabled()
    assert "No pending changes" in panel.status.text()
    redo.click()
    assert list(layer.data[0, 0, :2]) == [1, 1]
    redo.click()
    assert layer.data[0, 1, 1] == 0 and not redo.isEnabled()
    # Replay cannot interrupt napari while the next gesture is still being grouped.
    with layer.block_history():
        panel.undo_preview()
        assert layer.data[0, 1, 1] == 0
    panel.viewer.dims.order = (0, 1, 2)
    layer.paint((0, 1, 1), 1)
    layer.paint((0, 0, 0), 0)
    layer.paint((0, 0, 1), 0)
    assert undo.isEnabled() and "No pending changes" in panel.status.text()
    undo.click()
    assert "Uncommitted changes" in panel.status.text()
    layer.paint((0, 2, 2), 0)
    assert not redo.isEnabled()
    assert state.session_revision() == revision and not state.segmentation_result.get("edit_history")
    assert panel.selected_id == panel.brush_id == layer.selected_label == 1
    assert layer.brush_size == 1 and panel.layer is layer
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), raw)
    panel.discard()


@pytest.mark.parametrize("operation", ["split", "remove", "merge"])
def test_quick_operation_history_while_paused(review_window, operation):
    window, raw = review_window
    panel = window.geometry_panel
    panel.start()
    layer = panel.layer
    _click_mask(layer, (0, 1, 1))
    if operation == "merge":
        panel.add_merge()
        _click_mask(layer, (0, 5, 6))
        panel.add_merge()
        panel.merge_target.setCurrentIndex(panel.merge_target.findData(2))
    getattr(panel, operation)()
    parameters = panel.edit.parameters.copy()
    selected = panel.selected_id
    if operation == "split":
        layer.paint((0, 1, 1), panel.brush_id)
    expected = layer.data.copy()
    size = layer.brush_size
    panel.toggle_pause()
    if operation == "split":
        panel.undo_preview()
        assert panel.edit.operation == "split" and panel.brush_id == parameters["new_id"]
    panel.undo_preview()
    np.testing.assert_array_equal(layer.data, raw)
    assert panel.paused and not layer.editable and panel.edit.operation == "paint/erase"
    assert "Paused" in panel.status.text() and "No pending changes" in panel.status.text()
    assert not panel.buttons["undo_preview"].isEnabled()
    panel.redo_preview()
    if operation == "split":
        panel.redo_preview()
    assert panel.edit.operation == operation and panel.edit.parameters == parameters
    np.testing.assert_array_equal(layer.data, expected)
    assert panel.paused and not layer.editable and layer.brush_size == size
    assert panel.selected_id == selected and not panel.buttons["paint"].isEnabled()
    assert not panel.buttons["redo_preview"].isEnabled()
    panel.toggle_pause()
    panel.commit()
    np.testing.assert_array_equal(effective_masks(window.project_state.segmentation_result), expected)
    assert window.project_state.segmentation_result["edit_history"][-1]["operation"] == operation


def test_quick_history_scope_hidden_labels_and_new_preview(retained_second_mask):
    window, raw, working = retained_second_mask
    panel = window.geometry_panel
    panel.start()
    layer = panel.layer
    _click_mask(layer, (0, 5, 6))
    layer.brush_size = 1
    layer.paint((0, 1, 1), 2)
    with pytest.raises(ValueError, match="excluded object"):
        panel.commit()
    panel.undo_preview()
    assert layer.data[0, 1, 1] == 0
    panel.redo_preview()
    with pytest.raises(ValueError, match="excluded object"):
        panel.commit()
    panel.undo_preview()
    panel.scope.setCurrentIndex(1)
    assert layer.data[0, 1, 1] == 1
    assert not panel.buttons["redo_preview"].isEnabled()
    panel.scope.setCurrentIndex(0)
    layer.paint((0, 0, 0), 2)
    panel.commit()
    assert window.filtering.filtered_layer is working and working.data[0, 1, 1] == 0
    panel.start()
    assert panel.layer is not layer and panel.edit.filtered
    for name in ("undo_preview", "redo_preview"):
        assert not panel.buttons[name].isEnabled()
    _click_mask(panel.layer, (0, 5, 6))
    panel.layer.brush_size = 1
    panel.layer.paint((0, 1, 1), 2)
    panel.undo_preview()
    assert "No pending changes" in panel.status.text() and panel.layer.data[0, 1, 1] == 0
    assert effective_masks(window.project_state.segmentation_result)[0, 1, 1] == raw[0, 1, 1] == 1
    panel.discard()


def test_quick_shortcuts_text_focus_and_saved_history(review_window, qapp):
    import sys
    from qtpy.QtGui import QKeySequence
    from qtpy.QtTest import QTest

    window, raw = review_window
    state, panel = window.project_state, window.geometry_panel
    window.show()
    window.activateWindow()
    window.set_workflow("mask_review")
    window.navigation.set_subpage("mask_editing", emit=True)
    panel.start()
    _click_mask(panel.layer, (0, 1, 1))
    layer = panel.layer
    layer.brush_size = 1
    layer.paint((0, 0, 0), 1)
    for field in (panel.brush_label, panel.brush_size.lineEdit()):
        field.setFocus()
        qapp.processEvents()
        field.selectAll()
        QTest.keyClicks(field, "2")
        QTest.keySequence(field, QKeySequence("Ctrl+Z"))
        assert layer.data[0, 0, 0] == 1 and panel.buttons["undo_preview"].isEnabled()
        if field is panel.brush_label:
            assert field.text() == "1"
    target = window.embedding.qt_viewer
    target.setFocus()
    panel.set_expanded(False)
    QTest.keySequence(target, QKeySequence("Ctrl+Z"))
    assert layer.data[0, 0, 0] == 0
    redo = "Ctrl+Shift+Z" if sys.platform == "darwin" else "Ctrl+Y"
    QTest.keySequence(target, QKeySequence(redo))
    assert layer.data[0, 0, 0] == 1
    panel.commit()
    revision = state.active_session.geometry_revision
    panel.start()
    QTest.keySequence(target, QKeySequence("Ctrl+Z"))
    assert state.active_session.geometry_revision == revision and panel.busy
    panel.discard()
    window.set_workflow("segmentation")
    QTest.keySequence(target, QKeySequence("Ctrl+Z"))
    assert state.active_session.geometry_revision == revision
    window.set_workflow("mask_review")
    QTest.keySequence(target, QKeySequence("Ctrl+Z"))
    assert state.active_session.geometry_revision == revision + 1
    assert state.segmentation_result["edit_history"][-1]["operation"] == "undo"
    np.testing.assert_array_equal(effective_masks(state.segmentation_result), raw)
    QTest.keySequence(target, QKeySequence(redo))
    assert state.active_session.geometry_revision == revision + 1


def test_new_stroke_after_undoing_removal_starts_a_new_branch(review_window):
    window, raw = review_window
    panel = window.geometry_panel
    panel.start()
    _click_mask(panel.layer, (0, 1, 1))
    panel.remove()
    panel.undo_preview()
    panel.layer.brush_size = 1
    panel.layer.paint((0, 0, 0), 1)
    assert not panel.buttons["redo_preview"].isEnabled()
    panel.undo_preview()
    panel.redo_preview()
    assert panel.edit.operation == "paint/erase" and panel.layer.data[0, 0, 0] == 1
    np.testing.assert_array_equal(panel.layer.data[raw == 1], raw[raw == 1])
    panel.discard()
