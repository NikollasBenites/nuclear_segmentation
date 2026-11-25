import pytest
from nuclear_segmentation.automatic_matching import MatchingSettings
from test_matching_explorer import inspection_pair
from test_matching_pair import choose_in_panel, window_pair, qapp
from test_integrated_matching import run_job


def test_review_independent_of_thresholds_and_geometry(window_pair):
    window, _ = window_pair
    state = window.project_state
    n,c = inspection_pair(window)
    choose_in_panel(window,n,c)
    pair = state.matching_pair
    run_job(pair)
    original = pair.current_result
    window.set_workflow("proofreading")
    panel = window.association_panel
    panel.nuclei.setCurrentText("2")
    panel.comment.setText("inspected")
    panel.save(True)
    review = pair.review
    assert review.status(original, 2) == "Matched"
    assert review.records[review.key(2)]["cytoplasmic_id"] == 99
    panel.targets.setCurrentText("23")
    panel.decision.setCurrentText("Matched")
    panel.save(False)
    assert review.records[review.key(2)]["cytoplasmic_id"] == 23
    panel.inspect(False, True)
    assert len(panel.layers) == 2 and all(not layer.editable for layer in panel.layers)
    panel.restore()
    panel.undo()
    assert review.records[review.key(2)]["cytoplasmic_id"] == 99
    pair.set_settings(MatchingSettings(.9,.1))
    run_job(pair)
    assert review.status(pair.current_result,2) == "Matched"
    with pytest.raises(ValueError):
        review.decide(original,2,"Unmatched")
    state.select_session(n.session_id)
    from nuclear_segmentation.geometry_edit import GeometryEdit
    from nuclear_segmentation.filtering_workspace import calculate_filtering_prerequisites
    from nuclear_segmentation.geometry_edit import effective_masks
    edit = GeometryEdit(state)
    edit.remove(11)
    edit.commit()
    measured = calculate_filtering_prerequisites(state, state.segmentation_result["scientific_config"])
    window.filtering._calculated(dict(measured, retained_ids=(2,7,13), retained_count=3,
                                      filtered_masks=effective_masks(state.segmentation_result)))
    run_job(pair)
    assert review.status(pair.current_result,2) == "Requires Review"
    assert review.records[review.key(2)]["comment"] == "inspected"


def test_review_filters_reset_comments_and_pair_scope(window_pair):
    window, _ = window_pair
    state = window.project_state
    nuclear, target = inspection_pair(window)
    choose_in_panel(window, nuclear, target)
    run_job(state.matching_pair)
    window.set_workflow("proofreading")
    ui = window.association_panel
    review = state.matching_pair.review
    original = state.matching_pair.current_result
    ui.nuclei.setCurrentText("7")
    for status in ("Unmatched", "Uncertain", "Not Reviewed"):
        ui.decision.setCurrentText(status)
        ui.comment.setText(status)
        ui.save(False)
        ui.filter.setCurrentText(status)
        assert ui.nuclei.findText("7") >= 0
        ui.filter.setCurrentIndex(0)
        assert review.status(original, 7) == status
        assert review.records[review.key(7)]["cytoplasmic_id"] is None
    with pytest.raises(ValueError, match="retained cytoplasmic"):
        review.decide(original, 7, "Matched", 987)
    assert state.matching_pair.current_result is original
    geometry = state.session_revision(target.session_id)
    ui.classes.setCurrentText("NO_MATCH")
    assert all(row.auto_class == "NO_MATCH" for row in original.nuclei
               if ui.nuclei.findText(str(row.nuclear_id)) >= 0)
    assert state.session_revision(target.session_id) == geometry
