"""Total nuclear overlap is a classification criterion on complete sparse evidence."""
from copy import deepcopy
from weakref import ref

import numpy as np
import pytest

from nuclear_segmentation import association_checkpoint as checkpoint
from nuclear_segmentation.automatic_matching import MatchingSettings, OverlapStatistics, classify_population
from nuclear_segmentation.candidate_explorer import MergeSelection
from nuclear_segmentation.guided_split import candidate_records, qualifying
from nuclear_segmentation.matching_explorer import AssociationIndex
from test_integrated_matching import install_labels, run_job
from test_matching_pair import inputs


@pytest.mark.parametrize("counts,expected,reason", [
    ([], "NO_MATCH", "ZERO_OVERLAP"),
    ([1], "NO_MATCH", "BELOW_MINIMUM_OVERLAP"),
    ([49], "NO_MATCH", "BELOW_MINIMUM_OVERLAP"),
    ([50], "AMBIGUOUS", "BEST_OR_SECOND_OUTSIDE_LIMITS"),
    ([51], "AMBIGUOUS", "BEST_OR_SECOND_OUTSIDE_LIMITS"),
    ([19, 17, 15], "AMBIGUOUS", "BEST_OR_SECOND_OUTSIDE_LIMITS"),
    ([500, 200], "MATCH", "OVERLAP_CRITERIA_MET"),
    ([500, 201], "AMBIGUOUS", "BEST_OR_SECOND_OUTSIDE_LIMITS"),
])
def test_total_overlap_uses_every_candidate_and_complete_nucleus(counts, expected, reason):
    cyto = dict(enumerate(counts, 1))
    pairs = {(7, label): n for label, n in cyto.items()}
    stats = OverlapStatistics((), .5, {7: 1000}, cyto, pairs)
    result = classify_population(stats, MatchingSettings(), (1, 2))
    row, = result.nuclei
    assert (row.auto_class, row.classification_reason) == (expected, reason)
    assert row.total_nuclear_fraction == sum(counts) / 1000
    assert row.nuclear_volume_um3 == 500 and row.cytoplasmic_candidate_count == len(counts)
    assert row.overlap_voxels == max(counts, default=0)
    index = AssociationIndex(result)
    assert sorted(index.by_nucleus[7]) == list(cyto)
    for label, n in cyto.items():
        candidate, = index.candidates(label)
        assert (candidate.voxels, candidate.nuclear_fraction, candidate.volume_um3) == (n, n / 1000, n * .5)
    assert sum(result.no_match_reason_counts.values()) == result.class_counts["NO_MATCH"]
    if expected == "NO_MATCH":
        assert result.no_match_reason_counts[reason] == 1


def test_inclusive_total_and_best_at_same_fraction():
    stats = OverlapStatistics((), 1., {3: 20}, {5: 1}, {(3, 5): 1})
    row, = classify_population(stats, MatchingSettings(.05, 0, .05), (1, 1)).nuclei
    assert row.auto_class == "MATCH"


def test_zero_threshold_matches_previous_decision_for_every_distribution():
    # Enumerate three-way intersections, ties and uncovered voxels for one small nucleus.
    for a in range(11):
        for b in range(11 - a):
            for c in range(11 - a - b):
                counts = [a, b, c]
                ranked = sorted(counts, reverse=True)
                expected = ("NO_MATCH" if not ranked[0] else
                            "MATCH" if ranked[0] / 10 >= .5 and ranked[1] / 10 <= .2 else "AMBIGUOUS")
                pairs = {(7, i): n for i, n in enumerate(counts, 1) if n}
                stats = OverlapStatistics((), 1., {7: 10}, {1: 10, 2: 10, 3: 10}, pairs)
                row, = classify_population(stats, MatchingSettings(.5, .2, 0), (1, 1)).nuclei
                assert row.auto_class == expected


@pytest.mark.parametrize("total", [-.01, .50001, 1.01, np.nan, np.inf, -np.inf])
def test_invalid_minimum_total(total):
    with pytest.raises(ValueError):
        MatchingSettings(minimum_total_overlap_fraction=total)


def test_threshold_only_rerun_keeps_candidates_seeds_and_review(inputs):
    state, nuclear, cyto, pair = inputs
    labels = np.zeros(state.working_dataset.standardized_shape[1:], dtype=np.int32)
    labels.flat[:100] = 7
    labels.flat[100:] = 8
    install_labels(state, nuclear, labels)
    other = np.zeros_like(labels)
    other.flat[:2] = [21, 22]
    install_labels(state, cyto, other)
    pair.set_settings(MatchingSettings(minimum_total_overlap_fraction=0))
    original = run_job(pair)
    pair.review.decide(original, 7, "Matched", 22, "visually confirmed")
    pair.set_settings(MatchingSettings())
    pending_record = checkpoint.snapshot(pair)
    assert pending_record["pending"][-1] == .05 and pending_record["automatic"]["settings"][-1] == 0
    current = run_job(pair)
    assert current.statistics is original.statistics
    assert current.no_match_reason_counts == {"ZERO_OVERLAP": 1, "BELOW_MINIMUM_OVERLAP": 1}
    assert current.class_counts == {"MATCH": 0, "AMBIGUOUS": 0, "NO_MATCH": 2}
    row = AssociationIndex(current).nuclei[7]
    assert row.accepted_cytoplasmic_id is None
    assert (row.best_cytoplasmic_id, row.second_cytoplasmic_id) == (21, 22)
    assert qualifying(candidate_records(current, 21), .01) == qualifying(candidate_records(original, 21), .01) == [7]
    selection = MergeSelection(ref(current), current.statistics.population_identity, 7, (21, 22))
    assert selection.is_current(pair)
    assert "2/100 voxels" in selection.summary(pair.review)
    assert pair.review.status(current, 7) == "Matched"
    event = pair.review.records[pair.review.key(7)]
    assert (event["cytoplasmic_id"], event["automatic_class"]) == (22, "AMBIGUOUS")
    assert event["settings"]["minimum_total_overlap_fraction"] == 0

    # Reconstruct the version 1 wire format, including evidence at decision time.
    pair.set_settings(original.settings)
    run_job(pair)
    record = deepcopy(checkpoint.snapshot(pair))
    record["version"] = 1
    record["pending"].pop()
    record["automatic"]["settings"].pop()
    for row in record["automatic"]["rows"]:
        del row["classification_reason"]
    for decision in record["history"]:
        del decision["settings"]["minimum_total_overlap_fraction"]
        del decision["automatic_reason"]
    validated = checkpoint.validate(state, record)
    restored = validated[3]
    assert validated[1].minimum_total_overlap_fraction == restored.settings.minimum_total_overlap_fraction == 0
    assert restored.nuclei == original.nuclei
    assert validated[4] == pair.review.events
    # Stale evidence also migrates, without inferring a new classification.
    record["automatic"]["dependencies"]["sources"][0]["geometry"] += 1
    _, _, historical, result, _, _ = checkpoint.validate(state, record)
    assert result is None and historical["settings"][-1] == 0
    assert historical["rows"][0]["classification_reason"] == original.nuclei[0].classification_reason


def test_checkpoints_without_matching_runs_distinguish_new_and_old_projects(inputs):
    state, _, _, pair = inputs
    assert checkpoint.validate(state, checkpoint.snapshot(pair))[1] == MatchingSettings()
    checkpoint.install(state, checkpoint.validate(state, None))
    assert pair.pending_settings.minimum_total_overlap_fraction == 0
    assert checkpoint.validate(state, checkpoint.snapshot(pair))[1] == pair.pending_settings
