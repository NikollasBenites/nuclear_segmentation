import numpy as np
import pytest

from nuclear_segmentation.filtering_workspace import calculate_filtering_prerequisites
from nuclear_segmentation.geometry_edit import GeometryEdit, effective_masks, validate_history
from nuclear_segmentation.project_state import ProjectState
from test_desktop_normalization import _dataset
from test_mask_review_gui import qapp, review_window
from test_native_project import _segment, open_saved, project, save


def test_preview_discard_worker_conflicts_and_2d_3d(review_window, tmp_path):
    window, raw = review_window
    panel = window.geometry_panel
    original = raw.copy()
    assert "analysis_mode" not in window.context_panel.project_page.sections
    panel.start()
    assert not window.segmentation.result_layer.editable
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
        panel.label.setValue(90000)
        panel.split()
        panel.layer.paint((0, 1, 1), panel.label.value())
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
