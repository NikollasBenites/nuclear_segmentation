from copy import deepcopy
from matplotlib.figure import Figure
import pytest

from nuclear_segmentation import association_checkpoint as checkpoint
from nuclear_segmentation.reviewed_qc import REVIEW_PLOTS, draw_review
from test_matching_explorer import inspection_pair
from test_matching_pair import choose_in_panel
from test_integrated_matching import run_job
from test_native_project import project, save, open_saved


def test_reviewed_checkpoint_round_trip_and_qc(project, tmp_path):
    window, app, _, _, warnings = project
    state = window.project_state
    n,c = inspection_pair(window)
    panel = choose_in_panel(window,n,c)
    panel.minimum.setText(".71")
    result = run_job(state.matching_pair)
    review = state.matching_pair.review
    review.decide(result,2,"Matched",23,"different retained object")
    review.decide(result,7,"Uncertain")
    review.undo()
    record = checkpoint.snapshot(state.matching_pair)
    invalid = deepcopy(record)
    invalid["automatic"]["intersections"][0][-1] += 1
    with pytest.raises(ValueError,match="evidence"):
        checkpoint.validate(state,invalid)
    folder = tmp_path/"Associations"
    save(window,app,folder)
    window.close_project(confirm=False)
    open_saved(window,app,folder)
    assert not warnings
    pair = state.matching_pair
    current = pair.current_result
    assert current.settings == result.settings
    assert current.nuclei == result.nuclei and panel.minimum.text() == "0.71"
    assert pair.review.status(current,2) == "Matched"
    assert pair.review.records[pair.review.key(2)]["cytoplasmic_id"] == 23
    assert pair.review.events == review.events
    assert pair.review.status(current,7) == "Not Reviewed"
    for kind in REVIEW_PLOTS:
        ax = Figure().subplots()
        draw_review(ax,kind,current,pair.review)
        assert ax.get_title() and (ax.lines or ax.patches)
    pair.review.undo()
    assert pair.review.status(current,2) == "Not Reviewed"


def test_stale_review_checkpoint_and_geometry_integrity(project):
    window, app, _, _, _ = project
    state = window.project_state
    n,c = inspection_pair(window)
    choose_in_panel(window,n,c)
    result = run_job(state.matching_pair)
    state.matching_pair.review.decide(result,7,"Unmatched",comment="no supporting geometry")
    from nuclear_segmentation.native_project import snapshot_state
    snap = snapshot_state(state)
    record = checkpoint.prepare(snap["association_state"], snap["sessions"], snap["source"]["path"],
                                snap["source"]["sha256"])
    checkpoint.verify_geometry(record,state)
    corrupt = deepcopy(record)
    corrupt["geometry_signatures"][c.session_id] = "0"*64
    with pytest.raises(ValueError,match="signatures"):
        checkpoint.verify_geometry(corrupt,state)
    record["automatic"]["dependencies"]["sources"][0]["geometry"] += 1
    assert checkpoint.validate(state, record)[3] is None


def test_reviewed_qc_in_existing_window(project):
    window,app,_,_,_ = project
    n,c = inspection_pair(window)
    panel = choose_in_panel(window,n,c)
    result = run_job(window.project_state.matching_pair)
    window.show()
    panel.graphs.click()
    plots = window.context_panel.quality_control_page
    plots.matching_kind.setCurrentText(REVIEW_PLOTS[1])
    window.project_state.matching_pair.review.decide(result,7,"Uncertain")
    window.project_state.association_review_changed.emit()
    from test_native_project import _wait
    _wait(app, lambda: not plots.dirty and "Review completion" in plots.axes.get_title())
    assert plots.axes.patches[0].get_height() == 1
    assert plots.axes.patches[3].get_height() == 1

    from nuclear_segmentation.matching_plots import VOLUME_HISTOGRAM
    for kind in (VOLUME_HISTOGRAM, REVIEW_PLOTS[-2]):
        plots.matching_kind.setCurrentText(kind)
        plots.histogram_scale.setCurrentText("Relative frequency")
        plots.histogram_bins.setValue(8)
        plots.histogram_layout.setCurrentText("Separate panels")
        _wait(app, lambda: not plots.dirty)
        assert plots.histogram_controls.isVisible()
        separate_limits = [(ax.get_xlim(), ax.get_ylim()) for ax in plots.figure.axes]
        assert len(separate_limits) > 1 and len(set(separate_limits)) == 1
        plots.histogram_layout.setCurrentText("Overlaid")
        _wait(app, lambda: not plots.dirty)
        assert plots.figure.axes == [plots.axes]
        assert (plots.axes.get_xlim(), plots.axes.get_ylim()) == separate_limits[0]
        assert window.project_state.matching_pair.current_result is result
