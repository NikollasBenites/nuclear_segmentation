"""Sparse native checkpoint records for automatic matching and manual decisions."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime

from .association_review import AssociationReview, STATES, dependencies
from .geometry_edit import effective_masks
from .manual_review import mask_signature
from .automatic_matching import LEGACY_REASONS, NO_MATCH_REASONS, MatchingSettings, classify_population, scan_overlaps


def thresholds(settings):
    return None if settings is None else [settings.match_min, settings.second_max,
                                          settings.minimum_total_overlap_fraction]


def scientific_record(result):
    stats = result.statistics
    return dict(dependencies=dependencies(result), measurements=result.measurement_revisions,
                settings=thresholds(result.settings), voxel_um3=stats.voxel_volume_um3,
                nuclear=dict(stats.nuclear_voxels), cytoplasmic=dict(stats.cytoplasmic_voxels),
                intersections=[[n,c,count] for (n,c),count in sorted(stats.intersections.items())],
                rows=[asdict(row) for row in result.nuclei],
                accepted_counts={label:a.matched_nuclei for label,a in result.associations.items()})


def snapshot(pair):
    # Before the first run, persist the thresholds and keep exploratory session selections transient.
    sessions = [None, None]
    if pair.attempted or pair.review.events:
        sessions = [pair.session_id(role) for role in ("nuclear", "cytoplasmic")]
    return dict(version=2, sessions=sessions,
                pending=thresholds(pair.pending_settings), interrupted=pair.job is not None or pair.error is not None,
                history=list(pair.review.events),
                automatic=scientific_record(pair._result) if pair._result else pair.historical_result)


def prepare(record, sessions, source, digest):
    """Bind review evidence to verified source bytes and the geometry written by the I/O worker."""
    if record is None:
        return None
    record = deepcopy(record)
    evidence = [e["dependencies"] for e in record["history"] if e["action"] == "decision"]
    if record["automatic"]:
        evidence.append(record["automatic"]["dependencies"])
    owners = set()
    for item in evidence:
        owners.update(s["session"] for s in item["sources"])
        grid = item["grid"]
        if grid.get("source_path") == source and grid.get("source_sha256") is None:
            grid.pop("source_path")
            grid["source_sha256"] = digest
    record["geometry_signatures"] = {
        key: mask_signature(effective_masks(sessions[key]["segmentation_result"]))
        for key in owners if sessions[key]["segmentation_result"] is not None}
    return record


def verify_geometry(record, state):
    if record is None:
        return
    evidence = [event["dependencies"] for event in record["history"] if event["action"] == "decision"]
    if record["automatic"]:
        evidence.append(record["automatic"]["dependencies"])
    owners = {s["session"] for item in evidence for s in item["sources"]}
    signatures = {key: mask_signature(effective_masks(state.sessions[key].segmentation_result))
                  for key in owners if state.sessions[key].segmentation_result is not None}
    if record.get("geometry_signatures") != signatures:
        raise ValueError("Association checkpoint geometry signatures disagree with the saved sessions.")


def validate_history(review, history, state):
    for event in history:
        if (not isinstance(event, dict) or event.get("action") not in ("decision","undo")
                or type(event.get("nuclear_id")) is not int or event["nuclear_id"] <= 0
                or datetime.fromisoformat(event["timestamp"]).tzinfo is None):
            raise ValueError("Invalid manual review history.")
        for role in ("nuclear","cytoplasmic"):
            owner = state.sessions.get(event[role+"_session"])
            if owner is None or owner.scientific_role != role:
                raise ValueError("Unknown review source session.")
        if event["action"] == "decision":
            status, target = event["status"], event["cytoplasmic_id"]
            sources = event["dependencies"]["sources"]
            settings = event["settings"]
            if settings != asdict(MatchingSettings(settings["match_min"], settings["second_max"],
                                                  settings["minimum_total_overlap_fraction"])):
                raise ValueError("Invalid historical automatic settings.")
            automatic_class = event["automatic_class"]
            reasons = (NO_MATCH_REASONS if automatic_class == "NO_MATCH" and settings["minimum_total_overlap_fraction"] > 0
                       else (LEGACY_REASONS[automatic_class],))
            if event["automatic_reason"] not in reasons:
                raise ValueError("Invalid historical automatic classification reason.")
            automatic_target = event["automatic_target"]
            if ((event["automatic_class"] == "MATCH" and
                 (type(automatic_target) is not int or automatic_target <= 0))
                    or (event["automatic_class"] != "MATCH" and automatic_target is not None)):
                raise ValueError("Invalid historical automatic target.")
            if (event["origin"] != "manual" or status not in STATES or not isinstance(event["comment"], str)
                    or event["automatic_class"] not in ("MATCH","AMBIGUOUS","NO_MATCH")
                    or [s["session"] for s in sources] != [event["nuclear_session"],event["cytoplasmic_session"]]
                    or any(type(s["geometry"]) is not int or s["geometry"] < 0 or len(s["population"]) != 64 for s in sources)
                    or (status == "Matched" and (type(target) is not int or target <= 0))
                    or (status != "Matched" and target is not None)):
                raise ValueError("Invalid manual decision provenance.")
        review.append(event)


def _upgrade_v1(record):
    # Old decisions can refer to geometry no longer available. At threshold zero
    # their recorded class uniquely determines the reason, even for stale results.
    record = deepcopy(record)
    saved = record["automatic"]
    for values in (record["pending"], saved["settings"] if saved else None):
        if values is not None:
            if len(values) != 2:
                raise ValueError("Invalid legacy matching thresholds.")
            values.append(0.0)
    for row in saved["rows"] if saved else ():
        row["classification_reason"] = LEGACY_REASONS[row["auto_class"]]
    for event in record["history"]:
        if event["action"] == "decision":
            event["settings"]["minimum_total_overlap_fraction"] = 0.0
            event["automatic_reason"] = LEGACY_REASONS[event["automatic_class"]]
    record["version"] = 2
    return record


def _read_settings(values):
    if len(values) != 3:
        raise ValueError("Expected three saved automatic matching thresholds.")
    return MatchingSettings(*values)


def validate(state, record):
    """Validate on the open worker before replacing the user's live project."""
    if record is None:
        return None
    try:
        pair = state.matching_pair
        if record["version"] == 1:
            record = _upgrade_v1(record)
        if record["version"] != 2:
            raise ValueError("Unsupported association record version.")
        for role, owner in zip(("nuclear","cytoplasmic"),record["sessions"],strict=True):
            pair.select(role, owner)
        review = AssociationReview(pair)
        validate_history(review, record["history"], state)
        pending = record["pending"]
        pending = None if pending is None else _read_settings(pending)
        saved = record["automatic"]
        executed = _read_settings(saved["settings"]) if saved is not None else None
        result = None
        captured = pair.capture() if saved is not None else None
        if saved is not None and captured is not None:
            stats = scan_overlaps(captured, lambda percent: None)
            measurements = saved["measurements"]
            if len(measurements) != 2 or any(type(v) is not int or v < 0 for v in measurements):
                raise ValueError("Invalid source measurement revisions.")
            candidate = classify_population(stats, executed, measurements)
            if dependencies(candidate) == saved["dependencies"]:
                if (any(old > now for old, now in zip(measurements, captured.measurement_revisions))
                        or scientific_record(candidate) != saved):
                    raise ValueError("Saved automatic matching evidence differs from retained geometry.")
                for key,event in review.records.items():
                    if event["dependencies"] == saved["dependencies"] and (
                            key[2] not in stats.nuclear_voxels or event["status"] == "Matched"
                            and event["cytoplasmic_id"] not in stats.cytoplasmic_voxels):
                        raise ValueError("Reviewed label is absent from its saved population.")
                if not record["interrupted"]:
                    result = candidate
        return record["sessions"], pending, saved, result, record["history"], record["interrupted"]
    except (KeyError, IndexError, StopIteration, TypeError) as error:
        raise ValueError("Incomplete association checkpoint.") from error


def install(state, validated):
    if validated is None:
        state.matching_pair.pending_settings = MatchingSettings(minimum_total_overlap_fraction=0.0)
        return
    owners, pending, historical, result, events, interrupted = validated
    pair = state.matching_pair
    for role, owner in zip(("nuclear","cytoplasmic"), owners, strict=True):
        pair.select(role,owner)
    pair.pending_settings = pending
    if interrupted:
        pair.error = "Prior calculation did not finish successfully. Run automatic matching again."
    pair.attempted = historical is not None
    if historical is not None:
        pair.executed_settings = MatchingSettings(*historical["settings"])
    pair.historical_result = historical if result is None else None
    for event in events:
        pair.review.append(event)
    if result is not None:
        identity = pair.capture().population_identity
        pair._result = replace(result, statistics=replace(result.statistics, population_identity=identity))
    state.matching_result_changed.emit()
    state.association_review_changed.emit()
