"""Nuclear association measures on the established eligible nuclear population."""

from collections import Counter
from copy import deepcopy
import csv
from dataclasses import asdict, fields
from datetime import datetime, timezone
import json
from math import isfinite

from .association_review import STATES, reviewed_outcomes
from .automatic_matching import NuclearOverlap


def current_inputs(state):
    pair = state.matching_pair
    if state.active_session_id != pair.session_id("nuclear"):
        raise ValueError("Select the nuclear session used for Matching.")
    result = pair.current_result if pair.attempted else None
    if result is None:
        raise ValueError("Run current automatic matching for the measured pair.")
    q = state.quantification_result
    if (
        not state.quantification_is_current
        or not state.scientific_roi_reviewed
        or not q
        or q.get("density_status") != "valid"
        or q.get("session_id") != state.active_session_id
        or q.get("geometry_revision") != state.active_session.geometry_revision
        or set(q.get("retained_mask_ids", ())) != result.statistics.nuclear_voxels.keys()
        or q.get("retained_nuclei_in_roi") != len(result.nuclei)
        or q["filter_settings"] != state.filtering_result["settings"]
        or q.get("source_identity") != state.filtering_result["measurement_provenance"]
    ):
        raise ValueError(
            "Review the nuclear ROI and obtain current quantification for these retained nuclei."
        )
    volume = q["roi_effective_volume_mm3"]
    if not isfinite(volume) or volume <= 0:
        raise ValueError("A positive calibrated effective sampling volume is required.")
    return result, q


def metrics(result, outcomes, volume):
    if not isfinite(volume) or volume <= 0:
        raise ValueError("Invalid effective sampling volume.")
    n = len(result.nuclei)
    counts, reviewed_links = Counter(), Counter()
    for _, status, target in outcomes:
        counts[status] += 1
        if status == "Matched":
            reviewed_links[target] += 1
    automatic, reviewed = result.class_counts["MATCH"], counts["Matched"]
    return dict(
        eligible_nuclei=n,
        effective_sampling_mm3=volume,
        automatic_counts=dict(result.class_counts),
        manual_counts={s: counts[s] for s in (*STATES, "Requires Review")},
        automatic_matched_fraction=automatic / n if n else None,
        reviewed_matched_fraction=reviewed / n if n else None,
        automatic_matched_nuclei_per_mm3=automatic / volume,
        reviewed_matched_nuclei_per_mm3=reviewed / volume,
        retained_cytoplasmic_objects=len(result.associations),
        automatic_multiple_objects=result.multiple_objects,
        reviewed_multiple_objects=sum(count > 1 for count in reviewed_links.values()),
    )


def summary(state):
    result, q = current_inputs(state)
    return metrics(
        result, reviewed_outcomes(result, state.matching_pair.review), q["roi_effective_volume_mm3"]
    )


def export_snapshot(state):
    result, q = current_inputs(state)
    pair = state.matching_pair
    review = pair.review
    outcomes = list(reviewed_outcomes(result, review))
    counts = Counter(target for _, status, target in outcomes if status == "Matched")
    records = []
    for row, status, target in outcomes:
        event = review.records.get(review.key(row.nuclear_id))
        auto = row.accepted_cytoplasmic_id
        overlap = result.statistics.intersections.get((row.nuclear_id, target), 0)
        records.append(
            dict(
                nuclear_id=row.nuclear_id,
                automatic_class=row.auto_class,
                automatic_target=auto,
                automatic_multiple=bool(auto is not None and result.associations[auto].multiple),
                review_status=status,
                final_target=target,
                reviewed_multiple=bool(target and counts[target] > 1),
                final_intersection_voxels=overlap if target is not None else None,
                final_fraction_of_nucleus=overlap / result.statistics.nuclear_voxels[row.nuclear_id]
                if target is not None
                else None,
                comment=event["comment"] if event else None,
                decision_timestamp=event["timestamp"] if event else None,
                decision_origin="manual" if event else None,
            )
        )
    tables = {}
    for role, ids in (
        ("nuclear", result.statistics.nuclear_voxels),
        ("cytoplasmic", result.statistics.cytoplasmic_voxels),
    ):
        session = state.sessions[pair.session_id(role)]
        props = session.filtering_result["properties"]
        if not props.index.is_unique or not set(ids).issubset(props.index):
            raise ValueError(f"Retained {role} IDs are missing from the measurement table.")
        tables[role] = props.loc[[int(label) in ids for label in props.index]].copy()
    key = review.key(None)[:2]
    # Undo events refer to indices in the complete project history. Retain those
    # indices when exporting just this pair so that each undo stays traceable.
    history = [
        dict(event, event_index=index)
        for index, event in enumerate(review.events)
        if (event["nuclear_session"], event["cytoplasmic_session"]) == key
    ]
    provenance = dict(
        captured_utc=datetime.now(timezone.utc).isoformat(),
        validity="Current at export capture",
        dependencies=review.identity(result),
        acquisition_path=str(state.original_dataset.source_path),
        settings=asdict(result.settings),
        executed_measurement_revisions=result.measurement_revisions,
        current_measurement_revisions=pair.capture().measurement_revisions,
        source_sessions={role: pair.session_id(role) for role in ("nuclear", "cytoplasmic")},
        voxel_volume_um3=result.statistics.voxel_volume_um3,
        source_filter_settings={
            role: deepcopy(state.sessions[pair.session_id(role)].filtering_result["settings"])
            for role in ("nuclear", "cytoplasmic")
        },
        eligible_counting_model="Retained nuclear IDs published by nuclear Quantification.",
        counting_rule=q["counting_rule"],
        roi_sha256=q["scientific_roi_sha256"],
        effective_sampling_planes=list(map(bool, q["effective_sampling_planes"])),
        final_policy="Only current explicit Matched decisions supply final target IDs.",
        review_history=history,
        interpretation="Missing association does not establish absence of marker expression.",
    )
    return result, records, tables, provenance, metrics(result, outcomes, q["roi_effective_volume_mm3"])


def write_package(destination, snapshot):
    result, records, tables, provenance, totals = snapshot

    def table(name, columns, rows):
        with (destination / name).open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    table(
        "automatic_associations.csv",
        [f.name for f in fields(NuclearOverlap)] + ["accepted_cytoplasmic_id"],
        (
            dict(asdict(row), accepted_cytoplasmic_id=row.accepted_cytoplasmic_id)
            for row in result.nuclei
        ),
    )
    columns = (
        "nuclear_id",
        "automatic_class",
        "automatic_target",
        "automatic_multiple",
        "review_status",
        "final_target",
        "reviewed_multiple",
        "final_intersection_voxels",
        "final_fraction_of_nucleus",
        "comment",
        "decision_timestamp",
        "decision_origin",
    )
    table("final_associations.csv", columns, records)
    table(
        "association_intersections.csv",
        ("nuclear_id", "cytoplasmic_id", "intersection_voxels"),
        (
            dict(nuclear_id=n, cytoplasmic_id=c, intersection_voxels=v)
            for (n, c), v in result.statistics.intersections.items()
        ),
    )
    for role, props in tables.items():
        props.to_csv(destination / f"{role}_association_measurements.csv", index_label=f"{role}_id")
    with (destination / "matched_quantification.json").open("x", encoding="utf-8") as stream:
        json.dump(dict(summary=totals, provenance=provenance), stream, indent=2, allow_nan=False)
