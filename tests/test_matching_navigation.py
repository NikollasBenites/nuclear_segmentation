"""Phase 2 inspection handoffs and in-project navigation history."""
import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QScrollArea

from nuclear_segmentation.matching_navigation import SpatialView
from test_guided_merge import open_candidates
from test_integrated_matching import run_job
from test_matching_explorer import inspection_pair
from test_matching_pair import choose_in_panel, publish, qapp, window_pair


def test_round_trip_merge_selection_and_rail(window_pair, qapp):
    window, _ = window_pair
    window.show()
    window.resize(1024, 680)
    _, _, candidates = open_candidates(window)
    display = window.matching_display
    viewer, p = display.viewer, display.panel
    candidates.follow.setChecked(False)
    candidates.isolate.setChecked(True)
    candidates.classes.setCurrentText("MATCH")
    candidates.candidates.setMaximumHeight(40)
    viewer.dims.set_point(0, 0)
    viewer.scene.camera.zoom = 2.75
    viewer.scene.camera.center = (0., 2., 3.)
    qapp.processEvents()
    table = candidates.candidates
    table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
    table.verticalScrollBar().setValue(table.verticalScrollBar().maximum())
    scroll = table.verticalScrollBar().value(), table.horizontalScrollBar().value()
    assert any(scroll)
    view = SpatialView.capture(viewer)
    nav = window.navigation
    assert nav.legend.isVisible() and not viewer.canvas.overlays.text.visible
    nav.legend.set_expanded(False)
    nav.set_collapsed(True)
    assert not nav.legend.isVisible()
    nav.set_collapsed(False)
    assert nav.legend.isVisible() and not nav.legend.expanded
    window.set_focus_mode(True)
    assert not nav.legend.isVisible()
    window.set_focus_mode(False)
    nav.legend.set_expanded(True)
    assert window.width() == 1024 and window.height() == 680
    rail = nav.findChild(QScrollArea)
    assert rail.horizontalScrollBar().maximum() == 0
    assert nav.legend.geometry().top() >= rail.geometry().bottom()
    assert nav.rect().contains(nav.legend.geometry())
    table.verticalScrollBar().setValue(scroll[0])
    table.horizontalScrollBar().setValue(scroll[1])
    window.set_workflow("proofreading")
    assert display.location().preferences.tables[-1].vertical == scroll[0]
    assert SpatialView.capture(viewer) == view
    review = window.association_panel
    assert review.nuclei.currentText() == "13"
    assert not nav.legend.isVisible()
    review.set_expanded(True)
    assert nav.legend.isVisible() and "13" in nav.legend_selection.text()
    window.set_workflow("matching")
    qapp.processEvents()
    assert candidates.selection().ids == (23, 99) and candidates.nucleus == 13
    assert candidates.isolate.isChecked() and not candidates.follow.isChecked()
    assert p.explorer_mode.currentIndex() == 1 and candidates.classes.currentText() == "MATCH"
    assert candidates.candidates.model().order == (1, Qt.SortOrder.DescendingOrder)
    assert (table.verticalScrollBar().value(), table.horizontalScrollBar().value()) == scroll
    assert SpatialView.capture(viewer) == view
    window.set_workflow("proofreading")
    review.nuclei.setCurrentText("11")
    window.set_workflow("matching")
    assert candidates.nucleus == 11 and "AMBIGUOUS" in nav.legend_selection.text()
    assert ("nuclear", 11) in display.renderer.marks
    window.set_workflow("quantification")
    assert not nav.legend.isVisible()


def test_pair_scoped_ids_and_invalid_geometry(window_pair):
    window, _ = window_pair
    state = window.project_state
    nuclear, cyto = inspection_pair(window)
    alternate = state.create_session("Second cyto", scientific_role="cytoplasmic", channel_index=0)
    publish(state, alternate, window=window)
    p = choose_in_panel(window, nuclear, cyto)
    pair = state.matching_pair
    display = window.matching_display
    run_job(pair)
    display.select_object("nuclear", 13)
    p.candidate_explorer.isolate.setChecked(True)
    pair.select("cytoplasmic", alternate.session_id)
    assert pair.current_result is None and not display.renderer.marks
    run_job(pair)
    display.select_object("cytoplasmic", 7)
    pair.select("cytoplasmic", cyto.session_id)
    run_job(pair)
    assert p.candidate_explorer.nucleus == 13 and p.explorer_mode.currentIndex() == 1
    assert p.candidate_explorer.isolate.isChecked()
    pair.select("cytoplasmic", alternate.session_id)
    run_job(pair)
    assert p.explorer.selection.focus_id == 7 and p.explorer_mode.currentIndex() == 0
    pair.select("cytoplasmic", cyto.session_id)
    run_job(pair)
    publish(state, nuclear, window=window)
    assert pair.current_result is None and not display.renderer.marks
    run_job(pair)
    assert p.candidate_explorer.nucleus is None and not display.renderer.marks
    assert not p.candidate_explorer.objects.currentIndex().isValid()
    assert "unavailable" in p.selection_status.text()
    window.set_workflow("proofreading")
    assert window.association_panel.nuclei.currentIndex() == -1
    window.set_workflow("matching")
    assert not display.renderer.marks and "unavailable" in p.selection_status.text()
    display.select_object("nuclear", 7)
    assert "unavailable" not in p.selection_status.text()


def test_editing_reference_camera_and_preview_identity(window_pair):
    window, _ = window_pair
    source, cyto, ui = open_candidates(window)
    viewer = window.embedding.viewer
    viewer.scene.camera.zoom = 3.25
    viewer.scene.camera.center = (0, 4, 2)
    view = SpatialView.capture(viewer)
    ui.review.click()
    g = window.geometry_panel
    assert view == SpatialView.capture(viewer)
    ref = g.reference_layer
    assert np.shares_memory(ref.data, source.filtering_result["filtered_masks"])
    assert not ref.data.flags.writeable and not ref.editable
    assert viewer.layers.selection.active is g.layer and not window.mask_selection._is_labels(ref)
    assert not window.project_state.owns_mask_layer(ref)
    assert source.display_name in g.handoff_context.text() and cyto.display_name in g.handoff_context.text()
    assert all(str(i) in g.handoff_context.text() for i in (13, 23, 99))
    g.merge_target.setCurrentIndex(g.merge_target.findData(23))
    g.merge()
    assert "result ID 23" in g.layer.name and window.mask_selection.selected_mask_id == 23
    assert not ref.editable and not g.layer.editable
    g.discard()
    assert ref not in viewer.layers


def test_watershed_parent_and_inspected_reference(window_pair, qapp):
    from test_guided_split import generate

    window, _ = window_pair
    nuclear, cyto = inspection_pair(window)
    panel = choose_in_panel(window, nuclear, cyto)
    state = window.project_state
    run_job(state.matching_pair)
    explorer = panel.explorer
    explorer.candidates.selectRow(2)
    assert explorer.selection.focus_id == 11
    viewer = window.embedding.viewer
    viewer.scene.camera.zoom = 4.5
    before = SpatialView.capture(viewer)
    explorer.review.click()
    g = window.geometry_panel
    assert state.active_session is cyto and window.mask_selection.selected_mask_id == 99
    assert before == SpatialView.capture(viewer)
    assert "Inspected nuclear ID 11" in g.handoff_context.text()
    ref = g.reference_layer
    assert np.shares_memory(ref.data, nuclear.filtering_result["filtered_masks"])
    assert not ref.editable and viewer.layers.selection.active is window.mask_display.layers["filtered"]
    generate(g.guided, qapp)
    assert ref in viewer.layers and not ref.editable
    assert g.layer is viewer.layers.selection.active
    assert "Watershed preview" in g.layer.name
    expected = g.edit.parameters["child_ids"]
    assert all(str(label) in g.guided.details.text() for label in expected)
    g.apply_operation()
    assert all(str(label) in g.layer.name for label in expected)
    g.discard()
    window.set_workflow("matching")
    assert ref not in viewer.layers and explorer.selection.focus_id == 11


def test_removed_candidate_does_not_select_first_remaining_row(window_pair):
    window, _ = window_pair
    _, cyto, ui = open_candidates(window)
    ui.multiple.setChecked(False)
    ui.candidates.selectAll()
    assert set(ui.selection().ids) == {23, 99}
    state = window.project_state
    before = state.session_revision(cyto.session_id)
    window.set_workflow("quantification")
    state.select_session(cyto.session_id)
    data = cyto.filtering_result["filtered_masks"]
    window.filtering._calculated(dict(cyto.filtering_result, retained_ids=(23,), retained_count=1,
                                      filtered_masks=np.where(data == 23, data, 0)))
    assert state.session_revision(cyto.session_id) == before
    result = run_job(state.matching_pair)
    assert 13 in result.statistics.nuclear_voxels
    window.set_workflow("matching")
    assert ui.nucleus is None and ui.selection() is None
    assert "unavailable" in window.matching_display.panel.selection_status.text()
    ui.objects.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    assert ui.nucleus is None and not window.matching_display.renderer.marks
