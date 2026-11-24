"""Scientific revision and ownership boundaries during desktop proofreading."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from nuclear_segmentation.geometry_edit import GeometryEdit
from nuclear_segmentation.z_correction import DEFAULTS, correct_z
from test_cellpose_execution import _fake_result
from test_native_project import project, save, open_saved, _wait
from test_scientific_input import _z_report


def revisions(session):
    return session.input_revision, session.geometry_revision, session.measurement_revision


def run_and_measure(window, app):
    window.context_panel.segmentation_page.model_name.setText("test-model")
    assert window.segmentation.run()
    _wait(app, lambda: not window.segmentation.busy)
    measure(window, app)


def measure(window, app):
    assert window.filtering.calculate(), window.filtering.working_status
    _wait(app, lambda: not window.filtering.busy)
    assert window.project_state.filtering_is_current, window.filtering.working_status


@pytest.mark.parametrize("cyto_first", [True, False])
def test_switch_execute_and_proofread_both_directions(project, tmp_path, cyto_first):
    window, app, _, _, warnings = project
    state = window.project_state
    window.segmentation.runner = lambda scientific, progress: _fake_result(scientific)
    cyto = state.create_session("MAP2", scientific_role="cytoplasmic", channel_index=1)
    first, other = (cyto, state.nuclear_session) if cyto_first else (state.nuclear_session, cyto)
    state.select_session(other.session_id)
    run_and_measure(window, app)
    for session in (first, other):
        state.select_session(session.session_id)
        prep = window.context_panel.preprocessing_page
        mode = "disabled" if session is cyto else "percentile"
        prep.normalization_mode_combo.setCurrentIndex(prep.normalization_mode_combo.findData(mode))
        assert window.normalization.calculate(prep.normalization_options())
        _wait(app, lambda: not window.normalization.busy)
        assert window.normalization.apply()
        run_and_measure(window, app)
        review = window.context_panel.mask_review_page
        review.minimum_volume.setValue(0)
        review.minimum_sphericity.setValue(0)
        review.z_guard_mode.setCurrentText("none")
        window.filtering.apply_filters()
        assert state.proofreading_ids == (1,)
        for x in (2, 3):
            edit = GeometryEdit(state, filtered=True)
            edit.preview[0, 1, x] = 0
            edit.commit()
        measure(window, app)
        state.update_channel(0, visible=session is cyto)
        if session is first:
            original = session.segmentation_result
            filtering = session.filtering_result
            previous = revisions(session)
            labels = original["edited_masks"].copy()
            raw = original["masks"].copy()
            retained = tuple(filtering["retained_ids"])
            settings = deepcopy(filtering["settings"])
            properties = filtering["properties"].copy()
    for session in (first, other, first):
        state.select_session(session.session_id)
        app.processEvents()
        assert session.segmentation_is_current and session.filtering_is_current
        assert prep.normalization_mode_combo.currentData() == ("disabled" if session is cyto else "percentile")
    assert first.segmentation_result is original and first.filtering_result is filtering
    assert revisions(first) == previous
    assert first.filtering_result["retained_ids"] == retained
    assert first.filtering_result["settings"] == settings
    pd.testing.assert_frame_equal(first.filtering_result["properties"], properties)
    np.testing.assert_array_equal(first.segmentation_result["masks"], raw)
    np.testing.assert_array_equal(first.segmentation_result["edited_masks"], labels)
    folder = tmp_path / "both edited"
    save(window, app, folder)
    open_saved(window, app, folder)
    assert not warnings
    for session in state.sessions.values():
        assert session.segmentation_is_current and session.filtering_is_current
        assert len(session.segmentation_result["edit_history"]) == 2
    assert revisions(state.active_session) == previous
    np.testing.assert_array_equal(state.segmentation_result["edited_masks"], labels)


def test_z_routes_invalidate_only_consumers(project, tmp_path):
    window, app, _, _, warnings = project
    state = window.project_state
    window.segmentation.runner = lambda scientific, progress: _fake_result(scientific)
    nuclear = state.nuclear_session
    cyto = state.create_session("MAP2", scientific_role="cytoplasmic", channel_index=1)
    for session in (nuclear, cyto):
        state.select_session(session.session_id)
        run_and_measure(window, app)
    before = {s.session_id: revisions(s) for s in (nuclear, cyto)}
    options = dict(DEFAULTS, segmentation=True, measurement=False)
    assert window.z_correction.calculate({0: options}, 0, True)
    _wait(app, lambda: not window.z_correction.busy)
    assert all(revisions(s) == before[s.session_id] for s in (nuclear, cyto))
    assert window.z_correction.apply([0])
    assert not nuclear.segmentation_is_current
    assert cyto.segmentation_is_current and cyto.filtering_is_current
    assert revisions(cyto) == before[cyto.session_id]
    folder = tmp_path / "separate routes"
    save(window, app, folder)
    open_saved(window, app, folder)
    assert not warnings
    assert state.z_correction_applied == {0}
    assert not state.nuclear_session.segmentation_is_current
    assert state.segmentation_is_current and state.filtering_is_current
    assert revisions(state.active_session) == before[cyto.session_id]


def test_measurement_only_change_reuses_masks_review_and_checkpoint(project, tmp_path):
    window, app, _, image, warnings = project
    state = window.project_state
    window.segmentation.runner = lambda scientific, progress: _fake_result(scientific)
    run_and_measure(window, app)
    window.filtering.accept_roi()
    assert state.quantification_is_current and state.scientific_roi_reviewed
    saved_roi = state.scientific_roi.copy()
    original = state.segmentation_result
    raw_owner = state.session_revision()
    cyto = state.create_session("MAP2", scientific_role="cytoplasmic", channel_index=1)
    state.select_session(cyto.session_id)
    run_and_measure(window, app)
    state.select_session("nuclear")
    prior = {s.session_id: revisions(s) for s in state.sessions.values()}

    panel = window.context_panel.preprocessing_page.z_correction_panel
    panel.segmentation.setChecked(False)
    panel.measurement.setChecked(True)
    options = panel.options_for(0)
    assert window.z_correction.calculate({0: options}, 0, True)
    _wait(app, lambda: not window.z_correction.busy)
    assert window.z_correction.apply([0])
    for session in state.sessions.values():
        i, g, m = prior[session.session_id]
        assert revisions(session) == (i, g, m + 1)
        assert session.segmentation_is_current and not session.filtering_is_current
        assert not session.quantification_is_current
    assert "Measurements outdated" in window.filtering.working_status
    assert window.geometry_panel.buttons["start"].isEnabled()
    assert window.context_panel.mask_review_page.calculate_button.isEnabled()
    assert state.proofreading_ids == state.filtering_result["retained_ids"]
    assert GeometryEdit(state, filtered=True).filtered
    assert window.image_export._scientific_unavailable()
    window.filtering.apply_filters()
    assert not state.filtering_is_current
    assert state.quantification_result is not None and not state.quantification_is_current
    assert state.segmentation_result is original and state.session_revision() == raw_owner
    folder = tmp_path / "measurement routes"
    save(window, app, folder)
    open_saved(window, app, folder)
    assert not warnings and state.segmentation_is_current and not state.filtering_is_current
    assert "Measurements outdated" in window.filtering.working_status
    measure(window, app)
    assert state.scientific_roi_reviewed and state.quantification_is_current
    np.testing.assert_array_equal(state.scientific_roi, saved_roi)
    assert state.segmentation_result["input_provenance"]["z_corrections"] == {}
    measured = state.filtering_result
    assert measured["measurement_provenance"]["z_corrections"]["DAPI"] == options
    pixels, _ = correct_z(image[0], options)
    expected = pixels[state.segmentation_result["masks"] == 1].mean()
    assert measured["properties"].loc[1, "mean_intensity_DAPI"] == pytest.approx(expected)
    assert not window.image_export._scientific_unavailable()
    exported = window.image_export._scientific_namespace(tmp_path)
    assert exported["Z_CORRECTIONS"] == {"DAPI": options}
    save(window, app)
    open_saved(window, app, folder)
    assert not warnings and state.filtering_is_current and state.quantification_is_current
    assert state.sessions[cyto.session_id].segmentation_is_current
    assert not state.sessions[cyto.session_id].filtering_is_current

    from nuclear_segmentation import native_project
    from test_native_project import _read_snapshot

    snapshot = _read_snapshot(folder)
    snapshot["z_previews"].clear()
    incomplete = tmp_path / "incomplete applied correction"
    native_project.save_project(incomplete, snapshot)
    open_saved(window, app, incomplete)
    assert not warnings
    for session in state.sessions.values():
        assert session.segmentation_is_current and not session.filtering_is_current
        assert "checkpoint Z correction unavailable" in session.measurement_invalidation_reason


@pytest.mark.parametrize("grid", ["crop", "calibration"])
def test_shared_grid_invalidates_both_sessions(project, grid, caplog):
    window, app, _, _, _ = project
    state = window.project_state
    window.segmentation.runner = lambda scientific, progress: _fake_result(scientific)
    cyto = state.create_session("MAP2", scientific_role="cytoplasmic", channel_index=1)
    for session in (state.nuclear_session, cyto):
        state.select_session(session.session_id)
        run_and_measure(window, app)
    before = {s.session_id: revisions(s) for s in state.sessions.values()}
    caplog.set_level("INFO", logger="nuclear_segmentation.project_state")
    state.set_calibration(state.voxel_spacing_zyx_um)
    state.reset_z_corrections([1])
    assert all(revisions(s) == before[s.session_id] for s in state.sessions.values())
    if grid == "calibration":
        state.set_calibration((2., 1., 1.))
    else:
        state.apply_crop((1, 5, 2, 7), ((1, 2), (1, 7), (5, 7)), np.ones((4, 5), bool), "rectangle")
    for s in state.sessions.values():
        i, g, m = before[s.session_id]
        assert revisions(s) == (i + 1, g, m + 1)
        assert s.segmentation_result and not s.segmentation_is_current and not s.filtering_is_current
        assert grid in s.segmentation_invalidation_reason
        assert f"session={s.session_id} scope=segmentation input={i}->{i + 1}" in caplog.text


def test_measurement_worker_rejects_obsolete_intensity_route(project, monkeypatch):
    from threading import Event
    from nuclear_segmentation import filtering_workspace

    window, app, _, image, _ = project
    state = window.project_state
    window.segmentation.runner = lambda scientific, progress: _fake_result(scientific)
    run_and_measure(window, app)
    previous = state.filtering_result
    owner = state.session_revision()
    wait_for_change = Event()
    original_measure = filtering_workspace._measure

    def delayed(*args):
        assert wait_for_change.wait(5)
        return original_measure(*args)

    monkeypatch.setattr(filtering_workspace, "_measure", delayed)
    options = dict(DEFAULTS, segmentation=False, measurement=True)
    pixels, report = correct_z(image[1], options)
    report.update(_z_report(state, 1, options))
    state.set_z_correction_preview(1, options, report, pixels=pixels)
    assert window.filtering.calculate()
    try:
        state.apply_z_corrections([1])
    finally:
        wait_for_change.set()
    _wait(app, lambda: not window.filtering.busy)
    assert state.session_revision() == owner and state.segmentation_is_current
    assert state.filtering_result is previous and not state.filtering_is_current
    measure(window, app)
    assert state.filtering_result["measurement_provenance"]["z_corrections"] == {"MAP2": options}


def test_default_channel_change_preserves_explicit_sources(project):
    window, app, _, _, _ = project
    state = window.project_state
    window.segmentation.runner = lambda scientific, progress: _fake_result(scientific)
    nuclear = state.nuclear_session
    cyto = state.create_session("MAP2", scientific_role="cytoplasmic", channel_index=1)
    for session in (nuclear, cyto):
        state.select_session(session.session_id)
        run_and_measure(window, app)
    previous = revisions(cyto)
    state.update_channel(1, role="segmentation")
    assert not nuclear.segmentation_is_current
    assert revisions(cyto) == previous and cyto.filtering_is_current
    state.update_channel(0, name="DNA")
    assert cyto.segmentation_is_current and not cyto.filtering_is_current
    assert window.filtering.calculate()
    _wait(app, lambda: not window.filtering.busy)
    assert not state.filtering_is_current
    assert "Review incompatible filters" in window.filtering.working_status
    measure(window, app)
    assert "mean_intensity_DNA" in cyto.filtering_result["properties"]
    assert "mean_intensity_DAPI" not in cyto.filtering_result["properties"]
    assert cyto.segmentation_result["scientific_config"]["CHANNELS"][0]["name"] == "DAPI"


def test_measurement_dependency_uses_enabled_channels_and_marker_inputs(tmp_path):
    from test_scientific_input import _state, _config

    state, image = _state(tmp_path)
    session = state.nuclear_session
    config = _config(state)
    config["CHANNELS"][2]["measure"] = False
    state.set_segmentation_result({"masks": np.ones(image.shape[1:], dtype=np.uint16), "scientific_config": config})
    before = revisions(session)
    options = dict(DEFAULTS, segmentation=False, measurement=True)
    state.set_z_correction_preview(2, options, _z_report(state, 2, options))
    state.apply_z_corrections([2])
    assert revisions(session) == before
    state.set_perinuclear_markers([{"channel_index": 2, "enabled": True}])
    i, g, m = revisions(session)
    state.reset_z_corrections([2])
    assert revisions(session) == (i, g, m + 1)
    assert session.segmentation_is_current


def test_older_outdated_checkpoint_preserves_edited_geometry(project, tmp_path):
    from nuclear_segmentation import native_project
    from test_native_project import _read_snapshot, _segment

    window, app, _, _, warnings = project
    state = window.project_state
    _segment(window)
    edit = GeometryEdit(state)
    edit.preview[0, 1, 1] = 0
    edit.commit()
    masks = state.segmentation_result["edited_masks"].copy()
    folder = tmp_path / "current"
    save(window, app, folder)
    snapshot = _read_snapshot(folder)
    record = snapshot["sessions"]["nuclear"]
    record["segmentation_is_current"] = False
    del record["segmentation_invalidation_reason"], record["measurement_invalidation_reason"]
    older = tmp_path / "older"
    native_project.save_project(older, snapshot)
    open_saved(window, app, older)
    assert not warnings and not state.segmentation_is_current
    assert not state.filtering_is_current and window.image_export._scientific_unavailable()
    assert "keep the edit history" in window.filtering.working_status
    assert len(state.segmentation_result["edit_history"]) == 1
    np.testing.assert_array_equal(state.segmentation_result["edited_masks"], masks)


def test_z_report_alias_keeps_channel_identity(tmp_path):
    from nuclear_segmentation.scientific_input import validate_scientific_input
    from test_scientific_input import _state, _config

    state, _ = _state(tmp_path)
    options = dict(DEFAULTS, segmentation=False, measurement=True)
    report = dict(_z_report(state, 1, options), channel_index=1)
    state.set_z_correction_preview(1, options, report)
    state.apply_z_corrections([1])
    state.update_channel(1, name="Marker renamed")
    assert state.z_correction_reports[1]["channel_name_at_calculation"] == "Marker"
    assert validate_scientific_input(state, _config(state))["Z_CORRECTIONS"] == {"Marker renamed": options}
    state.z_correction_reports[1]["channel_index"] = 2
    with pytest.raises(ValueError, match="provenance"):
        validate_scientific_input(state, _config(state))


def test_roi_fixed_normalization_allows_other_measurement_channel(project):
    window, app, _, image, _ = project
    state = window.project_state
    panel = window.context_panel.preprocessing_page
    size = image.shape[-2:]
    state.set_normalization_roi(np.ones(size, dtype=bool), [(0, 0), (0, size[1]-1), (size[0]-1, 0)], {})
    panel.normalization_mode_combo.setCurrentIndex(panel.normalization_mode_combo.findData("roi_fixed"))
    assert window.normalization.calculate(panel.normalization_options())
    _wait(app, lambda: not window.normalization.busy)
    assert window.normalization.apply()

    z = panel.z_correction_panel
    z.inspect_combo.setCurrentIndex(z.inspect_combo.findData(1))
    z.segmentation.setChecked(False)
    z.measurement.setChecked(True)
    assert window.z_correction.calculate({1: z.options_for(1)}, 1, True)
    _wait(app, lambda: not window.z_correction.busy)
    owner = state.session_revision()
    assert window.z_correction.apply([1])
    assert state.normalization_is_current and state.session_revision() == owner
    assert window.normalization.apply()
    assert state.session_revision() == owner
