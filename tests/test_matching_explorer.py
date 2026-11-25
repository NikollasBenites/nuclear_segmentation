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
from nuclear_segmentation.matching_plots import MatchingPlotData, PLOTS, draw_volume_histogram
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
    ui.candidates.sortByColumn(5, Qt.SortOrder.DescendingOrder)
    assert [row.nuclear_fraction for row in candidates.rows] == [1., .9, .3, .2]
    cell = candidates.index(1, 5)
    assert cell.data() == "90.00%" and cell.data(Qt.ItemDataRole.UserRole) == .9
    assert ui.selection == selected and ui.candidates.currentIndex().row() == 3
    ui.accepted.setCurrentIndex(0)
    assert ui.selection == selected and len(model.rows) == 3
    ui.objects.sortByColumn(1, Qt.SortOrder.AscendingOrder)
    assert [row[0] for row in model.rows] == [CYTO + 2, CYTO + 1, CYTO]
    assert ui.selection == selected and ui.objects.currentIndex().row() == 2
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
        edges = np.histogram_bin_edges(np.concatenate(list(data.volumes.values())), bins=30)
        np.testing.assert_allclose(xy[1:2*len(edges)-1:2, 0], edges[:-1])
        np.testing.assert_allclose(xy[1:2*len(edges)-1:2, 1], np.histogram(values, edges)[0])
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


@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("separate", [False, True])
def test_volume_histogram_shared_scale(relative, separate):
    # Different ranges, sample sizes and peaks expose per-panel auto-binning or scaling.
    groups = {"A": [1, 2, 3, 4], "B": [3, 3, 4]}
    figure = Figure()
    ax = figure.subplots()
    draw_volume_histogram(ax, groups, bins=4, relative=relative, separate=separate)
    assert len(figure.axes) == (2 if separate else 1)
    edges = np.linspace(1, 4, 5)
    for panel in figure.axes:
        np.testing.assert_allclose(panel.get_xlim(), [1, 4])
        peak = 2 / 3 if relative else 2
        np.testing.assert_allclose(panel.get_ylim(), [0, peak * 1.05])
    patches = [patch for panel in figure.axes for patch in panel.patches]
    for patch, values in zip(patches, groups.values(), strict=True):
        points = patch.get_xy()[1:2*len(edges)-1:2]
        np.testing.assert_allclose(points[:, 0], edges[:-1])
        expected = np.histogram(values, edges)[0]
        np.testing.assert_allclose(points[:, 1], expected / len(values) if relative else expected)
    if separate:
        ax.set_xlim(2, 4)
        ax.set_ylim(0, 0.5)
        assert figure.axes[1].get_xlim() == (2, 4)
        assert figure.axes[1].get_ylim() == (0, 0.5)


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
        def focused_boundary():
            selection = ui.selection
            return display.renderer.marks[selection.focus_role, selection.focus_id][1]

        sources = (nuclear.filtering_result["filtered_masks"], cytoplasmic.filtering_result["filtered_masks"])
        originals = tuple(a.copy() for a in sources)
        active = state.active_session_id
        panel.opacity["nuclear"].setValue(73)
        panel.visibility["cytoplasmic"].setChecked(False)
        for dimensions in (2, 3, 2):
            assert window.viewer_toolbar.set_ndisplay(dimensions)
            ui._object_clicked(ui.objects.currentIndex())
            ui.focus.click()
            parent_highlight = focused_boundary()
            assert parent_highlight is not None and not parent_highlight.editable
            assert parent_highlight.data.any() and not parent_highlight.visible
            assert not c.visible and state.active_session_id == active
            ui.isolate.click()
            assert n.visible and c.visible and ui.restore.isEnabled()
            assert not n.editable and not c.editable
            np.testing.assert_allclose(n.colormap.map(7), COLORS["MATCH"])
            np.testing.assert_allclose(n.colormap.map(11), (0, 0, 0, 0))
            np.testing.assert_allclose(c.colormap.map(23), (0, 0, 0, 0))
            ui.candidates.selectRow(3)
            ui.focus.click()
            highlight = focused_boundary()
            assert highlight is not parent_highlight and parent_highlight not in viewer.layers
            assert not highlight.editable and highlight.data.any()
            for pos in np.argwhere(n.data == 13):
                local = np.rint(highlight.world_to_data(n.data_to_world(pos))).astype(int)
                assert highlight.data[tuple(local)] == 0
            assert ui.selection.focus_id == 13 and ui.include.isChecked()
            np.testing.assert_allclose(n.colormap.map(13), COLORS["MATCH"])
            world = n.data_to_world((2, 0, 2))
            np.testing.assert_allclose(viewer.camera.center[-dimensions:], world[-dimensions:])
            if dimensions == 2:
                assert viewer.dims.point[0] == pytest.approx(world[0])
            assert viewer.dims.ndisplay == dimensions and len(display.layers) == 2
            QTest.qWait(100)
            assert window.viewer_toolbar.set_ndisplay(3 if dimensions == 2 else 2)
            assert highlight not in viewer.layers and not focused_boundary().editable
            assert window.viewer_toolbar.set_ndisplay(dimensions)
            QTest.qWait(100)
            highlight = focused_boundary()
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
        highlight = focused_boundary()
        viewer.layers.remove(n)
        assert highlight not in viewer.layers
        display.show()
        n, c = display.layers["nuclear"], display.layers["cytoplasmic"]
        ui.isolate.click()
        ui.accepted.setCurrentIndex(0)
        ui.next.click()
        assert ui.selection.cytoplasmic_id == 23 and ui.selection.associated_ids == (13,)
        np.testing.assert_allclose(n.colormap.map(13), COLORS["MATCH"])
        np.testing.assert_allclose(c.colormap.map(99), (0, 0, 0, 0))
        ui.previous.click()
        ui.candidates.selectRow(3)
        ui.focus.click()
        highlight = focused_boundary()
        window.set_workflow("proofreading")
        assert highlight not in viewer.layers
        assert not display.layers and not window.mask_display.suspended
        window.set_workflow("matching")
        assert ui.selection.focus_id == 13 and ui.restore.isEnabled()
        ui.isolate.click()
        ui.focus.click()
        highlight = focused_boundary()
        panel.minimum.setText(".9")
        assert highlight not in viewer.layers
        assert ui.selection is None and not ui.restore.isEnabled() and not display.inspection.appearance
        assert not selected.is_current(state.matching_pair) and not display.renderer.marks
        assert not display.layers["cytoplasmic"].visible
        panel.minimum.setText(".5")
        ui.isolate.click()
        ui.focus.click()
        highlight = focused_boundary()
        assert window._apply_imported_dataset(dataset)
        assert highlight not in viewer.layers
        assert ui.selection is None and not display.layers and not display.inspection.appearance
    finally:
        window.close()
        qapp.processEvents()


def click_mask(display, position, *, direction=None, drag=False):
    from types import SimpleNamespace

    viewer = display.viewer
    event = SimpleNamespace(type="mouse_press", button=1, modifiers=(), pos=np.array([10, 10]),
                            position=position, dims_displayed=viewer.dims.displayed, view_direction=direction)
    callback = display._pick
    assert callback in viewer.mouse_drag_callbacks
    gesture = callback(viewer, event)
    next(gesture, None)
    if drag:
        event.type = "mouse_move"
        event.pos += 50
        next(gesture, None)
    event.type = "mouse_release"
    next(gesture, None)


def test_targeted_picks_and_read_only_appearance(window_pair):
    window, _ = window_pair
    n, c = inspection_pair(window)
    panel = choose_in_panel(window, n, c)
    result = run_job(window.project_state.matching_pair)
    display = window.matching_display
    viewer = display.viewer
    panel.class_colors.setChecked(True)
    nuclear = display.layers["nuclear"]
    position = nuclear.data_to_world((2, 0, 0))  # Nucleus 13 overlaps cyto 99 here but matches 23.
    viewer.dims.set_point(0, position[0])
    center = viewer.camera.center
    ui = panel.candidate_explorer
    click_mask(display, position)
    assert ui.nucleus == 13 and panel.explorer_mode.currentIndex() == 1
    assert panel.table.currentIndex().siblingAtColumn(0).data() == "13"
    assert panel.selection_status.text() == "Nucleus 13 · MATCH · OVERLAP_CRITERIA_MET"
    assert viewer.camera.center == center
    marks = display.renderer.marks
    assert marks["cytoplasmic", 23][1].name.startswith("Accepted")
    assert marks["cytoplasmic", 99][1].name.startswith("Candidate")
    panel.pick_target.setCurrentIndex(1)
    click_mask(display, position, drag=True)
    assert panel.explorer_mode.currentIndex() == 1
    click_mask(display, position)
    assert panel.explorer.selection.focus_id == 99 and panel.explorer_mode.currentIndex() == 0
    assert viewer.camera.center == center
    for label, kind in ((2, "Accepted"), (7, "Accepted"), (11, "Candidate"), (13, "Candidate")):
        assert marks["nuclear", label][1].name.startswith(kind)
    panel.pick_target.setCurrentIndex(0)
    point = nuclear.data_to_world((1, 3, 0))
    viewer.dims.set_point(0, point[0])
    click_mask(display, point)
    assert ui.nucleus == 11 and not ui.multiple.isChecked()
    assert panel.selection_status.text() == "Nucleus 11 · AMBIGUOUS · BEST_OR_SECOND_OUTSIDE_LIMITS"
    assert all(layer.name.split()[0] != "Accepted" for _, layer in marks.values())
    ui.isolate.setChecked(True)
    np.testing.assert_array_equal(nuclear.colormap.map(11), COLORS["AMBIGUOUS"])
    fraction = ui.candidates.model().index(0, 2)
    assert fraction.data() == "20.00%" and fraction.data(Qt.ItemDataRole.UserRole) == .2
    panel.table.sortByColumn(5, Qt.SortOrder.AscendingOrder)
    assert panel.table.currentIndex().siblingAtColumn(0).data() == "11"
    row = panel.table_model.row_numbers[7]
    panel.table.selectRow(row)
    assert ui.nucleus == 7 and panel.table_model.index(row, 5).data() == "100.00%"
    panel.styles["nuclear"].setCurrentText("Outlines only")
    panel.styles["cytoplasmic"].setCurrentText("Filled masks with outlines")
    for dimensions in (3, 2):
        window.viewer_toolbar.set_ndisplay(dimensions)
        edge = display.renderer.edges["nuclear"][1]
        np.testing.assert_array_equal(edge.colormap.map(7), COLORS["MATCH"])
        assert nuclear.colormap.map(7)[-1] == 0 and edge.data.any()
        assert edge.data_to_world((1, 2, 0)) == nuclear.data_to_world((1, 2, 0))
        assert all(layer.contour == 0 for _, layer in marks.values())
        for layer in (nuclear, edge, marks["nuclear", 7][1]):
            layer.editable = True
            assert not layer.editable
            viewer.layers.selection.active = layer
            for tool in ("paint", "erase"):
                assert not window.viewer_toolbar.activate_tool(tool)
        panel.opacity["nuclear"].setValue(39)
        assert edge.opacity == .39
        assert marks["nuclear", 7][1].opacity == pytest.approx(.39 * (.4 if dimensions == 3 else 1.))
        panel.visibility["nuclear"].setChecked(False)
        assert not edge.visible and not marks["nuclear", 7][1].visible
        panel.visibility["nuclear"].setChecked(True)
    ui.isolate.setChecked(False)
    window.viewer_toolbar.set_ndisplay(3)
    click_mask(display, nuclear.data_to_world((0, 1, 2)), direction=(1., 0., 0.))
    assert ui.nucleus == 2 and viewer.dims.ndisplay == 3
    assert window.project_state.matching_pair.current_result is result
    window.set_workflow("proofreading")
    assert ui.nucleus is None and not marks and not display.renderer.edges


def test_zero_link_inspection(qapp):
    from nuclear_segmentation.candidate_explorer import CandidateExplorer
    from nuclear_segmentation.matching_appearance import relationships

    result = population()
    cyto = AssociationExplorer()
    cyto.set_result(result, "")
    cyto.accepted.setCurrentIndex(0)
    cyto.objects.selectRow(2)
    assert cyto.selection.cytoplasmic_id == CYTO + 2 and cyto.selection.associated_ids == ()
    assert relationships(cyto.index, "cytoplasmic", CYTO + 2) == {("cytoplasmic", CYTO + 2): "selected"}
    nuc = CandidateExplorer()
    nuc.set_result(result, None)
    nuc.multiple.setChecked(False)
    pos = next(i for i, row in enumerate(nuc.objects.model().rows) if row[0] == 15)
    nuc.objects.selectRow(pos)
    assert nuc.nucleus == 15 and nuc.candidates.model().rowCount() == 0
    assert relationships(cyto.index, "nuclear", 15) == {("nuclear", 15): "selected"}
    assert not nuc.review.isEnabled()


def test_matching_canvas_click_dispatch(qapp, tmp_path):
    if qapp.platformName() in {"offscreen", "minimal"}:
        pytest.skip("Requires the Qt OpenGL canvas")
    window = DesktopWindow(settings=QSettings(str(tmp_path / "pick.ini"), QSettings.Format.IniFormat),
                           restore_workspace=False)
    try:
        window.show()
        assert window._apply_imported_dataset(_dataset(tmp_path / "pick.tif"))
        nuclear, cyto = inspection_pair(window)
        panel = choose_in_panel(window, nuclear, cyto)
        run_job(window.project_state.matching_pair)
        panel.class_colors.setChecked(True)
        display = window.matching_display
        widget = window.embedding.qt_viewer.canvas.native
        world = display.layers["nuclear"].data_to_world((0, 1, 3))
        for dimensions in (2, 3):
            window.viewer_toolbar.set_ndisplay(dimensions)
            camera = display.viewer.scene.camera
            display.viewer.dims.set_point(0, world[0])
            if dimensions == 3:
                camera.set_view_direction((1, 0, 0))
            camera.center = world
            QTest.qWait(120)
            for role, label in (("Nuclear", 2), ("Cytoplasmic", 99)):
                panel.pick_target.setCurrentText(role)
                center = camera.center
                section = display.viewer.dims.point[0]
                QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())
                QTest.qWait(50)
                assert panel.explorer_mode.currentIndex() == int(role == "Nuclear")
                assert display.renderer.marks[role.lower(), label][1].name.startswith("Selected")
                assert camera.center == center
                if dimensions == 2:
                    assert display.viewer.dims.point[0] == pytest.approx(section)
    finally:
        window.close()
        qapp.processEvents()


def test_picking_disjoint_populations(window_pair):
    window, _ = window_pair
    nuclear, cyto = measured_pair(window, crop=True)
    # Retain one nucleus and one cyto with no common voxels.
    from test_matching_pair import publish
    publish(window.project_state, nuclear, x=0, window=window)
    publish(window.project_state, cyto, x=3, window=window)
    panel = choose_in_panel(window, nuclear, cyto)
    result = run_job(window.project_state.matching_pair)
    assert result.nuclei[0].auto_class == "NO_MATCH"
    panel.class_colors.setChecked(True)
    display = window.matching_display
    for role, point in (("cytoplasmic", (0, 1, 3)), ("nuclear", (0, 1, 0))):
        layer = display.layers[role]
        panel.pick_target.setCurrentText(role.title())
        world = layer.data_to_world(point)
        display.viewer.dims.set_point(0, world[0])
        click_mask(display, world)
        assert set(display.renderer.marks) == {(role, 7)}
        ui = panel.explorer if role == "cytoplasmic" else panel.candidate_explorer
        assert ui.candidates.model().rowCount() == 0
        assert ui.accepted.currentIndex() == 0 if role == "cytoplasmic" else not ui.multiple.isChecked()
        assert ui.objects.currentIndex().siblingAtColumn(0).data() == "7"
        ui.isolate.click()
        if role == "cytoplasmic":
            ui.restore.click()
    np.testing.assert_array_equal(display.layers["nuclear"].colormap.map(7), COLORS["NO_MATCH"])
    assert "NO_MATCH" in window.navigation.legend_selection.text()
    assert not display.viewer.canvas.overlays.text.visible


def test_isolated_ray_skips_hidden_foreground(qapp):
    from napari.components import ViewerModel
    from nuclear_segmentation.mask_selection import pick_world_label
    viewer = ViewerModel(ndisplay=3)
    data = np.zeros((5, 3, 3), dtype=np.uint16)
    data[0] = 7
    data[4] = 11
    layer = viewer.add_labels(data, scale=(2., .5, .3), translate=(4., 1., 6.))
    point = layer.data_to_world((0, 1, 1))
    args = (layer, point, (0, 1, 2), (1., 0., 0.))
    assert pick_world_label(*args) == 7
    assert pick_world_label(*args, visible_ids={11}) == 11
    assert pick_world_label(*args, visible_ids=set()) is None
