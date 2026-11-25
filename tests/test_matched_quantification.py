import csv
import json
import pytest

from nuclear_segmentation import matched_quantification as matched
from nuclear_segmentation.automatic_matching import MatchingSettings
from test_matching_explorer import inspection_pair, population, BIG
from test_matching_pair import choose_in_panel, qapp, window_pair
from test_integrated_matching import run_job
from test_native_project import _wait


def test_independent_metrics_with_large_ids_and_partial_review():
    result = population()
    outcomes = [
        (
            row,
            "Matched" if row.nuclear_id in (BIG, 7) else "Requires Review",
            45 if row.nuclear_id in (BIG, 7) else None,
        )
        for row in result.nuclei
    ]
    values = matched.metrics(result, iter(outcomes), 0.25)
    assert values["reviewed_matched_nuclei_per_mm3"] == 8
    assert values["reviewed_multiple_objects"] == 1
    assert values["eligible_nuclei"] == 6 and values["reviewed_matched_fraction"] == 2 / 6
    assert values["manual_counts"]["Requires Review"] == 4


def test_export_with_reviewed_sampling_geometry(window_pair, qapp, tmp_path):
    window, _ = window_pair
    state = window.project_state
    nuclear, cyto = inspection_pair(window)
    state.select_session(nuclear.session_id)
    state.segmentation_result["segmentation_channel"] = state.segmentation_channel
    controls = window.context_panel.mask_review_page
    controls.minimum_volume.editor.setValue(0)
    controls.minimum_sphericity.editor.setValue(0)
    controls.z_guard_mode.setCurrentText("none")
    _wait(qapp, lambda: not controls._filter_timer.isActive())
    window.filtering.accept_roi()
    assert state.quantification_is_current and state.quantification_result["density_status"] == "valid"
    panel = choose_in_panel(window, nuclear, cyto)
    panel.total_minimum.setText("0.25")
    result = run_job(state.matching_pair)
    assert len(result.nuclei) >= 2
    target = next(iter(result.associations))
    for row in result.nuclei[:2]:
        state.matching_pair.review.decide(result, row.nuclear_id, "Matched", target, comment="inspected")

    review = state.matching_pair.review
    review.decide(result, 11, "Uncertain", comment="Inspect small intersection")
    review.decide(result, result.nuclei[0].nuclear_id, "Uncertain")
    review.undo()
    state.association_review_changed.emit()
    window.set_workflow("quantification")
    summary = matched.summary(state)
    assert summary["eligible_nuclei"] == state.quantification_result["retained_nuclei_in_roi"]
    assert (
        summary["reviewed_matched_nuclei_per_mm3"]
        == 2 / state.quantification_result["roi_effective_volume_mm3"]
    )
    assert summary["reviewed_multiple_objects"] == 1
    assert "mm³" in window.context_panel.quantification_page.matched_status.text()
    panel = window.context_panel.export_page
    panel._check_scientific_options(False)
    panel.matched.setChecked(True)
    destination = tmp_path / "reviewed associations"
    assert window.image_export.export_scientific(destination), panel.scientific_status.text()
    _wait(qapp, lambda: not window.image_export.busy)
    assert (destination / "matched_quantification.json").exists(), panel.scientific_status.text()
    record = json.loads((destination / "matched_quantification.json").read_text())
    assert record["summary"] == summary
    assert record["provenance"]["settings"]["minimum_total_overlap_fraction"] == .25
    assert summary["automatic_no_match_reasons"] == {"ZERO_OVERLAP": 0, "BELOW_MINIMUM_OVERLAP": 1}
    with (destination / "automatic_associations.csv").open() as stream:
        automatic = {int(r["nuclear_id"]): r for r in csv.DictReader(stream)}
    assert automatic[11]["auto_class"] == "NO_MATCH"
    assert automatic[11]["classification_reason"] == "BELOW_MINIMUM_OVERLAP"
    assert float(automatic[11]["minimum_total_overlap_fraction"]) == .25
    assert automatic[11]["best_cytoplasmic_id"] == "99"
    with (destination / "association_intersections.csv").open() as stream:
        assert {"nuclear_id": "11", "cytoplasmic_id": "99", "intersection_voxels": "1"} in list(csv.DictReader(stream))
    history = record["provenance"]["review_history"]
    assert history[-1]["undone"] == history[-2]["event_index"]
    assert history[-2]["status"] == "Uncertain"
    decision = next(event for event in history if event["nuclear_id"] == 11)
    assert (decision["automatic_class"], decision["automatic_reason"]) == ("NO_MATCH", "BELOW_MINIMUM_OVERLAP")
    assert decision["status"] == "Uncertain"
    assert record["provenance"]["roi_sha256"] == state.quantification_result["scientific_roi_sha256"]
    with (destination / "final_associations.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(result.nuclei)
    assert all(r["automatic_reason"] == automatic[int(r["nuclear_id"])]["classification_reason"] for r in rows)
    assert next(r for r in rows if r["nuclear_id"] == "11")["review_status"] == "Uncertain"
    assert [r["final_target"] for r in rows[:2]] == [str(target)] * 2
    assert all(r["reviewed_multiple"] == "True" for r in rows[:2])
    assert (destination / "cytoplasmic_association_measurements.csv").exists()
    state.matching_pair.set_settings(MatchingSettings(0.89, 0.11))
    assert not window.image_export.export_scientific(tmp_path / "stale")
    assert not (tmp_path / "stale").exists()


def test_empty_eligible_population_preserves_density_units():
    from nuclear_segmentation.automatic_matching import OverlapStatistics, classify_population

    result = classify_population(
        OverlapStatistics((), 0.42, {}, {2: 17}, {}), MatchingSettings(), (1, 2)
    )
    totals = matched.metrics(result, [], 0.125)
    assert totals["eligible_nuclei"] == 0 and totals["retained_cytoplasmic_objects"] == 1
    assert totals["automatic_matched_fraction"] is None and totals["reviewed_matched_fraction"] is None
    assert (
        totals["reviewed_matched_nuclei_per_mm3"] == 0
        and totals["automatic_matched_nuclei_per_mm3"] == 0
    )
    with pytest.raises(ValueError):
        matched.metrics(result, [], 0)


def test_exact_scientific_ids_in_export_files(tmp_path):
    import pandas as pd
    from test_matching_explorer import CYTO

    result = population()
    tables = {
        "nuclear": pd.DataFrame({"volume_um3": [15.0]}, index=pd.Index([BIG], dtype="uint64")),
        "cytoplasmic": pd.DataFrame({"volume_um3": [45.0]}, index=pd.Index([CYTO], dtype="uint64")),
    }
    matched.write_package(tmp_path, (result, [], tables, {}, {}))
    with (tmp_path / "automatic_associations.csv").open() as stream:
        by_id = {row["nuclear_id"]: row for row in csv.DictReader(stream)}
    assert by_id[str(BIG)]["accepted_cytoplasmic_id"] == str(CYTO)
    assert by_id["15"]["accepted_cytoplasmic_id"] == ""
    with (tmp_path / "nuclear_association_measurements.csv").open() as stream:
        assert next(csv.DictReader(stream))["nuclear_id"] == str(BIG)
