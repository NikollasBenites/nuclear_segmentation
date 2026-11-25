from qtpy.QtTest import QTest

from test_matching_pair import qapp, window_pair, choose_in_panel
from test_matching_explorer import inspection_pair
from test_integrated_matching import run_job


def test_display_filters_preserve_science_and_reveal_picked_ids(window_pair):
    w, _ = window_pair
    p = choose_in_panel(w, *inspection_pair(w))
    pair = w.project_state.matching_pair
    result = run_job(pair)
    evidence = dict(result.statistics.intersections)
    revision = w.project_state.session_revision()
    p.display_overlap.setValue(30)
    assert {r.label_id for r in p.explorer.candidates.model().rows} == {2, 7}
    assert set(p.explorer.selection.other_candidate_ids) == {11, 13}
    w.matching_display.select_object("nuclear", 13)
    ui = p.candidate_explorer
    assert {r[0] for r in ui.candidates.model().rows} == {23}
    ui.accepted.setCurrentIndex(1)
    assert ui.nucleus is None
    w.matching_display.select_object("nuclear", 2)
    assert ui.nucleus == 2
    ui.review_filter.setCurrentText("Matched")
    assert ui.nucleus is None
    pair.review.decide(result, 2, "Matched", 99)
    w.project_state.association_review_changed.emit()
    assert [r[0] for r in ui.objects.model().rows] == [2]
    p.display_overlap.setValue(0)
    assert pair.current_result is result and pair.pending_settings == result.settings
    assert evidence == result.statistics.intersections and w.project_state.session_revision() == revision


def test_explicit_modes_and_readonly_handoff(window_pair, qapp):
    w, _ = window_pair
    w.show()
    nuclear, cyto = inspection_pair(w)
    p = choose_in_panel(w, nuclear, cyto)
    pair = w.project_state.matching_pair
    result = run_job(pair)
    w.matching_display.select_object("nuclear", 13)
    p.review_selected.click()
    assert w.review_mode.currentText() == "Manual Association Review"
    assert w.association_panel.isVisible() and not w.geometry_panel.isVisible()
    assert w.association_panel.nuclei.currentText() == "13"
    w._choose_review_mode(1)
    assert w.geometry_panel.isVisible() and not w.association_panel.isVisible()
    assert w.geometry_panel.reference_target_role == "nuclear"
    assert w.geometry_panel.reference_layer is not None and not w.geometry_panel.reference_layer.editable
    w.geometry_panel.start()
    try:
        w._choose_review_mode(0)
        assert w.review_mode.currentIndex() == 1 and w.geometry_panel.busy
    finally:
        w.geometry_panel.discard()
    w._choose_review_mode(0)
    assert result is pair.current_result and p.candidate_explorer.nucleus == 13
