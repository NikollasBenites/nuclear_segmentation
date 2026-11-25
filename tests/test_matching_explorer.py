"""Phase 2D.5.3 sparse associations, plot denominators and desktop lifecycle."""
import warnings

import numpy as np
import pytest
from matplotlib.figure import Figure
from qtpy.QtCore import QSettings, Qt
from qtpy.QtTest import QTest

from nuclear_segmentation.automatic_matching import (
    COLORS, MatchingSettings, OverlapStatistics, classify_population,
)
from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.filtering_workspace import calculate_filtering_prerequisites
from nuclear_segmentation.matching_explorer import AssociationExplorer
from nuclear_segmentation.matching_inspection import focus_voxel
from nuclear_segmentation.matching_plots import MatchingPlotData, PLOTS
from test_desktop_normalization import _dataset
from test_integrated_matching import run_job
from test_matching_pair import _wait, choose_in_panel, measured_pair, qapp, window_pair


BIG = 2**63 + 101
CYTO = 2**61 + 17


def population():
    # Nucleus 11 is ambiguous, 13 overlaps CYTO but is accepted by CYTO+1.
    nuclei = {BIG: 10, 7: 5, 11: 10, 13: 10, 15: 5, 17: 2}
    cytos = {CYTO: 30, CYTO + 1: 12, CYTO + 2: 7}
    intersections = {(BIG, CYTO): 9, (7, CYTO): 5, (11, CYTO): 3,
                     (13, CYTO): 2, (13, CYTO + 1): 8}
    stats = OverlapStatistics((), 1.5, nuclei, cytos, intersections)
    return classify_population(stats, MatchingSettings(), (1, 1))


def test_sparse_tables_filter_sort_and_candidate_selection(qapp):
    ui = AssociationExplorer()
    result = population()
    ui.set_result(result, "")
    model = ui.objects.model()
    assert model.rows == [(CYTO, 2, (7, BIG), 4, 45.)]
    assert not ui.next.isEnabled() and not ui.previous.isEnabled()
    assert model.index(0, 0).data() == str(CYTO)
    assert model.index(0, 2).data() == f"7, {BIG}"
    assert not model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable
    candidates = ui.candidates.model()
    assert candidates.rows == [(7, "MATCH", CYTO, 5, 7.5, 1., True),
                               (11, "AMBIGUOUS", None, 3, 4.5, .3, False),
                               (13, "MATCH", CYTO + 1, 2, 3., .2, False),
                               (BIG, "MATCH", CYTO, 9, 13.5, .9, True)]
    ui.candidates.selectRow(2)
    selected = ui.selection
    assert selected.focus_id == 13 and selected.focus_role == "nuclear"
    assert selected.other_candidate_ids == (11, 13)
    ui.multiple.setChecked(False)
    assert ui.selection == selected and len(model.rows) == 2
    ui.objects.sortByColumn(1, Qt.SortOrder.AscendingOrder)
    assert [row[0] for row in model.rows] == [CYTO + 1, CYTO]
    assert ui.selection == selected and ui.objects.currentIndex().row() == 1
    ui.previous.click()
    assert ui.selection.associated_ids == (13,) and ui.selection.focus_id == CYTO + 1
    ui.next.click()
    assert ui.selection.focus_id == CYTO
    ui.set_result(None, "Outdated")
    assert ui.selection is None and ui.status.text() == "Outdated" and not candidates.rows
    ui.close()


def test_decision_and_plot_denominators():
    result = population()
    data = MatchingPlotData(result)
    fig = Figure()
    ax = fig.subplots()
    data.draw(ax, PLOTS[0])
    np.testing.assert_allclose([p.get_height() for p in ax.patches], [3/6, 1/6, 2/6])
    assert [t.get_text() for t in ax.texts] == ["3 (50.0%)", "1 (16.7%)", "2 (33.3%)"]
    ax.clear()
    data.draw(ax, PLOTS[1])
    np.testing.assert_allclose(data.volumes["MATCH"], [7.5, 15., 15.])
    for patch, values in zip(ax.patches, data.volumes.values(), strict=True):
        xy = patch.get_xy()
        edges = data.volume_bins
        np.testing.assert_allclose(xy[1:2*len(edges)-1:2, 0], edges[:-1])
        np.testing.assert_allclose(xy[1:2*len(edges)-1:2, 1], np.histogram(values, edges)[0] / len(values))
    ax.clear()
    data.draw(ax, PLOTS[2])
    match_line = ax.lines[0]
    np.testing.assert_allclose(match_line.get_xdata(), [7.5, 7.5, 15])
    np.testing.assert_allclose(match_line.get_ydata(), [0., 1/3, 1.])
    ax.clear()
    data.draw(ax, PLOTS[3])
    assert data.links == {0: 1, 1: 1, 2: 1}
    assert [r.get_height() for r in ax.patches] == [1, 1, 1]
    ax.clear()
    data.draw(ax, PLOTS[4])
    np.testing.assert_allclose(data.fractions, [[.9, 0], [1, 0], [.3, 0], [.8, .2], [0, 0], [0, 0]])
    assert [line.get_xdata()[0] for line in ax.lines] == [.5, .2]
    ax.clear()
    data.draw(ax, PLOTS[5])
    assert data.points["NO_MATCH"] == {(0, 0): 2}
    np.testing.assert_allclose(ax.collections[-1].get_offsets(), [[0., 0.]])
    assert ax.collections[-1].get_sizes()[0] > ax.collections[0].get_sizes()[0]
    np.testing.assert_allclose(ax.patches[0].get_xy()[:3], [[0, 0], [.5, .5], [1, 0]])
    assert ax.lines[0].get_xdata()[0] == .5 and ax.lines[1].get_ydata()[0] == .2


@pytest.mark.parametrize("n,c", [(0, 0), (0, 1), (2, 0), (2, 1)])
def test_empty_plots_and_empty_groups(n, c, qapp):
    stats = OverlapStatistics((), 1., {i+1: 1 for i in range(n)}, {i+1: 1 for i in range(c)}, {})
    result = classify_population(stats, MatchingSettings(.80000003, .1), (1, 1))
    ui = AssociationExplorer()
    ui.set_result(result, "")
    assert ui.status.text().startswith("No multiple associations.")
    if not n or not c:
        assert f"{n} retained nuclei and {c} retained cytoplasmic objects" in ui.status.text()
    ui.close()
    data = MatchingPlotData(result)
    ax = Figure().subplots()
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        for plot in PLOTS:
            ax.clear()
            data.draw(ax, plot)
            if not (c if plot == PLOTS[3] else n):
                assert ax.texts[0].get_text().startswith("No retained") and not ax.patches
            elif plot in PLOTS[1:3]:
                assert [t.get_text() for t in ax.get_legend().get_texts()] == ["NO_MATCH (n=2)"]
            elif plot == PLOTS[3]:
                assert ax.patches[0].get_height() == c
            elif plot == PLOTS[5]:
                assert ax.lines[0].get_label() == "Best ≥ 0.80000003"
                assert ax.lines[1].get_label() == "Second ≤ 0.1"


def test_focus_uses_occupied_section_for_disconnected_label():
    labels = np.zeros((9, 3, 5), np.uint64)
    labels[[1, 7], 2, 3] = BIG
    point = focus_voxel(labels, BIG, None)
    assert point == (1, 2, 3) and labels[point] == BIG


def test_matching_qc_refresh_without_matching_workspace(window_pair, qapp):
    window, _ = window_pair
    window.show()
    state = window.project_state
    nuclear, cyto = measured_pair(window, crop=True)
    panel = choose_in_panel(window, nuclear, cyto)
    panel.graphs.click()
    plots = window.context_panel.quality_control_page
    canvas = plots.canvas
    assert window.graph_window.isVisible() and plots.category.currentText() == "Matching"
    assert "not been executed" in plots.status.text()
    assert plots.ns.get("segmentation_volume") is None
    context = state.matching_pair
    panel.run.click()
    window.set_workflow("quality_control")
    _wait(qapp, lambda: not window.matching_display.execution.busy)
    original = context.current_result
    assert not window.matching_display._active
    _wait(qapp, lambda: plots.canvas.isVisible() and not plots.dirty)
    data = plots._matching_data
    assert data.result is original
    renders = plots.render_count
    state.rename_session(cyto.session_id, "Renamed")
    state.create_session("Unrelated source")
    QTest.qWait(360)
    assert plots._matching_data is data and plots.render_count == renders
    context.set_settings(MatchingSettings(.8, .2))
    assert not canvas.isVisible() and "outdated" in plots.status.text()
    window.matching_display.execution.start()
    _wait(qapp, lambda: not window.matching_display.execution.busy)
    changed = context.current_result
    assert changed.statistics is original.statistics
    plots.matching_kind.setCurrentText(PLOTS[5])
    _wait(qapp, lambda: canvas.isVisible() and not plots.dirty)
    assert plots.axes.lines[0].get_label() == "Best ≥ 0.8"
    state.select_session(nuclear.session_id)
    saved = state.filtering_result
    state.invalidate_filtering()
    assert not canvas.isVisible() and "Recalculate" in plots.status.text()
    state.set_filtering_result(saved)
    _wait(qapp, lambda: canvas.isVisible())
    assert plots._matching_data.result is changed
    window.graph_window.hide()
    renders = plots.render_count
    failed = context.begin()
    context.fail(failed, "example failure")
    QTest.qWait(360)
    assert "failed" in plots.status.text() and plots.render_count == renders
    assert plots.canvas is canvas
    window.close_project(confirm=False)
    assert plots._matching_data is None and not plots.timer.isActive()



def inspection_pair(window):
    state = window.project_state
    nuclear, cytoplasmic = measured_pair(window, crop=True)
    nuclei = np.zeros(state.working_dataset.standardized_shape[1:], dtype=np.int32)
    cytos = np.zeros_like(nuclei)
    nuclei[0, 1, 1:5] = 2
    nuclei[1, 2, 1:5] = 7
    nuclei[1, 3, :5] = 11
    nuclei[2, 0, :5] = 13
    cytos[(nuclei == 7) | (nuclei == 2)] = 99
    cytos[1, 3, 0] = cytos[2, 0, 0] = 99
    cytos[2, 0, 1:5] = 23
    for session, labels in ((nuclear, nuclei), (cytoplasmic, cytos)):
        state.select_session(session.session_id)
        ids = tuple(map(int, np.unique(labels)[1:]))
        state.set_segmentation_result(dict(state.segmentation_result, masks=labels,
                                           mask_shape=labels.shape, object_count=len(ids), maximum_label_id=max(ids)))
        measured = calculate_filtering_prerequisites(state, state.segmentation_result["scientific_config"])
        window.filtering._calculated(dict(measured, retained_ids=ids, retained_count=len(ids), filtered_masks=labels))
    return nuclear, cytoplasmic


def test_explorer_on_embedded_canvas(qapp, tmp_path):
    if qapp.platformName() in {"offscreen", "minimal"}:
        pytest.skip("Requires the Qt OpenGL canvas")
    window = DesktopWindow(settings=QSettings(str(tmp_path / "inspector.ini"), QSettings.Format.IniFormat),
                           restore_workspace=False)
    try:
        window.show()
        dataset = _dataset(tmp_path / "spatial.tif")
        assert window._apply_imported_dataset(dataset)
        state = window.project_state
        nuclear, cytoplasmic = inspection_pair(window)
        panel = choose_in_panel(window, nuclear, cytoplasmic)
        run_job(state.matching_pair)
        ui = panel.explorer
        panel.class_colors.setChecked(True)
        selected = ui.selection
        assert selected.cytoplasmic_id == 99 and selected.associated_ids == (2, 7)
        assert selected.other_candidate_ids == (11, 13)
        assert selected.project_id == state.session_revision().project_id
        assert selected.nuclear_revision == state.session_revision(nuclear.session_id)
        assert selected.cytoplasmic_revision == state.session_revision(cytoplasmic.session_id)
        viewer = window.embedding.viewer
        canvas = window.embedding.qt_viewer.canvas
        display = window.matching_display
        n, c = display.layers["nuclear"], display.layers["cytoplasmic"]
        sources = (nuclear.filtering_result["filtered_masks"], cytoplasmic.filtering_result["filtered_masks"])
        originals = tuple(a.copy() for a in sources)
        active = state.active_session_id
        panel.opacity["nuclear"].setValue(73)
        panel.visibility["cytoplasmic"].setChecked(False)
        for dimensions in (2, 3, 2):
            assert window.viewer_toolbar.set_ndisplay(dimensions)
            ui._object_clicked(ui.objects.currentIndex())
            ui.focus.click()
            parent_highlight = display.inspection.highlight
            assert parent_highlight is not None and not parent_highlight.editable
            assert parent_highlight.data.sum() == (c.data == 99).sum()
            assert not c.visible and state.active_session_id == active
            ui.isolate.click()
            assert n.visible and c.visible and ui.restore.isEnabled()
            assert not n.editable and not c.editable
            np.testing.assert_allclose(n.colormap.map(7), COLORS["MATCH"])
            np.testing.assert_allclose(n.colormap.map(11), (0, 0, 0, 0))
            np.testing.assert_allclose(c.colormap.map(23), (0, 0, 0, 0))
            ui.candidates.selectRow(3)
            ui.focus.click()
            highlight = display.inspection.highlight
            assert highlight is not parent_highlight and parent_highlight not in viewer.layers
            assert not highlight.editable and highlight.data.sum() == (n.data == 13).sum()
            for pos in np.argwhere(highlight.data):
                source = np.rint(n.world_to_data(highlight.data_to_world(pos))).astype(int)
                assert n.data[tuple(source)] == 13
            assert ui.selection.focus_id == 13 and ui.include.isChecked()
            np.testing.assert_allclose(n.colormap.map(13), (1, 0, 1, 1))
            world = n.data_to_world((2, 0, 2))
            np.testing.assert_allclose(viewer.camera.center[-dimensions:], world[-dimensions:])
            if dimensions == 2:
                assert viewer.dims.point[0] == pytest.approx(world[0])
            assert viewer.dims.ndisplay == dimensions and len(display.layers) == 2
            QTest.qWait(100)
            assert window.viewer_toolbar.set_ndisplay(3 if dimensions == 2 else 2)
            assert highlight is display.inspection.highlight and not highlight.editable
            assert window.viewer_toolbar.set_ndisplay(dimensions)
            QTest.qWait(100)
            highlighted = canvas.screenshot()
            highlight.visible = False
            QTest.qWait(100)
            assert highlighted != canvas.screenshot()
            highlight.visible = True
            QTest.qWait(100)
            with_candidates = canvas.screenshot()
            ui.include.setChecked(False)
            QTest.qWait(100)
            assert not with_candidates.isNull() and canvas.screenshot() != with_candidates
            ui.restore.click()
            assert not c.visible and n.visible and n.opacity == pytest.approx(.73)
            np.testing.assert_allclose(n.colormap.map(11), COLORS["AMBIGUOUS"])
            assert ui.selection.is_current(state.matching_pair) and state.active_session_id == active
            for layer, source, original in zip((n, c), sources, originals, strict=True):
                assert np.shares_memory(layer.data, source)
                np.testing.assert_array_equal(source, original)
                viewer.layers.selection.active = layer
                assert not window.viewer_toolbar.activate_tool("paint")
                assert window.mask_selection.selected_mask_id is None
        ui.focus.click()
        highlight = display.inspection.highlight
        viewer.layers.remove(n)
        assert highlight not in viewer.layers
        display.show()
        n, c = display.layers["nuclear"], display.layers["cytoplasmic"]
        ui.isolate.click()
        ui.multiple.setChecked(False)
        ui.next.click()
        assert ui.selection.cytoplasmic_id == 23 and ui.selection.associated_ids == (13,)
        np.testing.assert_allclose(n.colormap.map(13), COLORS["MATCH"])
        np.testing.assert_allclose(c.colormap.map(99), (0, 0, 0, 0))
        ui.previous.click()
        ui.candidates.selectRow(3)
        ui.focus.click()
        highlight = display.inspection.highlight
        window.set_workflow("proofreading")
        assert highlight not in viewer.layers
        assert not display.layers and not window.mask_display.suspended
        window.set_workflow("matching")
        assert ui.selection.focus_id == 13 and not ui.restore.isEnabled()
        ui.isolate.click()
        ui.focus.click()
        highlight = display.inspection.highlight
        panel.minimum.setText(".9")
        assert highlight not in viewer.layers
        assert ui.selection is None and not ui.restore.isEnabled() and not display.inspection.appearance
        assert not selected.is_current(state.matching_pair) and not display.inspection.positions
        assert not display.layers["cytoplasmic"].visible
        panel.minimum.setText(".5")
        ui.isolate.click()
        ui.focus.click()
        highlight = display.inspection.highlight
        assert window._apply_imported_dataset(dataset)
        assert highlight not in viewer.layers
        assert ui.selection is None and not display.layers and not display.inspection.appearance
    finally:
        window.close()
        qapp.processEvents()
