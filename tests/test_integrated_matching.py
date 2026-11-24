"""Integrated scientific parity, result ownership and Qt worker regressions."""
from dataclasses import replace
from threading import Event

import numpy as np
import pytest

from nuclear_segmentation import matching_execution
from nuclear_segmentation.automatic_matching import (
    COLORS, MatchingSettings, classify_population, scan_overlaps,
)
from nuclear_segmentation.geometry_edit import GeometryEdit
from test_automatic_matching import auto_model
from test_matching_pair import (
    _wait, choose_in_panel, filter_labels, inputs, measured_pair, publish, qapp, window_pair,
)


def run_job(context):
    job = context.begin()
    stats = job.cache or scan_overlaps(job.snapshot, lambda percent: None)
    result = classify_population(stats, job.settings, job.snapshot.measurement_revisions)
    assert context.publish(job, result)
    return result


def install_labels(state, session, labels):
    # Publish through the same segmentation/filtering gateways as the desktop.
    publish(state, session)
    record = dict(state.segmentation_result, masks=labels, mask_shape=labels.shape)
    state.set_segmentation_result(record)
    filter_labels(state, tuple(int(i) for i in np.unique(labels) if i))


def test_legacy_equivalence_all_fields_and_multiple_links(inputs, auto_model):
    state, nuclear, cyto, context = inputs
    shape = state.working_dataset.standardized_shape[1:]
    for session, source in ((nuclear, auto_model.dapi), (cyto, auto_model.map2)):
        labels = np.zeros(shape, source.dtype)
        labels.flat[:source.size] = source.ravel()
        install_labels(state, session, labels)
    result = run_job(context)
    stats = result.statistics
    factor = stats.voxel_volume_um3 / np.prod(auto_model.spacing)
    expected = {r['dapi_id']: r for r in auto_model.run_automatic()}
    for row in result.nuclei:
        old = expected[row.nuclear_id]
        assert row.auto_class == old["auto_class"]
        assert row.best_cytoplasmic_id == old["best_map2_id"]
        assert row.second_cytoplasmic_id == old["second_map2_id"]
        assert row.overlap_voxels == old["overlap_voxels"]
        assert row.cytoplasmic_candidate_count == old["overlapping_map2_count"]
        assert row.nuclear_volume_um3 == pytest.approx(old["dapi_volume_um3"] * factor)
        assert row.overlap_volume_um3 == pytest.approx(old["overlap_volume_um3"] * factor)
        if old["best_map2_id"] is None:
            assert row.best_cytoplasmic_volume_um3 is None
        else:
            assert row.best_cytoplasmic_volume_um3 == pytest.approx(old["best_map2_volume_um3"] * factor)
        assert (row.best_nuclear_fraction, row.second_nuclear_fraction, row.total_nuclear_fraction,
                row.iou, row.dice) == pytest.approx((old["best_dapi_fraction"], old["second_dapi_fraction"],
                                                     old["total_dapi_fraction"], old["iou"], old["dice"]))
    links = [(r.nuclear_id, r.accepted_cytoplasmic_id) for r in result.nuclei if r.accepted_cytoplasmic_id]
    assert links == [(2, 10), (1000000, 40)]
    assert stats.intersections == {(2, 10): 5, (2, 20): 2, (7, 10): 5, (7, 20): 3,
                                   (99, 30): 4, (1000000, 40): 10}
    assert result.class_counts == {"MATCH": 2, "AMBIGUOUS": 2, "NO_MATCH": 1}
    assert result.multiple_objects == result.nuclei_in_multiple == 0
    context.set_settings(MatchingSettings(.50, .30))
    assert context.current_result is None
    relaxed = run_job(context)
    assert relaxed.statistics is stats
    assert relaxed.associations[10].matched_nuclei == 2 and relaxed.associations[10].multiple
    assert relaxed.associations[20].matched_nuclei == 0
    assert relaxed.multiple_objects == 1 and relaxed.nuclei_in_multiple == 2
    assert relaxed.class_counts == {"MATCH": 3, "AMBIGUOUS": 1, "NO_MATCH": 1}


def test_exact_mixed_integer_ids_and_permitted_tie(inputs):
    state, nuclear, cyto, context = inputs
    shape = state.working_dataset.standardized_shape[1:]
    a, b = np.zeros(shape, np.int64), np.zeros(shape, np.uint64)
    big = 2**63 + 35
    a.flat[:4] = 2**55 + 1
    b.flat[:4] = np.array([big + 1, big, big + 1, big], dtype=b.dtype)
    b.flat[4] = big
    install_labels(state, nuclear, a)
    install_labels(state, cyto, b)
    context.set_settings(MatchingSettings(.5, .5))
    result = run_job(context)
    row, = result.nuclei
    assert row.nuclear_id == 2**55 + 1
    assert (row.best_cytoplasmic_id, row.second_cytoplasmic_id) == (big, big + 1)
    assert row.auto_class == "MATCH" and row.cytoplasmic_candidate_count == 2
    assert row.total_nuclear_fraction == 1
    assert (row.iou, row.dice) == pytest.approx((2/5, 4/7))


@pytest.mark.parametrize("minimum,maximum", [(0, .2), (-.1, .2), (1.1, 1), (.5, -.1),
                                             (.5, 1.01), (np.nan, 0), (.4, np.inf), (-np.inf, 0)])
def test_invalid_settings(minimum, maximum):
    with pytest.raises(ValueError):
        MatchingSettings(minimum, maximum)


@pytest.mark.parametrize("role", ["nuclear", "cytoplasmic"])
def test_current_cache_lifecycle_and_corrected_geometry(inputs, role):
    state, nuclear, cyto, context = inputs
    for session in (cyto, nuclear):
        publish(state, session)
    result = run_job(context)
    assert set(result.statistics.nuclear_voxels) == set(result.associations) == {7}
    assert result.nuclei[0].iou == 1
    other = state.create_session("Other")
    publish(state, other)
    state.invalidate_segmentation(active_only=True)
    state.rename_session(nuclear.session_id, "Nuclei")
    assert context.current_result is result
    source = nuclear if role == "nuclear" else cyto
    state.select_session(source.session_id)
    state.invalidate_filtering()
    assert context.current_result is None
    filter_labels(state, (7,))
    assert context.current_result is result
    # Even a round trip through a different population permanently evicts its cache.
    filter_labels(state, (500,))
    filter_labels(state, (7,))
    assert context.current_result is None
    fresh = run_job(context)
    assert fresh.statistics is not result.statistics
    edit = GeometryEdit(state, filtered=True)
    edit.preview[0, 1, 1] = 0
    assert context.current_result is fresh
    edit.commit()
    assert context.current_result is None
    filter_labels(state, (7,))
    edited = run_job(context)
    assert edited.statistics is not fresh.statistics
    assert edited.nuclei[0].overlap_voxels == fresh.nuclei[0].overlap_voxels - 1
    assert edited.nuclei[0].iou < 1


@pytest.mark.parametrize("change", ["settings", "pair", "measurement", "geometry", "project_close", "replacement"])
def test_running_worker_rejects_obsolete_completion(window_pair, qapp, monkeypatch, change):
    window, dataset = window_pair
    state = window.project_state
    nuclear, cyto = measured_pair(window)
    panel = choose_in_panel(window, nuclear, cyto)
    context = state.matching_pair
    started, release = Event(), Event()

    def delayed(pair, progress):
        started.set()
        assert release.wait(15)
        return scan_overlaps(pair, progress)

    monkeypatch.setattr(matching_execution, "scan_overlaps", delayed)
    panel.run.click()
    try:
        _wait(qapp, started.is_set)
        assert not panel.run.isEnabled() and "Running" in panel.result_status.text()
        if change == "settings":
            panel.minimum.setText("0.9")
            panel.defaults.click()
        elif change == "pair":
            context.select("nuclear", None)
            context.select("nuclear", nuclear.session_id)
        elif change == "measurement":
            state.select_session(cyto.session_id)
            measured = state.filtering_result
            state.invalidate_filtering()
            state.set_filtering_result(measured)
        elif change == "geometry":
            publish(state, cyto, x=3, window=window)
        elif change == "project_close":
            assert not window.close()
            assert window.close_project(confirm=False)
        else:
            assert window._apply_imported_dataset(replace(dataset, time_index=1))
    finally:
        release.set()
        _wait(qapp, lambda: not window.matching_display.execution.busy)
    assert context.current_result is None and context.job is None
    assert state.matching_pair.current_result is None and state.matching_pair.job is None
    assert panel.table_model.rowCount() == 0


def test_async_reclassification_failure_and_read_only_colors(window_pair, qapp, monkeypatch):
    window, _ = window_pair
    nuclear, cyto = measured_pair(window)
    panel = choose_in_panel(window, nuclear, cyto)
    display = window.matching_display
    context = window.project_state.matching_pair

    def run():
        panel.run.click()
        _wait(qapp, lambda: not display.execution.busy)

    def fail(*args):
        raise ValueError("test scan error")

    with monkeypatch.context() as patch:
        patch.setattr(matching_execution, "scan_overlaps", fail)
        run()
    assert "Calculation failed" in panel.result_status.text() and "test scan error" in panel.result_status.text()
    assert context.current_result is None and panel.run.isEnabled()
    run()
    assert panel.table_model.rowCount() == 1 and "MATCH · 1 (100.0%)" in panel.summary.text()
    panel.class_colors.click()
    layer = display.layers["nuclear"]
    assert np.shares_memory(layer.data, nuclear.filtering_result["filtered_masks"]) and not layer.editable
    np.testing.assert_allclose(layer.colormap.color_dict[7], COLORS["MATCH"])
    assert not window.project_state.owns_mask_layer(layer)
    original_stats = context.current_result.statistics
    panel.minimum.setText("nan")
    assert not panel.run.isEnabled() and "finite fractions" in panel.result_status.text()
    panel.minimum.setText("1e-25")
    assert panel.run.isEnabled() and context.pending_settings.match_min == 1e-25
    panel.minimum.setText("0.75")
    assert panel.table_model.rowCount() == 0 and "Outdated" in panel.result_status.text()
    assert "Best ≥ 0.5," in panel.summary.text()
    assert not np.allclose(layer.colormap.map(7), COLORS["MATCH"])
    monkeypatch.setattr(matching_execution, "scan_overlaps", fail)
    run()
    assert context.current_result.statistics is original_stats
    assert display.layers["nuclear"] is layer
    np.testing.assert_allclose(layer.colormap.color_dict[7], COLORS["AMBIGUOUS"])
    panel.defaults.click()
    run()
    assert context.current_result.statistics is original_stats
    window.set_workflow("proofreading")
    assert not display.layers
    window.set_workflow("matching")
    np.testing.assert_allclose(display.layers["nuclear"].colormap.color_dict[7], COLORS["MATCH"])


@pytest.mark.parametrize("grid_change", ["crop", "calibration"])
def test_grid_changes_release_result_and_cached_statistics(inputs, grid_change):
    state, nuclear, cyto, context = inputs
    for session in (nuclear, cyto):
        publish(state, session)
    original = run_job(context)
    if grid_change == "crop":
        state.apply_crop((0, 4, 0, 5), [(0, 0), (4, 5)])
    else:
        state.set_calibration((3.2, .4, .6))
    assert context.current_result is None
    for session in (nuclear, cyto):
        publish(state, session)
    job = context.begin()
    assert job.cache is None and job.snapshot.population_identity != original.statistics.population_identity


def test_publication_requires_exact_job_even_for_identical_inputs(inputs):
    state, nuclear, cyto, context = inputs
    for session in (nuclear, cyto):
        publish(state, session)
    obsolete = context.begin()
    stats = scan_overlaps(obsolete.snapshot, lambda percent: None)
    result = classify_population(stats, obsolete.settings, obsolete.snapshot.measurement_revisions)
    context.set_settings(MatchingSettings(.51, .2))
    context.set_settings(obsolete.settings)
    new = context.begin()
    assert new.snapshot == obsolete.snapshot and new.settings == obsolete.settings
    assert not context.publish(obsolete, result)
    context.fail(obsolete, "obsolete failure")
    assert context.error is None and context.job is new
    state.rename_session(cyto.session_id, "Renamed")
    publish(state, state.create_session("Unrelated"))
    assert context.publish(new, result)
