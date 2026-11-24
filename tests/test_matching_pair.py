"""Scientific and embedded-viewer contracts for integrated Matching."""
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest
from qtpy.QtCore import QSettings, Qt
from qtpy.QtTest import QTest

from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.filtering_workspace import calculate_filtering_prerequisites
from nuclear_segmentation.geometry_edit import GeometryEdit
from nuclear_segmentation.matching_pair import MATCHING_LAYER
from nuclear_segmentation.scientific_input import scientific_input_provenance
from test_cellpose_execution import _fake_result, _state
from test_desktop_normalization import FakeQtViewer, _dataset
from test_mask_review_gui import qapp
from test_native_project import _wait, open_saved, project, save
from test_session_validity import measure, revisions, run_and_measure


def publish(state, session, *, retained=(7,), x=1, window=None):
    state.select_session(session.session_id)
    raw = np.zeros(state.working_dataset.standardized_shape[1:], dtype=np.int32)
    raw[:, 1:3, x:x + 2] = 7
    raw[:, 3, 1:4] = 500
    channel = state.segmentation_channel
    provenance = scientific_input_provenance(
        state, dict(SEGMENTATION_CHANNEL=channel, Z_CORRECTIONS={}, CELLPOSE_NORMALIZE=True))
    if window is None:
        config = {"SEGMENTATION_CHANNEL": channel, "CHANNELS": state.analysis_channel_records()}
    else:
        panel = window.context_panel.segmentation_page
        panel.model_name.setText("test-model")
        config = panel.scientific_config()
    state.set_segmentation_result(dict(
        masks=raw, mask_shape=raw.shape, object_count=2, device="cpu",
        input_provenance=provenance, scientific_config=config,
    ))
    if window is None:
        filter_labels(state, retained)
    else:
        measured = calculate_filtering_prerequisites(state, config)
        window.filtering._calculated(dict(measured, retained_ids=retained, retained_count=len(retained),
                                           filtered_masks=np.where(np.isin(raw, retained), raw, 0)))
    return raw


def filter_labels(state, retained):
    segmentation = state.segmentation_result
    raw = segmentation.get("edited_masks", segmentation["masks"])
    provenance = segmentation["input_provenance"]
    state.set_filtering_result(dict(
        session_id=state.active_session_id, geometry_revision=state.active_session.geometry_revision,
        raw_mask_shape=raw.shape, retained_ids=retained, retained_count=len(retained),
        filtered_masks=np.where(np.isin(raw, retained), raw, 0),
        input_segmentation_provenance=provenance, measurement_provenance=provenance,
        scientific_roi=None, settings={},
    ))


def select_pair(state, nuclear, cytoplasmic):
    pair = state.matching_pair
    pair.select("nuclear", nuclear.session_id)
    pair.select("cytoplasmic", cytoplasmic.session_id)
    return pair


@pytest.fixture
def inputs(tmp_path):
    state, _ = _state(tmp_path)
    nuclear = state.create_session("Active zone", channel_index=1)
    cyto = state.create_session("Cell body", scientific_role="cytoplasmic", channel_index=0)
    pair = select_pair(state, nuclear, cyto)
    return state, nuclear, cyto, pair


def test_pair_captures_validated_projection_without_density(inputs):
    state, nuclear, cyto, pair = inputs
    assert pair.capture() is None
    assert "Run segmentation" in pair.inspect()[1]["nuclear"].message
    publish(state, nuclear)
    publish(state, cyto, retained=(500,))
    snapshot = pair.capture()
    assert snapshot is not None
    assert snapshot.grid.axes == "ZYX" and snapshot.grid.series_identifier == "0"
    assert snapshot.grid.shape_zyx == (3, 5, 7)
    assert snapshot.nuclear.source_channel_index == 1 and snapshot.cytoplasmic.source_channel_index == 0
    assert snapshot.nuclear.owner == state.session_revision(nuclear.session_id)
    assert snapshot.nuclear.measurement_revision == nuclear.measurement_revision
    assert snapshot.nuclear.retained_ids == (7,) and snapshot.cytoplasmic.retained_ids == (500,)
    assert not nuclear.quantification_is_current and not nuclear.scientific_roi_reviewed
    assert set(np.unique(snapshot.nuclear.labels)) == {0, 7}
    assert np.shares_memory(snapshot.nuclear.labels, nuclear.filtering_result["filtered_masks"])
    with pytest.raises(ValueError):
        snapshot.nuclear.labels[0, 0, 0] = 10

    state.rename_session(nuclear.session_id, "Renamed")
    state.select_session(nuclear.session_id)
    assert pair.is_current(snapshot)
    with pytest.raises(ValueError, match="nuclear session"):
        pair.select("nuclear", cyto.session_id)
    with pytest.raises(ValueError, match="current project"):
        pair.select("cytoplasmic", "another-project-id")


def test_measurement_only_recovery_empty_and_population_change(inputs):
    state, nuclear, cyto, pair = inputs
    publish(state, nuclear)
    publish(state, cyto, retained=())
    before = pair.capture()
    assert before.cytoplasmic.retained_ids == ()
    assert not before.cytoplasmic.labels.any()
    assert "empty" in pair.inspect()[1]["cytoplasmic"].message

    other = state.create_session("Other", channel_index=0)
    publish(state, other)
    state.invalidate_segmentation(active_only=True)
    assert pair.is_current(before)
    state.select_session(cyto.session_id)
    state.invalidate_filtering()
    assert pair.capture() is None and not pair.is_current(before)
    assert before.cytoplasmic.owner == state.session_revision()
    assert "Recalculate measurements" in pair.inspect()[1]["cytoplasmic"].message
    filter_labels(state, ())
    recovered = pair.capture()
    assert recovered.population_identity == before.population_identity
    assert not pair.is_current(before)
    filter_labels(state, (7,))
    assert pair.capture().population_identity != recovered.population_identity
    assert not before.cytoplasmic.labels.any()


def test_edits_undo_and_provisional_ids_require_remeasurement(inputs):
    state, nuclear, cyto, pair = inputs
    publish(state, cyto)
    publish(state, nuclear)
    before = pair.capture()
    edit = GeometryEdit(state, filtered=True)
    edit.preview[0, 1, 1] = 0
    assert pair.is_current(before)
    assert before.nuclear.labels[0, 1, 1] == 7
    edit.commit()
    assert state.provisional_ids == [7]
    assert pair.capture() is None
    assert "Committed geometry has changed" in pair.inspect()[1]["nuclear"].message
    filter_labels(state, (7,))
    edited = pair.capture()
    assert edited.nuclear.labels[0, 1, 1] == 0
    assert edited.population_identity != before.population_identity
    GeometryEdit.undo(state)
    assert pair.capture() is None
    filter_labels(state, (7,))
    assert not pair.is_current(before) and not pair.is_current(edited)
    np.testing.assert_array_equal(before.nuclear.labels, pair.capture().nuclear.labels)
    state.invalidate_segmentation(active_only=True)
    assert "Segmentation outdated" in pair.inspect()[1]["nuclear"].message
    publish(state, nuclear)
    assert pair.capture().nuclear.owner != before.nuclear.owner


@pytest.mark.parametrize(("key", "value"), [
    ("source_path", "/other/source.tif"),
    ("source_series", 4),
    ("working_shape_czyx", (2, 3, 7, 5)),
    ("voxel_spacing_zyx_um", (1, 1, 1)),
    ("working_offset_yx", (2, 1)),
    ("crop_type", "polygon"),
])
def test_incompatible_acquisition_or_grid(inputs, key, value):
    state, nuclear, cyto, pair = inputs
    publish(state, cyto)
    publish(state, nuclear)
    nuclear.segmentation_result["input_provenance"] = dict(
        nuclear.segmentation_result["input_provenance"], **{key: value})
    assert pair.capture() is None
    assert "Acquisition or calibrated grid is incompatible" in pair.inspect()[1]["nuclear"].message


@pytest.mark.parametrize("override", [
    {"time_index": 1}, {"series_identifier": "another-series"}, {"axes_override": "CXYZ"},
])
def test_working_grid_must_be_from_the_same_import(inputs, override):
    state, nuclear, cyto, pair = inputs
    publish(state, cyto)
    publish(state, nuclear)
    assert pair.capture() is not None
    state.working_dataset = replace(state.working_dataset, **override)
    assert pair.capture() is None
    assert "compatible working grid" in pair.inspect()[1]["cytoplasmic"].message


@pytest.mark.parametrize("problem", ["wrong-voxel", "negative", "float", "missing-id", "owner", "revision"])
def test_filtering_must_belong_to_exact_effective_geometry(inputs, problem):
    state, nuclear, cyto, pair = inputs
    publish(state, cyto)
    publish(state, nuclear)
    before = pair.capture()
    record = dict(nuclear.filtering_result)
    if problem == "wrong-voxel":
        record["filtered_masks"] = np.roll(record["filtered_masks"], 1, axis=2)
    elif problem == "negative":
        record["filtered_masks"] = -record["filtered_masks"]
    elif problem == "float":
        record["filtered_masks"] = record["filtered_masks"].astype(float)
    elif problem == "missing-id":
        record.update(retained_ids=(7, 21), retained_count=2)
    elif problem == "owner":
        record["session_id"] = cyto.session_id
    else:
        record["geometry_revision"] -= 1
    state.set_filtering_result(record)
    assert pair.capture() is None
    assert not pair.is_current(before)


@pytest.fixture
def window_pair(qapp, tmp_path):
    window = DesktopWindow(qt_viewer_factory=FakeQtViewer,
                           settings=QSettings(str(tmp_path / "matching.ini"), QSettings.Format.IniFormat),
                           restore_workspace=False)
    dataset = _dataset(tmp_path / "reference.tif")
    try:
        assert window._apply_imported_dataset(dataset)
        yield window, dataset
    finally:
        window.close()


def measured_pair(window, *, crop=False):
    state = window.project_state
    if crop:
        working = state.apply_crop((1, 7, 2, 8), [(1, 2), (7, 8)])
        window._display_working_dataset(working)
    nuclear = state.create_session("Nuclei", channel_index=1)
    cyto = state.create_session("Cytoplasm", scientific_role="cytoplasmic", channel_index=0)
    publish(state, cyto, x=2, window=window)
    publish(state, nuclear, window=window)
    return nuclear, cyto


def choose_in_panel(window, nuclear, cyto):
    window.set_workflow("matching")
    panel = window.context_panel.matching_page
    for role, session in zip(("nuclear", "cytoplasmic"), (nuclear, cyto), strict=True):
        selector = panel.selectors[role]
        selector.setCurrentIndex(selector.findData(session.session_id))
    return panel


def test_role_selectors_stay_independent_across_rename_and_pair_replacement(window_pair):
    window, dataset = window_pair
    state = window.project_state
    nuclear, cyto = measured_pair(window)
    alternate = state.create_session("Other cytoplasmic", scientific_role="cytoplasmic", channel_index=1)
    publish(state, alternate, window=window)
    panel = choose_in_panel(window, nuclear, cyto)
    for role in panel.selectors:
        ids = {panel.selectors[role].itemData(i) for i in range(1, panel.selectors[role].count())}
        assert ids == {s.session_id for s in state.sessions.values() if s.scientific_role == role}
        assert len(ids) == 2
    assert state.active_session_id == alternate.session_id
    assert "Nuclei · MAP2" == panel.selectors["nuclear"].currentText()
    assert "Segmentation current · Filtering current · 1 retained" in panel.summaries["nuclear"].text()
    panel.show_inputs.click()
    old_layer = window.matching_display.layers["nuclear"]
    snapshot = state.matching_pair.capture()
    state.rename_session(nuclear.session_id, "Renamed nuclei")
    assert window.matching_display.layers["nuclear"] is old_layer
    assert "Renamed nuclei" in old_layer.name
    assert state.matching_pair.is_current(snapshot)
    selector_n = panel.selectors["nuclear"]
    selector_n.setCurrentIndex(selector_n.findData(state.nuclear_session.session_id))
    assert "Segmentation missing" in panel.summaries["nuclear"].text()
    assert "Run segmentation" in panel.compatibility.text()
    assert not window.matching_display.layers
    selector_n.setCurrentIndex(selector_n.findData(nuclear.session_id))
    assert len(window.matching_display.layers) == 2
    selector = panel.selectors["cytoplasmic"]
    selector.setCurrentIndex(selector.findData(alternate.session_id))
    viewer = window.embedding.viewer
    assert old_layer not in viewer.layers
    assert state.matching_pair.capture().cytoplasmic.owner.session_id == alternate.session_id
    assert not state.matching_pair.is_current(snapshot)


def test_layers_lifecycle_read_only_and_independent_appearance(window_pair):
    window, dataset = window_pair
    state, viewer = window.project_state, window.embedding.viewer
    nuclear, cyto = measured_pair(window, crop=True)
    ordinary = window.segmentation.result_layer
    window.mask_display.show("raw")
    preferences = deepcopy(nuclear.display_state)
    before = {s.session_id: revisions(s) for s in state.sessions.values()}
    images = {layer for layer in viewer.layers if layer.metadata.get("nucytoseg_imported")}
    panel = choose_in_panel(window, nuclear, cyto)
    token = state.matching_pair.capture()
    panel.show_inputs.click()
    panel.visibility["nuclear"].setChecked(False)
    panel.opacity["nuclear"].setValue(20)
    panel.opacity["cytoplasmic"].setValue(85)
    for _ in range(3):
        window.set_workflow("matching")
        assert not panel.show_inputs.isEnabled()
        assert panel.selectors["nuclear"].currentData() == nuclear.session_id
        assert panel.selectors["cytoplasmic"].currentData() == cyto.session_id
        layer_n, layer_c = (window.matching_display.layers[role] for role in ("nuclear", "cytoplasmic"))
        assert sum(bool(layer.metadata.get(MATCHING_LAYER)) for layer in viewer.layers) == 2
        assert images <= set(viewer.layers)
        ordinary.visible = True
        assert not ordinary.visible
        for layer, session in ((layer_n, nuclear), (layer_c, cyto)):
            assert not layer.editable and not layer.data.flags.writeable
            assert np.shares_memory(layer.data, session.filtering_result["filtered_masks"])
            assert tuple(layer.scale) == (2.5, 0.6, 0.3)
            assert tuple(layer.translate) == (0.0, 0.6, 0.6)
            assert not state.owns_mask_layer(layer)
            assert window.layer_panel.model.item_for_layer(layer).parent().text() == "Matching Layers"
            viewer.layers.selection.active = layer
            assert not window.viewer_toolbar.activate_tool("paint")
            assert not window.viewer_toolbar.activate_tool("review")
            assert window.mask_context_menu.layer is None
            assert window.mask_selection.selected_layer is None
        assert not np.array_equal(layer_n.colormap.map(7), layer_c.colormap.map(7))
        assert not layer_n.visible and layer_c.visible
        assert layer_n.opacity == .20 and layer_c.opacity == .85
        layer_n.visible = True
        assert panel.visibility["nuclear"].isChecked()
        panel.visibility["nuclear"].setChecked(False)
        window.set_workflow("proofreading")
        assert ordinary.visible and viewer.layers.selection.active is ordinary
        assert not window.matching_display.layers
        assert layer_n not in viewer.layers and layer_c not in viewer.layers
        layer_n.visible = True
        layer_c.opacity = .15
        assert not panel.visibility["nuclear"].isChecked()
        assert panel.opacity["cytoplasmic"].value() == 85
        assert nuclear.display_state == preferences
    assert state.active_session_id == nuclear.session_id
    assert {s.session_id: revisions(s) for s in state.sessions.values()} == before
    assert state.matching_pair.is_current(token)


def test_inactive_session_invalidation_and_project_lifecycle(window_pair):
    window, dataset = window_pair
    state, viewer = window.project_state, window.embedding.viewer
    nuclear, cyto = measured_pair(window)
    panel = choose_in_panel(window, nuclear, cyto)
    panel.show_inputs.click()
    old_context = state.matching_pair
    token = old_context.capture()
    layers = dict(window.matching_display.layers)
    unrelated = state.create_session("Unrelated", channel_index=0)
    state.select_session(unrelated.session_id)
    state.invalidate_segmentation(active_only=True)
    assert window.matching_display.layers == layers
    assert state.matching_pair.is_current(token)

    # Renaming an inactive source channel invalidates that input through ProjectState.
    state.update_channel(0, name="New source label")
    assert not cyto.segmentation_is_current and state.active_session is unrelated
    assert not window.matching_display.layers
    assert "Segmentation outdated" in panel.compatibility.text()
    assert "Segmentation outdated" in panel.summaries["cytoplasmic"].text()
    window.set_workflow("proofreading")
    window.set_workflow("matching")
    assert not window.matching_display.layers and not panel.show_inputs.isEnabled()
    publish(state, cyto, window=window)
    assert "Nuclear · Measurements are outdated" in panel.compatibility.text()
    publish(state, nuclear, window=window)
    assert len(window.matching_display.layers) == 2
    assert not state.matching_pair.is_current(token)
    working = state.apply_crop((1, 7, 2, 8), [(1, 2), (7, 8)])
    window._display_working_dataset(working)
    assert not window.matching_display.layers
    for session in (nuclear, cyto):
        publish(state, session, window=window)
    assert len(window.matching_display.layers) == 2
    window._apply_manual_calibration((3.5, .7, .4))
    assert not window.matching_display.layers
    for session in (nuclear, cyto):
        publish(state, session, window=window)
    assert len(window.matching_display.layers) == 2
    for layer in viewer.layers:
        if layer.metadata.get(MATCHING_LAYER) or layer.metadata.get("nucytoseg_imported"):
            assert tuple(layer.scale) == (3.5, .7, .4)
            assert tuple(layer.translate) == (0., .7, .8)
    panel.visibility["nuclear"].setChecked(False)
    panel.opacity["cytoplasmic"].setValue(25)
    assert window._apply_imported_dataset(replace(dataset, time_index=1))
    assert not window.matching_display.layers
    assert not window.mask_display.suspended
    assert panel.selectors["nuclear"].currentData() is None
    assert old_context.capture() is None
    assert not state.matching_pair.is_current(token)
    assert not any(layer.metadata.get(MATCHING_LAYER) for layer in viewer.layers)
    nuclear, cyto = measured_pair(window)
    panel = choose_in_panel(window, nuclear, cyto)
    assert panel.show_inputs.isEnabled() and not window.matching_display.layers
    panel.show_inputs.click()
    assert all(layer.visible and layer.opacity == .60 for layer in window.matching_display.layers.values())
    panel.visibility["cytoplasmic"].setChecked(False)
    panel.opacity["nuclear"].setValue(25)
    window.set_workflow("proofreading")
    assert window.close_project(confirm=False)
    assert not window.matching_display.layers and not viewer.layers
    assert window.matching_display._previous_layer is None
    assert window._apply_imported_dataset(dataset)
    nuclear, cyto = measured_pair(window)
    panel = choose_in_panel(window, nuclear, cyto)
    assert panel.show_inputs.isEnabled() and not window.matching_display.layers
    panel.show_inputs.click()
    assert all(layer.visible and layer.opacity == .60 for layer in window.matching_display.layers.values())


def test_measured_pair_can_be_reselected_after_checkpoint(project, tmp_path):
    window, app, _, _, warnings = project
    state = window.project_state
    window.segmentation.runner = lambda scientific, progress: _fake_result(scientific)
    nuclear = state.active_session
    cyto = state.create_session("Somata", scientific_role="cytoplasmic", channel_index=1)
    for session in (nuclear, cyto):
        state.select_session(session.session_id)
        run_and_measure(window, app)
    state.select_session(nuclear.session_id)
    state.clear_quantification_result()
    state.select_session(cyto.session_id)
    panel = choose_in_panel(window, nuclear, cyto)
    assert panel.show_inputs.isEnabled() and not nuclear.quantification_is_current
    panel.show_inputs.click()
    assert len(window.matching_display.layers) == 2
    before = state.matching_pair.capture()
    # Simulate a previously validated checkpoint whose unedited filtering lacked ownership fields.
    for session in (nuclear, cyto):
        for key in ("session_id", "geometry_revision"):
            session.filtering_result.pop(key)
    folder = tmp_path / "matching checkpoint"
    save(window, app, folder)
    open_saved(window, app, folder)
    assert not warnings
    assert state.matching_pair.capture() is None
    choose_in_panel(window, state.sessions[nuclear.session_id], state.sessions[cyto.session_id])
    after = state.matching_pair.capture()
    assert after is not None and after.nuclear.retained_ids == before.nuclear.retained_ids
    assert after.cytoplasmic.retained_ids == before.cytoplasmic.retained_ids
    assert not state.matching_pair.is_current(before)


@pytest.fixture
def gui_filtered_pair(project):
    window, app, _, _, _ = project
    state = window.project_state
    nuclear = state.active_session
    cyto = state.create_session("Somata", scientific_role="cytoplasmic", channel_index=1)
    window.segmentation.runner = lambda scientific, progress: _fake_result(scientific)
    window.set_workflow("mask_review")
    review = window.context_panel.mask_review_page
    for session in (cyto, nuclear):
        state.select_session(session.session_id)
        run_and_measure(window, app)
        review.minimum_volume.editor.setValue(0)
        review.minimum_sphericity.editor.setValue(0)
        review.z_guard_mode.setCurrentText("none")
        review.intensity_master.setChecked(False)
        _wait(app, lambda: not review._filter_timer.isActive())
        assert session.filtering_result["retained_ids"] == (1,)
    return window, app, nuclear, cyto


def test_automatic_restoration_after_session_switch_and_remeasurement(gui_filtered_pair):
    window, app, nuclear, cyto = gui_filtered_pair
    state, viewer = window.project_state, window.embedding.viewer
    panel = choose_in_panel(window, nuclear, cyto)
    panel.show_inputs.click()
    panel.opacity["cytoplasmic"].setValue(40)
    token = state.matching_pair.capture()

    window.set_workflow("proofreading")
    state.select_session(cyto.session_id)
    window.mask_display.show("raw")
    ordinary_cyto = window.segmentation.result_layer
    cyto_preferences = deepcopy(cyto.display_state)
    window.set_workflow("matching")
    assert state.active_session is cyto and state.matching_pair.is_current(token)
    assert len(window.matching_display.layers) == 2 and not ordinary_cyto.visible
    window.set_workflow("proofreading")
    assert ordinary_cyto.visible and viewer.layers.selection.active is ordinary_cyto
    assert cyto.display_state == cyto_preferences

    state.select_session(nuclear.session_id)
    edit = GeometryEdit(state, filtered=True)
    edit.preview[0, 1, 2] = 0
    edit.commit()
    assert state.provisional_ids == [1] and not nuclear.filtering_is_current
    window.set_workflow("matching")
    assert not window.matching_display.layers and not panel.show_inputs.isEnabled()
    assert not window.mask_display.suspended
    assert "Recalculate measurements in Mask Review" in panel.compatibility.text()
    assert "Filtering outdated" in panel.summaries["nuclear"].text()
    assert "No retained nuclear objects" not in panel.summaries["nuclear"].text()
    window.set_workflow("mask_review")
    measure(window, app)
    assert not window.matching_display.layers
    assert state.filtering_result["retained_ids"] == (1,)
    recovered = state.matching_pair.capture()
    assert recovered.population_identity != token.population_identity
    window.set_workflow("matching")
    assert len(window.matching_display.layers) == 2
    assert not panel.show_inputs.isEnabled()
    assert window.matching_display.layers["nuclear"].data[0, 1, 2] == 0
    assert window.matching_display.layers["cytoplasmic"].opacity == .40
    assert state.matching_pair.is_current(recovered)


def test_empty_feedback_from_gui_filters_in_narrow_context(gui_filtered_pair):
    window, app, nuclear, cyto = gui_filtered_pair
    state = window.project_state
    window.context_container.setFixedWidth(320)
    window.layers_section.set_expanded(False)
    window.resize(1150, 660)
    window.show()
    panel = choose_in_panel(window, nuclear, cyto)
    panel.show_inputs.click()
    geometry_owners = tuple(state.session_revision(session.session_id) for session in (nuclear, cyto))

    for session, empty, expected in (
        (nuclear, True, (True, False)),
        (cyto, True, (True, True)),
        (nuclear, False, (False, True)),
        (cyto, False, (False, False)),
    ):
        window.set_workflow("mask_review")
        state.select_session(session.session_id)
        review = window.context_panel.mask_review_page
        volume = review.minimum_volume.editor
        if empty:
            assert volume.maximum() > session.filtering_result["properties"]["volume_um3"].max()
        volume.setValue(volume.maximum() if empty else 0)
        _wait(app, lambda: session.filtering_result["retained_count"] == int(not empty))
        token = state.matching_pair.capture()
        window.set_workflow("matching")
        scroll = window.context_panel.stack.currentWidget()
        app.processEvents()
        assert len(window.matching_display.layers) == 2
        assert "Compatible grid" in panel.compatibility.text()
        for (role, summary), role_empty in zip(panel.summaries.items(), expected, strict=True):
            label = window.matching_display.layers[role]
            assert label.visible and not label.editable
            assert bool(label.data.any()) == (not role_empty)
            assert f"{int(not role_empty)} retained" in summary.text()
            warning = f"No retained {role} objects. Review filter settings."
            assert (warning in summary.text()) == role_empty
            if role_empty:
                assert "0 retained · Validated" in summary.text()
            assert scroll.verticalScrollBar().maximum() > 0
            scroll.ensureWidgetVisible(summary, 0, 8)
            _wait(app, lambda: summary.visibleRegion().boundingRect() == summary.rect())
            assert summary.isVisible() and summary.wordWrap()
            assert summary.height() >= summary.heightForWidth(summary.width())
        assert state.matching_pair.is_current(token)
        assert all(session.segmentation_is_current and session.filtering_is_current for session in (nuclear, cyto))
        assert tuple(state.session_revision(session.session_id) for session in (nuclear, cyto)) == geometry_owners


def test_matching_on_native_qt_canvas_2d_and_3d(qapp, tmp_path):
    if qapp.platformName() in {"offscreen", "minimal"}:
        pytest.skip("The embedded OpenGL canvas needs a window system.")
    window = DesktopWindow(settings=QSettings(str(tmp_path / "canvas.ini"), QSettings.Format.IniFormat),
                           restore_workspace=False)
    try:
        window.show()
        dataset = _dataset(tmp_path / "canvas.tif")
        assert window._apply_imported_dataset(dataset)
        nuclear, cyto = measured_pair(window, crop=True)
        panel = choose_in_panel(window, nuclear, cyto)
        window.navigation.set_collapsed(True)
        panel.show_inputs.click()
        viewer = window.embedding.viewer
        canvas = window.embedding.qt_viewer.canvas
        layers = tuple(window.matching_display.layers.values())
        assert len(layers) == 2
        for dimensions in (2, 3, 2):
            assert window.viewer_toolbar.set_ndisplay(dimensions)
            viewer.reset_view()
            QTest.qWait(100)
            assert canvas.native.isVisible() and viewer.dims.ndisplay == dimensions
            composite = canvas.screenshot()
            assert not composite.isNull()
            for role, control in panel.visibility.items():
                control.setChecked(False)
                assert canvas.screenshot() != composite, f"{role} layer is missing from the {dimensions}D render"
                control.setChecked(True)
            for layer in layers:
                assert layer.visible and not layer.editable
                viewer.layers.selection.active = layer
                QTest.mouseClick(canvas.native, Qt.MouseButton.RightButton, pos=canvas.native.rect().center())
                assert window.mask_context_menu.menu is None
                assert window.mask_selection.selected_mask_id is None
        window.set_workflow("proofreading")
        assert not any(layer in viewer.layers for layer in layers)
        window.set_workflow("matching")
        restored = tuple(window.matching_display.layers.values())
        assert len(restored) == 2 and not panel.show_inputs.isEnabled()
        assert all(layer.visible and not layer.editable for layer in restored)
        QTest.qWait(100)
        assert not canvas.screenshot().isNull()
    finally:
        window.close()
        qapp.processEvents()
