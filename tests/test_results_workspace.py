from qtpy.QtTest import QTest

from test_desktop_shell import qapp, settings, window
from test_matching_pair import window_pair


def test_results_and_focus_restore_live_layout(window, qapp):
    window.set_workflow("matching")
    results = window.results
    window.toggle_results_action.setChecked(True)
    results.setCurrentWidget(results.pages["candidates"])
    window.vertical_splitter.setSizes([470, 190])
    QTest.qWait(30)
    original = window.workspace_snapshot()
    for action in (window.toggle_results_action, window.focus_action):
        action.toggle()
        qapp.processEvents()
        action.toggle()
        qapp.processEvents()
        snapshot = window.workspace_snapshot()
        assert snapshot.results_tab == original.results_tab
        assert snapshot.results_height == original.results_height
        assert snapshot.splitter_sizes == original.splitter_sizes
    window.save_custom_layout()
    for name in ("Image Focus", "Proofreading Workspace", "Data Inspection", "Default Workspace"):
        window.set_configuration(name)
    window.restore_custom_layout()
    assert window.workspace_snapshot().results_tab == original.results_tab
    assert window.workspace_snapshot().results_height == original.results_height


def test_header_selection_and_progress_use_session_state(window):
    from test_desktop_normalization import _dataset
    from pathlib import Path
    state = window.project_state
    state.set_dataset(_dataset(Path("header.tif")))
    other = state.create_session("Membrane", scientific_role="cytoplasmic", channel_index=1)
    selector = window.session_selector
    index = selector.findData(other.session_id)
    selector.activated.emit(index)
    assert state.active_session is other and selector.currentText() == "Membrane · cytoplasmic"
    assert window.navigation._buttons["project"].accessibleDescription() == "Complete"
    window.set_workflow("segmentation")
    assert window.navigation._buttons["segmentation"].accessibleDescription() == "Pending"


def test_bottom_table_state_survives_collapse(window_pair, qapp):
    from qtpy.QtCore import Qt
    from test_guided_merge import open_candidates
    window, _ = window_pair
    window.show()
    _, _, explorer = open_candidates(window)
    table = explorer.candidates
    table.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    window.results.setCurrentWidget(window.results.pages["candidates"])
    table.setColumnWidth(0, 1200)
    table.selectRow(0)
    qapp.processEvents()
    table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
    def view():
        return (explorer.nucleus, explorer.selection().ids, table.model().order,
                table.horizontalScrollBar().value(), window.results.currentWidget())
    before = view()
    assert before[1] and before[3] > 0
    window.toggle_results_action.setChecked(False)
    window.toggle_results_action.setChecked(True)
    qapp.processEvents()
    assert view() == before
