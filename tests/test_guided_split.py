"""Phase 2D.5.4 scientific, transaction and embedded viewer contracts."""
import json
from copy import deepcopy
from zipfile import ZipFile

import numpy as np
import pytest
from qtpy.QtCore import QSettings
from qtpy.QtTest import QTest
from scipy.ndimage import distance_transform_edt
from skimage.segmentation import watershed

from nuclear_segmentation import native_project
from nuclear_segmentation.automatic_matching import MatchingSettings
from nuclear_segmentation.geometry_edit import GeometryEdit, replay_sequence, validate_history
from nuclear_segmentation.guided_split import OPERATION, candidate_records, propose, qualifying
from nuclear_segmentation.desktop_window import DesktopWindow
from nuclear_segmentation.watershed import partition
from test_desktop_normalization import _dataset
from test_integrated_matching import run_job
from test_map2_split import split_model
from test_matching_explorer import inspection_pair, population
from test_native_project import open_saved, project, save
from test_matching_pair import _wait, choose_in_panel, qapp, window_pair


def test_all_positive_intersections_use_complete_object_denominator():
    result = population()
    parent = next(iter(result.statistics.cytoplasmic_voxels))
    candidates = candidate_records(result, parent)
    assert len(candidates) == 4
    assert qualifying(candidates, .2) == sorted(result.statistics.nuclear_voxels.keys() - {15, 17})
    assert 11 in qualifying(candidates, .3) and 13 not in qualifying(candidates, .3)
    for bad in (0, -1, 1.01, np.inf, np.nan):
        with pytest.raises(ValueError):
            qualifying(candidates, bad)


def test_equivalence_to_legacy_and_large_scientific_ids(split_model):
    model = split_model
    parent = 65535
    legacy = model.preview_split(parent)
    candidates = [dict(id=r["dapi_id"], classification="MATCH", intersection=r["overlap_voxels"],
                       fraction=r["fraction_dapi_inside_map2"], total=int((model.dapi == r["dapi_id"]).sum()))
                  for r in legacy["overlaps"]]
    args = model.map2, model.dapi, parent, candidates, .5, model.spacing
    count = int((model.map2 == parent).sum())
    proposal = propose(*args, 65536, np.uint32, count)
    np.testing.assert_array_equal(proposal.labels, legacy["data"][proposal.box])
    wide = propose(*args, 65536, np.uint32, count, bounds=((0, 0, 0), model.map2.shape))
    assert wide.box == proposal.box
    np.testing.assert_array_equal(wide.labels, proposal.labels)
    # Independent pre-extraction method with actual child IDs as watershed markers.
    target = model.map2[proposal.box] == parent
    markers = np.zeros(target.shape, np.int32)
    for seed, child in legacy["children"].items():
        markers[target & (model.dapi[proposal.box] == seed)] = child
    expected = watershed(-distance_transform_edt(target, sampling=model.spacing), markers, mask=target)
    np.testing.assert_array_equal(proposal.labels, expected)
    first = 2**63 + 17
    large = propose(*args, first, np.uint64, count)
    assert set(map(int, np.unique(large.labels[target]))) == {first, first + 1}
    with pytest.raises(ValueError, match="Insufficient"):
        propose(*args, 2**64 - 1, np.uint64, count)
    with pytest.raises(ValueError, match="two seeds"):
        propose(model.map2, model.dapi, parent, candidates[:1], .5, model.spacing, first, np.uint64, count)


def open_context(window):
    state = window.project_state
    nuclear, cyto = inspection_pair(window)
    state.select_session(nuclear.session_id)
    explorer = choose_in_panel(window, nuclear, cyto).explorer
    run_job(state.matching_pair)
    selection = explorer.selection
    explorer.review.click()
    geometry = window.geometry_panel
    guided = geometry.guided
    assert guided.selection is selection, explorer.status.text()
    assert window.navigation.current_workflow == "proofreading" and not geometry.busy
    assert state.active_session_id == cyto.session_id
    assert window.mask_selection.selected_mask_id == selection.cytoplasmic_id
    return nuclear, cyto, geometry, guided


def generate(guided, app):
    guided.preview.click()
    _wait(app, lambda: guided.worker is None)
    assert guided.geometry.busy and guided.reference is not None, guided.geometry.status.text()


def test_readonly_preview_apply_commit_history_and_recalculation(window_pair, qapp, tmp_path):
    window, _ = window_pair
    nuclear, cyto, geometry, guided = open_context(window)
    state = window.project_state
    original = cyto.segmentation_result
    nuclear_seg = nuclear.segmentation_result
    measurements = nuclear.filtering_result
    result = state.matching_pair.current_result
    settings = result.settings
    guided.threshold.setText(".2")
    assert result.settings == settings and guided.preview.isEnabled()
    generate(guided, qapp)
    edit = geometry.edit
    p = edit.parameters
    assert p["seed_ids"] == [2, 7, 11, 13] and p["residual_voxels"] == 0
    assert not geometry.layer.editable and not guided.reference.editable
    parent_support = original["masks"] == 99
    assert geometry.buttons["apply_operation"].text() == "Apply Watershed"
    assert geometry.buttons["cancel_operation"].text() == "Cancel Preview"
    assert all(geometry.buttons[name].isHidden() for name in ("undo_preview", "redo_preview"))
    assert not geometry.buttons["commit"].isEnabled()
    snapshot = edit.preview.copy()
    edit.preview[parent_support] = 0
    with pytest.raises(ValueError, match="modified"):
        edit.apply_operation()
    geometry.cancel_operation()
    np.testing.assert_array_equal(edit.preview, original["masks"])
    geometry.discard()
    assert cyto.segmentation_result is original
    generate(guided, qapp)
    geometry.apply_operation()
    assert geometry.buttons["undo_preview"].isEnabled() and "Applied watershed" in geometry.status.text()
    steps = geometry.edit.steps
    with pytest.raises(ValueError, match="dedicated"):
        replay_sequence(geometry.edit.base, steps + steps, geometry.edit.retained_ids, geometry.edit.preview.dtype)
    with pytest.raises(ValueError):
        geometry.edit.merge(steps[0]["parameters"]["child_ids"], steps[0]["parameters"]["child_ids"][0])
    assert not geometry.layer.editable
    geometry.undo_preview()
    np.testing.assert_array_equal(geometry.edit.preview, original["masks"])
    assert geometry.buttons["redo_preview"].isEnabled() and not geometry.buttons["commit"].isEnabled()
    geometry.redo_preview()
    np.testing.assert_array_equal(geometry.edit.preview, snapshot)
    geometry.commit()
    assert geometry.buttons["undo"].isEnabled() and "Committed geometry" in geometry.status.text()
    committed = cyto.segmentation_result
    record = committed["edit_history"][-1]
    assert record["operation"] == OPERATION and record["removed_ids"] == [99]
    assert record["new_ids"] == record["parameters"]["child_ids"]
    assert state.matching_pair.current_result is None and state.matching_pair._result is None
    assert nuclear.segmentation_result is nuclear_seg and nuclear.filtering_result is measurements
    assert committed["masks"] is original["masks"]
    np.testing.assert_array_equal(committed["edited_masks"][~parent_support], original["masks"][~parent_support])
    with ZipFile(tmp_path / "checkpoint.ncs", "w") as archive:
        archive.writestr("geometry.json", json.dumps(native_project._pack(archive, committed), allow_nan=False))
    with ZipFile(tmp_path / "checkpoint.ncs") as archive:
        restored = native_project._unpack(archive, json.loads(archive.read("geometry.json")))
    validate_history(restored, cyto)
    tampered = deepcopy(restored)
    tampered["edit_history"][0]["parameters"]["child_voxels"][0] += 1
    with pytest.raises(ValueError, match="accounting"):
        validate_history(tampered, cyto)
    geometry.recalculate()
    _wait(qapp, lambda: not window.filtering.busy)
    assert state.matching_pair.capture() is not None
    run_job(state.matching_pair)
    assert state.matching_pair.current_result is not result
    GeometryEdit.undo(state)
    np.testing.assert_array_equal(cyto.segmentation_result["edited_masks"], original["masks"])
    assert GeometryEdit(state).next_id > max(record["new_ids"])


def test_reject_stale_completion_apply_and_commit(window_pair, qapp):
    window, _ = window_pair
    nuclear, cyto, geometry, guided = open_context(window)
    pair = window.project_state.matching_pair
    old = pair.pending_settings
    guided.preview.click()
    pair.set_settings(MatchingSettings(.97, .01))
    _wait(qapp, lambda: guided.worker is None)
    assert not geometry.busy
    pair.set_settings(old)
    window.set_workflow("matching")
    window.context_panel.matching_page.explorer.review.click()
    generate(guided, qapp)
    p = geometry.edit.parameters
    assert p["residual_components"] > 0 and p["residual_volume_um3"] > 0
    pair.set_settings(MatchingSettings(.97, .01))
    with pytest.raises(ValueError, match="inputs changed"):
        geometry.apply_operation()
    pair.set_settings(old)
    geometry.apply_operation()
    pair.set_settings(MatchingSettings(.97, .01))
    with pytest.raises(ValueError, match="inputs changed"):
        geometry.commit()
    geometry.discard()


def test_guided_real_canvas(qapp, tmp_path):
    if qapp.platformName() in {"offscreen", "minimal"}:
        pytest.skip("Requires the Qt OpenGL canvas")
    window = DesktopWindow(settings=QSettings(str(tmp_path / "guided.ini"), QSettings.Format.IniFormat),
                           restore_workspace=False)
    try:
        window.show()
        assert window._apply_imported_dataset(_dataset(tmp_path / "spatial.tif"))
        _, _, geometry, guided = open_context(window)
        generate(guided, qapp)
        p = geometry.edit.guided_proposal
        viewer = geometry.viewer
        for dimensions in (3, 2):
            window.viewer_toolbar.set_ndisplay(dimensions)
            assert not geometry.layer.editable and not guided.reference.editable
            np.testing.assert_allclose(guided.reference.data_to_world((0, 0, 0)),
                                       geometry.layer.data_to_world(tuple(s.start for s in p.box)))
            QTest.qWait(120)
            canvas = window.embedding.qt_viewer.canvas
            with_seeds = canvas.screenshot()
            guided.show_seeds.setChecked(False)
            QTest.qWait(120)
            assert not with_seeds.isNull() and with_seeds != canvas.screenshot()
            guided.show_seeds.setChecked(True)
    finally:
        if window.geometry_panel.busy:
            window.geometry_panel.discard()
        window.close()


def test_guided_native_project_round_trip(project, tmp_path):
    window, app, _, _, warnings = project
    nuclear, cyto, geometry, guided = open_context(window)
    generate(guided, app)
    geometry.apply_operation()
    geometry.commit()
    saved = cyto.segmentation_result["edited_masks"].copy()
    # Later reference edits must not change the validity of historical seed evidence.
    state = window.project_state
    state.select_session(nuclear.session_id)
    change = GeometryEdit(state)
    change.remove(7)
    change.commit()
    folder = tmp_path / "Guided project"
    save(window, app, folder)
    window.close_project(confirm=False)
    open_saved(window, app, folder)
    assert not warnings
    state.select_session(cyto.session_id)
    np.testing.assert_array_equal(state.segmentation_result["edited_masks"], saved)
    GeometryEdit.undo(state)
    np.testing.assert_array_equal(state.segmentation_result["edited_masks"], state.segmentation_result["masks"])


def test_spacing_changes_the_watershed_partition():
    z, y, x = np.indices((7, 11, 15))
    support = ((z - 3) / 3)**2 + ((y - 5) / 5)**2 + ((x - 7) / 7)**2 <= 1
    markers = np.zeros(support.shape, np.int32)
    markers[2, 5, 5], markers[3, 5, 10] = 1, 2
    spacing = (3, .7, .4)
    labels, residual = partition(support, markers, spacing)
    expected = watershed(-distance_transform_edt(support, sampling=spacing), markers, mask=support)
    np.testing.assert_array_equal(labels, expected)
    assert residual == 0 and np.any(labels != partition(support, markers, (1, 1, 1))[0])


def test_handoff_rejects_invalid_result_and_conflicting_edit(window_pair):
    window, _ = window_pair
    state = window.project_state
    nuclear, cyto = inspection_pair(window)
    state.select_session(nuclear.session_id)
    explorer = choose_in_panel(window, nuclear, cyto).explorer
    run_job(state.matching_pair)
    geometry = window.geometry_panel
    window.set_workflow("proofreading")
    geometry.start()
    geometry.guided.handoff()
    assert state.active_session_id == nuclear.session_id and geometry.guided.selection is None
    geometry.discard()
    state.matching_pair.set_settings(MatchingSettings(.97, .01))
    geometry.guided.handoff()
    assert state.active_session_id == nuclear.session_id and geometry.guided.selection is None
    assert "Rerun Matching" in explorer.status.text()


@pytest.mark.parametrize("dtype,excluded", [(np.uint32, 2**32 - 1), (np.uint64, 2**63 + 7)])
def test_guided_allocation_above_excluded_ids(window_pair, qapp, dtype, excluded):
    window, _ = window_pair
    state = window.project_state
    nuclear, cyto = inspection_pair(window)
    measured = dict(cyto.filtering_result)
    source = state.segmentation_result
    raw = source["masks"].astype(dtype)
    retained = raw.copy()
    raw[0, 0, 0] = excluded
    state.set_segmentation_result(dict(source, masks=raw, maximum_label_id=excluded, object_count=3))
    measured.update(session_id=cyto.session_id, geometry_revision=cyto.geometry_revision,
                    filtered_masks=retained, retained_ids=(23, 99), retained_count=2)
    state.set_filtering_result(measured)
    panel = choose_in_panel(window, nuclear, cyto)
    run_job(state.matching_pair)
    panel.explorer.review.click()
    geometry = window.geometry_panel
    assert geometry.guided.selection is not None, panel.explorer.status.text()
    generate(geometry.guided, qapp)
    assert geometry.edit.preview.dtype == np.uint64
    assert geometry.edit.parameters["child_ids"] == [excluded + 1, excluded + 2]
    geometry.apply_operation()
    geometry.commit()
    final = cyto.segmentation_result
    assert int(final["edited_masks"][0, 0, 0]) == excluded
    assert final["edit_history"][-1]["new_ids"] == [excluded + 1, excluded + 2]
    validate_history(final, cyto)
