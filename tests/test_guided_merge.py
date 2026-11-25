"""Association inspection, candidate identity and explicit merge transactions."""
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest
from qtpy.QtCore import QSettings, Qt
from qtpy.QtTest import QTest
from scipy.ndimage import label as components

from nuclear_segmentation.automatic_matching import MatchingSettings
from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.geometry_edit import GeometryEdit, validate_history
from nuclear_segmentation.matching_inspection import focus_voxel
from test_desktop_normalization import _dataset
from test_integrated_matching import run_job
from test_matching_explorer import inspection_pair
from test_matching_pair import choose_in_panel, qapp, window_pair
from test_native_project import project, save, open_saved


def open_candidates(window, *, excluded=False):
    nuclear, cyto = inspection_pair(window)
    if excluded:
        state = window.project_state
        measured = dict(cyto.filtering_result)
        raw = cyto.segmentation_result["masks"].copy()
        raw[0, 0, 0] = 501
        state.set_segmentation_result(dict(cyto.segmentation_result, masks=raw, maximum_label_id=501, object_count=3))
        measured["geometry_revision"] = cyto.geometry_revision
        window.filtering._calculated(measured)
    window.project_state.select_session(nuclear.session_id)
    panel = choose_in_panel(window, nuclear, cyto)
    result = run_job(window.project_state.matching_pair)
    panel.explorer_mode.setCurrentIndex(1)
    ui = panel.candidate_explorer
    assert ui.objects.model().rows == [(13, "MATCH", 2, (23, 99))]
    rows = {r[0]: r for r in ui.candidates.model().rows}
    assert rows[23][1:3] == (4, .8) and rows[99][1:3] == (1, .2)
    assert rows[99][4] == (2, 7)
    for cyto_id, row in rows.items():
        assert row[3] == pytest.approx(result.statistics.cytoplasmic_voxels[cyto_id] * result.statistics.voxel_volume_um3)
    ui.candidates.selectAll()
    assert ui.selection().ids == (23, 99)
    assert "5/5 voxels" in ui.summary.text() and "2, 7, 11" in ui.summary.text()
    ui.candidates.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    assert ui.selection().ids == (23, 99)
    assert window.project_state.active_session is nuclear and not window.project_state.matching_pair.review.events
    return nuclear, cyto, ui


def test_guided_merge_history_native_round_trip(project, tmp_path):
    window, app, _, _, warnings = project
    nuclear, cyto, ui = open_candidates(window, excluded=True)
    state = window.project_state
    reference = nuclear.segmentation_result
    original = cyto.segmentation_result["masks"].copy()
    ui.review.click()
    geometry = window.geometry_panel
    assert geometry.busy and state.active_session is cyto and geometry.edit.filtered
    assert geometry.merge_target.currentIndex() == -1 and not geometry.buttons["merge"].isEnabled()
    geometry.merge_target.setCurrentIndex(geometry.merge_target.findData(23))
    geometry.merge()
    assert "Disconnected" in geometry.status.text() and "5/5" in geometry.status.text()
    expected = original.copy()
    expected[original == 99] = 23
    assert components(expected == 23)[1] > 1
    np.testing.assert_array_equal(geometry.edit.preview, np.where(expected == 501, 0, expected))
    assert not geometry.buttons["commit"].isEnabled() and not geometry.layer.editable
    geometry.apply_operation()
    geometry.undo_preview()
    np.testing.assert_array_equal(geometry.edit.preview, np.where(original == 501, 0, original))
    geometry.redo_preview()
    np.testing.assert_array_equal(geometry.edit.preview, np.where(expected == 501, 0, expected))
    geometry.commit()
    record = cyto.segmentation_result["edit_history"][-1]
    assert record["operation"] == "merge" and record["parameters"]["origin"] == "association_review"
    assert record["parameters"]["nucleus_id"] == 13
    assert state.matching_pair.current_result is None and nuclear.segmentation_result is reference
    assert not state.matching_pair.review.events and state.geometry_pending
    np.testing.assert_array_equal(cyto.segmentation_result["masks"], original)
    corrupt = deepcopy(cyto.segmentation_result)
    corrupt["edit_history"][-1]["parameters"]["result_count"] += 1
    with pytest.raises(ValueError, match="Merge"):
        validate_history(corrupt, cyto)
    folder = tmp_path / "Guided merge"
    save(window, app, folder)
    window.close_project(confirm=False)
    open_saved(window, app, folder)
    assert not warnings
    state.select_session(cyto.session_id)
    np.testing.assert_array_equal(state.segmentation_result["edited_masks"], expected)
    GeometryEdit.undo(state)
    np.testing.assert_array_equal(state.segmentation_result["edited_masks"], original)


def test_merge_stale_and_cancel(window_pair):
    window, _ = window_pair
    nuclear, cyto, ui = open_candidates(window)
    pair = window.project_state.matching_pair
    selection = ui.selection()
    assert not replace(selection, ids=(23, 987)).is_current(pair)
    ui.review.click()
    panel = window.geometry_panel
    settings = pair.pending_settings
    panel.merge_target.setCurrentIndex(0)
    pair.set_settings(MatchingSettings(.98, .01))
    with pytest.raises(ValueError, match="inputs changed"):
        panel.merge()
    pair.set_settings(settings)
    panel.merge()
    panel.cancel_operation()
    np.testing.assert_array_equal(panel.edit.preview, cyto.filtering_result["filtered_masks"])
    panel.merge_target.setCurrentIndex(1)
    panel.merge()
    pair.set_settings(MatchingSettings(.98, .01))
    with pytest.raises(ValueError, match="inputs changed"):
        panel.apply_operation()
    pair.set_settings(settings)
    panel.apply_operation()
    pair.set_settings(MatchingSettings(.98, .01))
    with pytest.raises(ValueError, match="inputs changed"):
        panel.commit()
    panel.discard()


def test_review_overlay_lifecycle_and_navigation(window_pair):
    window, _ = window_pair
    n, c = inspection_pair(window)
    matching = choose_in_panel(window, n, c)
    pair = window.project_state.matching_pair
    run_job(pair)
    explorer = matching.explorer
    explorer.next_nucleus.click()
    assert window.matching_display.inspection.focused_selection == explorer.selection
    window.set_workflow("proofreading")
    ui = window.association_panel
    active = window.project_state.active_session
    appearance = deepcopy(active.display_state)
    ui.set_expanded(True)
    assert len(ui.layers) == 2
    viewer = window.embedding.viewer
    nuclear, cyto = ui.layers
    assert np.shares_memory(nuclear.data, n.filtering_result["filtered_masks"])
    assert np.shares_memory(cyto.data, c.filtering_result["filtered_masks"])
    assert not nuclear.editable and not cyto.editable
    ui.nuclei.setCurrentText("7")
    point = focus_voxel(nuclear.data, 7, None)
    assert viewer.dims.point[0] == pytest.approx(nuclear.data_to_world(point)[0])
    ui.follow.setChecked(False)
    camera = viewer.camera.center
    ui.targets.setCurrentText("23")
    assert viewer.camera.center == camera and not pair.review.events
    assert not np.array_equal(cyto.colormap.map(23), cyto.colormap.map(99))
    ui.visibility["nuclear"].setChecked(False)
    ui.opacity["cytoplasmic"].setValue(32)
    assert not nuclear.visible and cyto.opacity == .32
    ui.isolation.setChecked(True)
    assert cyto.colormap.map(99)[-1] == 0
    ui.targets.setCurrentText("")
    ui.isolation.setChecked(False)
    assert cyto.colormap.map(99)[-1] > 0
    geometry = window.geometry_panel
    geometry.start()
    assert not ui.layers and geometry.layer.editable == (geometry.brush_id is not None)
    ui.inspect(False, True)
    assert not ui.layers and window.mask_display.suspended
    geometry.discard()
    assert len(ui.layers) == 2
    pair.set_settings(MatchingSettings(.98, .01))
    assert not ui.layers and not window.mask_display.suspended
    assert window.project_state.active_session is active and active.display_state == appearance


def test_subset_accounting_manual_links_and_no_match(window_pair):
    window, _ = window_pair
    state = window.project_state
    nuclear, cyto = inspection_pair(window)
    raw = cyto.segmentation_result["masks"].copy()
    raw[nuclear.segmentation_result["masks"] == 11] = 0
    raw[2, 0, 4] = 42
    state.set_segmentation_result(dict(cyto.segmentation_result, masks=raw, object_count=3))
    from nuclear_segmentation.filtering_workspace import calculate_filtering_prerequisites
    measured = calculate_filtering_prerequisites(state, state.segmentation_result["scientific_config"])
    window.filtering._calculated(dict(measured, retained_ids=(23, 42, 99), retained_count=3, filtered_masks=raw))
    panel = choose_in_panel(window, nuclear, cyto)
    pair = state.matching_pair
    result = run_job(pair)
    pair.review.decide(result, 7, "Matched", 42)
    panel.explorer_mode.setCurrentIndex(1)
    ui = panel.candidate_explorer
    assert ui.objects.model().rows == [(13, "MATCH", 3, (23, 42, 99))]
    selection = replace(ui.selection(), ids=(23, 42))
    assert selection.is_current(pair)
    summary = selection.summary(pair.review)
    assert "4/5 voxels (80.00%)" in summary and summary.endswith(": 7")
    ui.classes.setCurrentText("NO_MATCH")
    assert ui.selection() is None and not ui.review.isEnabled()
    window.set_workflow("proofreading")
    review = window.association_panel
    review.set_expanded(True)
    review.nuclei.setCurrentText("11")
    assert not review.targets.currentText() and review.layers[1].colormap.map(99)[-1] > 0
    assert not np.array_equal(review.layers[0].colormap.map(11), review.layers[0].colormap.map(7))
    review.set_expanded(False)
    assert not review.layers and len(pair.review.events) == 1
    review.set_expanded(True)
    assert review.layers
    window.close()
    assert not review.layers and ui.result is None


def test_candidate_canvas_and_sidebar(qapp, tmp_path):
    if qapp.platformName() in {"offscreen", "minimal"}:
        pytest.skip("Requires the Qt OpenGL canvas")
    window = DesktopWindow(settings=QSettings(str(tmp_path / "merge.ini"), QSettings.Format.IniFormat),
                           restore_workspace=False)
    try:
        window.show()
        window.resize(1024, 680)
        window.splitter.setSizes([200, 496, 320])
        assert window._apply_imported_dataset(_dataset(tmp_path / "merge.tif"))
        _, _, ui = open_candidates(window)
        viewer = window.embedding.viewer
        for dimensions in (3, 2):
            window.viewer_toolbar.set_ndisplay(dimensions)
            ui.candidates.selectRow(0)
            layer = window.matching_display.layers["cytoplasmic"]
            world = layer.data_to_world(focus_voxel(layer.data, ui.focus_id, None))
            np.testing.assert_allclose(viewer.camera.center[-dimensions:], world[-dimensions:])
            for isolate in (False, True):
                ui.isolate.setChecked(isolate)
                QTest.qWait(100)
                assert not window.embedding.qt_viewer.canvas.screenshot().isNull()
        from qtpy.QtWidgets import QScrollArea
        scroll = ui.parentWidget()
        while not isinstance(scroll, QScrollArea):
            scroll = scroll.parentWidget()
        scroll.ensureWidgetVisible(ui.review)
        assert scroll.horizontalScrollBar().maximum() == 0
        assert scroll.viewport().rect().contains(ui.review.mapTo(scroll.viewport(), ui.review.rect().center()))
    finally:
        window.close()
