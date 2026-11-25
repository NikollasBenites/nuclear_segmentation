from qtpy.QtCore import QPoint, QSize
from qtpy.QtWidgets import QToolButton

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.analysis_objectives import DENSITY_ROUTES
from test_desktop_shell import FakeQtViewer, qapp, settings
from test_matching_pair import window_pair, choose_in_panel
from test_matching_explorer import inspection_pair
from test_integrated_matching import run_job
from test_native_project import project, save, _wait


def test_welcome_and_minimum_workspace(qapp, settings):
    w = DesktopWindow(qt_viewer_factory=FakeQtViewer, settings=settings, show_welcome=True)
    try:
        w.resize(1024, 680)
        w.show()
        qapp.processEvents()
        assert w.screens.currentWidget() is w.welcome
        profile, theme, objective, metrics, _ = w.interface.controls[0]
        profile.setCurrentText("Basic")
        theme.setCurrentText("Light")
        objective.setCurrentText("Density & Quantification")
        for name, route in DENSITY_ROUTES.items():
            assert metrics.model().item(metrics.findData(name)).isEnabled() == bool(route)
        before = w.project_state.active_session_id
        w.interface.set_metric("Marker-positive fraction")
        assert w.interface.metric == "Nuclear density" and w.project_state.active_session_id == before
        new = next(b for b in w.welcome.findChildren(QToolButton) if b.defaultAction() is w.project_actions[0])
        new.click()
        qapp.processEvents()
        assert w.screens.currentWidget() is w.workspace_page and w.interface.guide.isVisible()
        assert w.theme_combo.currentText() == "Light" and w.interface.selector.currentText() == "Basic"
        assert not w.interface.advance.isEnabled()
        assert w.size() == QSize(1024, 680)
        for control in (w.session_selector, w.interface.selector, w.theme_combo, w.interface.objective_selector):
            assert control.isVisible() and w.rect().contains(control.mapTo(w, QPoint(control.width()-1, 1)))
    finally:
        w.close()


def test_guided_progression_and_explicit_skip(window_pair):
    w, _ = window_pair
    ui = w.interface
    ui.selector.setCurrentText("Basic")
    ui.objective_selector.setCurrentText("Nuclear Segmentation")
    w.set_workflow("project")
    ui.advance.click()
    assert w.navigation.current_workflow == "preprocessing"
    assert not ui.advance.isEnabled() and ui.skip.isEnabled()
    ui.skip.click()
    assert w.navigation.current_workflow == "segmentation"
    assert w.navigation._buttons["preprocessing"].accessibleDescription() == "Skipped"
    assert not ui.advance.isEnabled()
    ui.previous.click()
    assert w.navigation.current_workflow == "preprocessing"
    ui.objective_selector.setCurrentText("Cytoplasmic Segmentation")
    assert not ui.skip.isEnabled() and "cytoplasmic session" in ui.prerequisite("segmentation")
    assert w.navigation._buttons["quantification"].isHidden()


def test_profile_objective_and_transaction_share_one_state(window_pair):
    w, _ = window_pair
    nuclear, cyto = inspection_pair(w)
    p = choose_in_panel(w, nuclear, cyto)
    pair = w.project_state.matching_pair
    result = run_job(pair)
    pair.review.decide(result, 2, "Matched", 99)
    w.project_state.association_review_changed.emit()
    w.matching_display.select_object("nuclear", 2)
    def snapshot():
        return (w.project_state.session_revision(), pair.pending_settings, tuple(pair.review.events),
                w.context_panel.segmentation_page.pending_config(), w.project_dirty,
                p.candidate_explorer.nucleus, tuple(w.embedding.viewer.layers))
    original = snapshot()
    advanced = w.context_panel.segmentation_page.advanced_section
    advanced.set_expanded(True)
    ui = w.interface
    ui.selector.setCurrentText("Basic")
    assert not advanced.expanded
    ui.objective_selector.setCurrentText("Density & Quantification")
    ui.set_metric("Matched nuclear density")
    assert "matching" in ui.route and "quantification" in ui.route
    ui.selector.setCurrentText("Advanced")
    assert advanced.expanded and pair.current_result is result and snapshot() == original
    w._choose_review_mode(1)
    w.geometry_panel.start()
    try:
        edit = w.geometry_panel.edit
        ui.selector.setCurrentText("Basic")
        assert w.geometry_panel.edit is edit and not ui.advance.isEnabled() and not ui.previous.isEnabled()
        assert not w.review_mode.isEnabled() and not w.session_selector.isEnabled()
    finally:
        w.geometry_panel.discard()


def test_recent_project_opens_native_checkpoint(project, tmp_path):
    w, app, _, _, warnings = project
    folder = tmp_path / "Recent analysis"
    save(w, app, folder)
    current = w.project_state.active_session_id
    w.close_project()
    assert w.screens.currentWidget() is w.welcome
    item = w.interface.recents.item(0)
    assert item.text() == folder.name
    w.interface.recents.itemActivated.emit(item)
    _wait(app, lambda: w._import_thread is None)
    assert not warnings and w.project_directory == folder
    assert w.project_state.active_session_id == current and w.screens.currentWidget() is w.workspace_page
