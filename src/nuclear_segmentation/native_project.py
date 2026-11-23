"""Versioned native checkpoints. No scientific stages are run on open."""
from dataclasses import asdict, fields, replace
from functools import partial
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
from zipfile import ZipFile

import numpy as np
import pandas as pd
from qtpy.QtCore import QObject, QLockFile, Signal, Slot

from .core import density_z_planes
from .image_import import load_tiff_dataset, load_verified_image, source_digest
from .lif_import import load_lif_dataset
from .geometry_edit import effective_masks, validate_history
from .manual_review import ManualReview
from .normalization import mask_digest
from .normalization_workspace import _options_from_report, cellpose_configuration, normalization_fingerprint
from .project_state import ChannelDescriptor, ProjectState, SegmentationSession
from .scientific_input import validate_scientific_input
from .z_correction import validate_options

MANIFEST = "project.json"
CHECKPOINT = "checkpoint.ncs"
VERSION = 2
SHARED_FIELDS = (
    "voxel_spacing_zyx_um", "calibration_source", "segmentation_channel_index",
    "analysis_mode", "roi_mask_yx", "working_offset_yx",
    "normalization_configuration", "normalization_preview", "normalization_is_current",
    "normalization_roi_mask_yx", "normalization_roi_vertices_yx", "normalization_roi_provenance",
    "z_correction_options", "z_correction_reports", "z_correction_applied",
)
V1_SESSION_FIELDS = (
    "perinuclear_markers", "segmentation_result", "segmentation_is_current",
    "filtering_result", "filtering_is_current", "scientific_roi", "scientific_roi_provenance",
    "scientific_roi_reviewed", "quantification_result", "quantification_is_current", "legacy_import",
)
SESSION_FIELDS = tuple(field.name for field in fields(SegmentationSession) if field.name != "_session_id")
SOURCE_FIELDS = (
    "source_format", "series_index", "series_identifier", "series_name", "reported_axes",
    "reported_shape", "standardized_shape", "dtype", "voxel_spacing_zyx_um",
    "calibration_source", "time_index", "acquisition_metadata", "axes_override",
)


class MissingProjectSource(FileNotFoundError):
    """The saved source path needs user-directed relocation."""


def _pack(archive, value):
    if isinstance(value, np.ndarray):
        name = f"arrays/{len(archive.filelist)}.npy"
        with archive.open(name, "w", force_zip64=True) as stream:
            np.save(stream, value, allow_pickle=False)
        return {"array": name}
    if isinstance(value, pd.DataFrame):
        return {"table": {
            "index": _pack(archive, value.index.tolist()),
            "index_name": _pack(archive, value.index.name),
            "index_dtype": str(value.index.dtype),
            "columns": [[name, str(series.dtype), _pack(archive, series.tolist())]
                        for name, series in value.items()],
        }}
    if isinstance(value, dict):
        return {"map": [[key, _pack(archive, item)] for key, item in value.items()]}
    if isinstance(value, (tuple, set)):
        return {type(value).__name__: [_pack(archive, item) for item in value]}
    if isinstance(value, list):
        return [_pack(archive, item) for item in value]
    if isinstance(value, np.generic):
        return _pack(archive, value.item())
    if value is pd.NA:
        return {"missing": True}
    if isinstance(value, float) and not np.isfinite(value):
        return {"float": str(value)}
    if isinstance(value, Path):
        return str(value)
    return value


def _unpack(archive, value):
    if isinstance(value, list):
        return [_unpack(archive, item) for item in value]
    if not isinstance(value, dict):
        return value
    kind, item = next(iter(value.items()))
    if kind == "array":
        # None of the array members are extracted as filesystem paths.
        with archive.open(item) as stream:
            return np.load(stream, allow_pickle=False)
    if kind == "table":
        index = pd.Index(_unpack(archive, item["index"]), dtype=item["index_dtype"],
                         name=_unpack(archive, item["index_name"]))
        return pd.DataFrame({
            name: pd.Series(_unpack(archive, values), dtype=dtype, index=index)
            for name, dtype, values in item["columns"]
        }, index=index)
    if kind == "map":
        result = {}
        for key, val in item:
            if key in result:
                raise ValueError("The checkpoint contains a duplicate record key.")
            result[key] = _unpack(archive, val)
        return result
    if kind in ("set", "tuple"):
        return (set if kind == "set" else tuple)(_unpack(archive, x) for x in item)
    if kind == "missing":
        return pd.NA
    if kind == "float":
        return float(item)
    raise ValueError(f"Unsupported project record: {kind}")


def snapshot_state(state):
    """Snapshot references after workers stop. Project I/O holds the editor closed."""
    sessions = {}
    for session_id, session in state.sessions.items():
        record = {key: getattr(session, key) for key in SESSION_FIELDS}
        segmentation = session.segmentation_result
        if segmentation is not None:
            model_input = segmentation.get("model_input_volume")
            if model_input is not None and any(
                np.shares_memory(model_input, dataset.data_czyx)
                for dataset in (state.original_dataset, state.working_dataset)
            ):
                record["segmentation_result"] = dict(segmentation, model_input_volume=None)
        sessions[session_id] = record
    return {
        "source": {
            **{key: getattr(state.original_dataset, key) for key in SOURCE_FIELDS},
            "path": str(state.original_dataset.source_path),
            "sha256": state.original_dataset.source_sha256,
            "channels": [asdict(channel) for channel in state.original_dataset.channels],
        },
        "channels": [asdict(channel) for channel in state.channels],
        "crop": asdict(state.crop_provenance) if state.crop_provenance else None,
        "state": {key: getattr(state, key) for key in SHARED_FIELDS},
        "sessions": sessions,
        "active_session_id": state.active_session_id,
    }


def _manifest(folder):
    with (folder / MANIFEST).open(encoding="utf-8") as stream:
        record = json.load(stream)
    version = record.get("version")
    if (
        record.get("format") != "NuCytoSeg" or type(version) is not int
        or version not in (1, VERSION) or not isinstance(record.get("id"), str)
        or not record["id"]
    ):
        raise ValueError("This is not a supported native NuCytoSeg project.")
    return record


def save_project(folder, snapshot, active=None):
    """Publish one closed archive atomically, leaving the old file on any failure."""
    folder = Path(folder)
    for session in snapshot["sessions"].values():
        legacy = session["legacy_import"]
        if legacy and folder.resolve().is_relative_to(Path(legacy["folder"]).resolve()):
            raise ValueError("Save the native project outside the legacy results directory.")
    source = snapshot["source"]
    digest = source_digest(Path(source["path"]))
    if source["sha256"] is not None and source["sha256"] != digest:
        raise ValueError("The original image changed after import. Reopen the original image before saving.")
    snapshot = dict(snapshot, source=dict(source, sha256=digest))
    created = active is None
    if created:
        folder.mkdir(exist_ok=False)
    temporary = None
    lock = QLockFile(str(folder / ".write.lock"))
    lock.setStaleLockTime(0)
    locked = False
    try:
        locked = lock.tryLock()
        if not locked:
            raise ValueError("Another application is saving this project.")
        if created:
            project_id = uuid4().hex
            with (folder / MANIFEST).open("x", encoding="utf-8") as stream:
                json.dump({"format": "NuCytoSeg", "version": VERSION, "id": project_id}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            project_id, expected_revision = active
            manifest = _manifest(folder)
            if manifest["version"] != VERSION:
                raise ValueError("Use Save Project As to convert a version 1 project.")
            with ZipFile(folder / CHECKPOINT) as previous:
                revision = previous.read("revision.txt").decode("ascii")
                archive_id = previous.read("project-id.txt").decode("ascii")
            if manifest["id"] != project_id or archive_id != project_id or revision != expected_revision:
                raise ValueError("The project changed on disk. Open it again or choose Save Project As.")
        revision = uuid4().hex
        with NamedTemporaryFile(dir=folder, prefix=".save-", delete=False) as stream:
            temporary = Path(stream.name)
            with ZipFile(stream, "w") as archive:
                archive.writestr("source.json", json.dumps(_pack(archive, snapshot["source"]), allow_nan=False))
                state = {key: value for key, value in snapshot.items() if key != "source"}
                archive.writestr("state.json", json.dumps(_pack(archive, state), allow_nan=False))
                archive.writestr("revision.txt", revision)
                archive.writestr("project-id.txt", project_id)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, folder / CHECKPOINT)
        return (project_id, revision), digest
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        abandoned = locked and created and not (folder / CHECKPOINT).exists()
        if abandoned:
            (folder / MANIFEST).unlink(missing_ok=True)
        if locked:
            lock.unlock()
        if abandoned:
            try:
                folder.rmdir()
            except OSError:
                pass


def _rebase_source(value, old, new):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "legacy_import":
                continue
            if key in ("source_path", "source", "path") and isinstance(item, str) and item == old:
                value[key] = new
            _rebase_source(item, old, new)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _rebase_source(item, old, new)


def open_project(folder, replacement=None, bioimage_factory=None):
    folder = Path(folder)
    record = _manifest(folder)
    with ZipFile(folder / CHECKPOINT) as archive:
        if archive.read("project-id.txt").decode("ascii") != record["id"]:
            raise ValueError("Checkpoint identity does not match its project folder.")
        revision = archive.read("revision.txt").decode("ascii")
        source = _unpack(archive, json.loads(archive.read("source.json")))
        dataset = _open_source(source, replacement, bioimage_factory)
        payload = _unpack(archive, json.loads(archive.read("state.json")))
    _rebase_source(payload, source["path"], str(dataset.source_path))
    state = _validated_state(dataset, payload, record["version"])
    payload["checkpoint_version"] = record["version"]
    return (record["id"], revision), dataset, state, payload


def _open_source(source, replacement, bioimage_factory):
    expected = source["sha256"]
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("This native checkpoint has no valid source SHA-256.")
    path = Path(replacement or source["path"])
    if not path.is_file():
        if replacement is None:
            raise MissingProjectSource(source["path"])
        raise FileNotFoundError(f"Image file does not exist: {path}")
    path = path.resolve()
    options = {
        "time_index": source["time_index"],
        "spacing_override": source["voxel_spacing_zyx_um"],
    }
    if source["source_format"] == "Leica LIF":
        read = partial(load_lif_dataset, path, source["series_index"], bioimage_factory=bioimage_factory, **options)
    else:
        read = partial(load_tiff_dataset, path, axes_override=source["axes_override"], **options)
    dataset = load_verified_image(path, read, expected)
    keys = ("source_format", "series_index", "series_identifier", "reported_axes", "reported_shape",
            "standardized_shape", "dtype", "time_index")
    if any(getattr(dataset, key) != source[key] for key in keys) or (
        dataset.acquisition_metadata.get("axes_interpreted_before_reorder")
        != source["acquisition_metadata"].get("axes_interpreted_before_reorder")
    ):
        raise ValueError("The source series, timepoint, axes, or dimensions do not match the checkpoint.")
    if [channel.original_name for channel in dataset.channels] != [
        item["original_name"] for item in source["channels"]
    ]:
        raise ValueError("The source channel identities do not match the checkpoint.")
    return replace(
        dataset, series_name=source["series_name"], calibration_source=source["calibration_source"],
        acquisition_metadata=source["acquisition_metadata"],
    )


def _validate_shared_controls(payload, indexes):
    """Check editable fields before the open worker hands records to Qt widgets."""
    projection_channel, projection_method = payload["projection_settings"]
    if projection_channel not in indexes or projection_method not in {"maximum", "mean"}:
        raise ValueError("The checkpoint contains invalid XY projection controls.")
    if (
        set(payload["z_settings"]) != indexes or payload["z_inspected"] not in indexes
        or not set(payload["z_selected"]) <= indexes or type(payload["z_include_zeros"]) is not bool
    ):
        raise ValueError("The checkpoint contains invalid Z-correction channel selections.")
    for settings in payload["z_settings"].values():
        validate_options(settings)
    _validate_normalization_controls(payload["normalization_settings"], indexes)


def _validate_normalization_controls(normalization, indexes):
    if (
        normalization["channel_index"] not in indexes
        or normalization["mode"] not in {"cellpose_default", "disabled", "percentile", "roi_fixed"}
        or normalization["input_source"] not in {"working_original", "z_corrected"}
        or type(normalization["norm3D"]) is not bool
        or not all(0 <= normalization[key] <= 100 for key in ("lower", "upper"))
    ):
        raise ValueError("The checkpoint contains invalid normalization controls.")


def _validate_display_state(display, indexes):
    if not isinstance(display, dict) or display.get("mode", "raw") not in ("raw", "edited", "retained", "filtered", "selected", "custom"):
        raise ValueError("Invalid session display mode.")
    channels = display.get("channels", {})
    if not isinstance(channels, dict) or any(
        type(index) is not int or index not in indexes or type(visible) is not bool
        for index, visible in channels.items()
    ):
        raise ValueError("Invalid session channel visibility.")
    for role in ("raw", "edited", "retained", "filtered", "roi"):
        settings = display.get(role, {})
        if not isinstance(settings, dict) or type(settings.get("visible", True)) is not bool:
            raise ValueError("Invalid session layer visibility.")
        opacity = settings.get("opacity", 1)
        if type(opacity) not in (int, float) or not 0 <= opacity <= 1:
            raise ValueError("Invalid session layer opacity.")
    if display.get("mode") == "selected" and (
        display.get("selected_layer") not in ("raw", "edited", "retained", "filtered")
        or type(display.get("selected_id")) is not int or display["selected_id"] <= 0
        or type(display.get("geometry_revision")) is not int
    ):
        raise ValueError("Invalid selected mask display settings.")


def _validate_segmentation_controls(segmentation):
    if (
        not isinstance(segmentation["MODEL_NAME"], str)
        or segmentation["PROCESSING_DEVICE"] not in {"auto", "cpu"}
        or any(type(segmentation[key]) is not bool for key in ("CELLPOSE_DO_3D", "CELLPOSE_RESAMPLE"))
        or len(segmentation["FLOW3D_SMOOTH"]) != 3
        or not all(np.isfinite(value) for value in segmentation["FLOW3D_SMOOTH"])
        or not all(type(segmentation[key]) is int and -(2**31) <= segmentation[key] < 2**31
                   for key in ("CELLPOSE_MIN_SIZE_VOXELS", "BATCH_SIZE"))
        or not all(np.isfinite(segmentation[key]) for key in ("CELLPROB_THRESHOLD", "CELLPOSE_STITCH_THRESHOLD"))
        or any(segmentation[key] is not None and not np.isfinite(segmentation[key])
               for key in ("CELLPOSE_RESCALE", "DIAMETER_PIXELS"))
    ):
        raise ValueError("The checkpoint contains invalid segmentation controls.")


def _validate_controls(payload, indexes):
    _validate_shared_controls(payload, indexes)
    _validate_segmentation_controls(payload["segmentation_settings"])


def _label_ids(array, shape):
    if len(shape) != 3 or array.shape != shape or not array.size or array.dtype.kind not in "ui" or array.min() < 0:
        raise ValueError("The checkpoint contains incompatible label mask data.")
    return set(map(int, np.unique(array))) - {0}


def _validated_state(dataset, payload, version):
    # A detached state lets existing grid and configuration validators run in the worker.
    state = ProjectState()
    stored = payload["state"]
    state.set_dataset(dataset)
    crop = payload["crop"]
    if crop:
        roi_mask = stored["roi_mask_yx"]
        if roi_mask.dtype != bool or roi_mask.shape != crop["roi_mask_shape_yx"] or not roi_mask.any():
            raise ValueError("The checkpoint contains an invalid crop inclusion mask.")
        state.set_calibration(crop["voxel_spacing_zyx_um"])
        state.apply_crop(crop["bounding_box_y0_y1_x0_x1"], crop["selection_vertices_yx"],
                         stored["roi_mask_yx"], crop["selection_type"])
        if asdict(state.crop_provenance) != crop:
            raise ValueError("Crop provenance does not match the restored working grid.")
    elif stored["roi_mask_yx"] is not None:
        raise ValueError("The checkpoint contains a crop mask without crop geometry.")
    if state.working_offset_yx != stored["working_offset_yx"]:
        raise ValueError("The checkpoint contains an incompatible spatial offset.")
    state.set_calibration(stored["voxel_spacing_zyx_um"], stored["calibration_source"])
    channels = [ChannelDescriptor(**item) for item in payload["channels"]]
    if [(c.index, c.original_name) for c in channels] != [
        (c.index, c.original_name) for c in dataset.channels
    ] or stored["segmentation_channel_index"] not in {c.index for c in channels}:
        raise ValueError("The checkpoint contains incompatible channel identities.")
    state.channels = channels
    for key in SHARED_FIELDS:
        setattr(state, key, stored[key])

    indexes = {channel.index for channel in channels}
    _validate_shared_controls(payload, indexes)
    normalization_fields = ("normalization_configuration", "normalization_is_current")
    previous_normalization = {key: stored[key] for key in normalization_fields}
    if version == 1:
        sessions = {"nuclear": SegmentationSession(
            **previous_normalization,
            geometry_revision=int(stored["segmentation_result"] is not None),
            measurement_revision=int(stored["filtering_result"] is not None),
            **{key: stored.get(key) if key == "legacy_import" else stored[key]
               for key in V1_SESSION_FIELDS},
            **{key: payload[key] for key in ("segmentation_settings", "filter_controls",
                                           "intensity_controls", "roi_grid_compatible")},
        )}
        active_id = "nuclear"
    else:
        sessions = {}
        for session_id, record in payload["sessions"].items():
            if session_id != "nuclear" and (
                not isinstance(session_id, str) or len(session_id) != 32
                or any(char not in "0123456789abcdef" for char in session_id)
            ):
                raise ValueError("The checkpoint contains an invalid session ID.")
            record = {"display_state": {}, **record}
            if set(record) == set(SESSION_FIELDS).difference(normalization_fields, {"normalization_settings"}):
                record = dict(record, normalization_settings=None, **previous_normalization)
            if set(record) != set(SESSION_FIELDS):
                raise ValueError("The checkpoint contains an incomplete session record.")
            session = SegmentationSession(session_id, **record)
            if (
                ProjectState._session_name(session.display_name) != session.display_name
                or session.scientific_role not in {"nuclear", "cytoplasmic"}
                or (session_id == "nuclear" and session.scientific_role != "nuclear")
                or (session.configured_channel_index is not None
                    and (type(session.configured_channel_index) is not int
                         or session.configured_channel_index not in indexes))
                or any(type(value) is not int or value < 0 for value in (
                    session.input_revision, session.geometry_revision, session.measurement_revision
                ))
                or any(type(value) is not bool for value in (
                    session.segmentation_is_current, session.filtering_is_current,
                    session.scientific_roi_reviewed, session.quantification_is_current,
                    session.roi_grid_compatible, session.normalization_is_current,
                ))
            ):
                raise ValueError("The checkpoint contains incompatible session metadata.")
            _validate_display_state(session.display_state, indexes)
            sessions[session_id] = session
        active_id = payload["active_session_id"]
        if active_id not in sessions or "nuclear" not in sessions:
            raise ValueError("The checkpoint contains an unknown active or missing original session.")

    # Shared historical preprocessing uses the original nuclear import provenance.
    state.restore_sessions(sessions, "nuclear")
    shared_inputs_valid = _validate_preprocessing(state, payload)
    for session_id, session in state.sessions.items():
        state.select_session(session_id)
        if session.normalization_settings is not None:
            _validate_normalization_controls(session.normalization_settings, indexes)
        if session.segmentation_settings is not None:
            _validate_segmentation_controls(session.segmentation_settings)
        if not shared_inputs_valid:
            session.segmentation_is_current = False
        _validate_normalization(state, payload, check_preview=session_id == active_id)
        _validate_results(state)
    for session in sessions.values():
        session.display_state.setdefault("channels", {c.index: c.visible for c in channels})
    state.select_session(active_id)
    return restored_state(state)


def restored_state(state):
    return {"working_dataset": state.working_dataset, "crop_provenance": state.crop_provenance,
            "channels": state.channels, "sessions": dict(state.sessions),
            "active_session_id": state.active_session_id,
            **{key: getattr(state, key) for key in SHARED_FIELDS}}


def _validate_preprocessing(state, payload):
    original = state.original_dataset
    working_shape = state.working_dataset.standardized_shape
    normalization_roi = state.normalization_roi_mask_yx
    if normalization_roi is not None:
        vertices = np.asarray(state.normalization_roi_vertices_yx)
        roi_provenance = state.normalization_roi_provenance or {}
        if (
            normalization_roi.dtype != bool or normalization_roi.shape != original.standardized_shape[-2:]
            or not normalization_roi.any() or vertices.ndim != 2 or vertices.shape[1] != 2
            or len(vertices) < 3 or not np.isfinite(vertices).all()
            or roi_provenance.get("mask_sha256") != mask_digest(normalization_roi)
            or roi_provenance.get("source") != str(original.source_path)
            or roi_provenance.get("series_index") != original.series_index
        ):
            raise ValueError("The checkpoint contains incompatible normalization ROI geometry.")
    z_previews = payload["z_previews"]
    for index, array in z_previews.items():
        if index not in state.z_correction_reports or array.shape != working_shape[1:] or array.dtype.kind != "f":
            raise ValueError("The checkpoint contains incompatible Z-correction preview data.")
    channel_names = {channel.index: channel.name for channel in state.channels}
    for index, report in tuple(state.z_correction_reports.items()):
        expected = {
            "options": state.z_correction_options.get(index),
            "working_shape_czyx": list(working_shape),
            "working_offset_yx": list(state.working_offset_yx),
            "voxel_spacing_zyx_um": list(state.voxel_spacing_zyx_um),
            "channel_name_at_calculation": channel_names.get(index),
            "source_path": str(original.source_path),
            "series_index": original.series_index,
            "series_identifier": str(original.series_identifier),
        }
        if index not in channel_names or any(report.get(key) != value for key, value in expected.items()):
            state.z_correction_reports.pop(index)
            z_previews.pop(index, None)
    legacy_reports = {
        index for index, report in state.z_correction_reports.items()
        if state.legacy_import and report.get("legacy_recorded")
        and len(report["gain"]) == working_shape[1]
        and report["options"] == state.legacy_import["configuration"]["z_corrections"].get(channel_names[index])
    }
    valid_applied = state.z_correction_applied.intersection(set(z_previews) | legacy_reports)
    applied_compatible = valid_applied == state.z_correction_applied
    if not applied_compatible:
        state.z_correction_applied = valid_applied
        state.normalization_is_current = False

    return applied_compatible


def _validate_normalization(state, payload, *, check_preview):
    working_shape = state.working_dataset.standardized_shape
    normalization = state.normalization_configuration or {}
    applied = normalization.get("provenance")
    if state.normalization_is_current:
        try:
            if state.legacy_import and normalization["mode"] == "legacy_recorded":
                state.normalization_is_current = (
                    normalization["cellpose_normalize"]
                    == state.legacy_import["configuration"]["cellpose"].get("normalize", True)
                )
            else:
                state.normalization_is_current = bool(applied and cellpose_configuration(applied) == normalization)
        except (KeyError, TypeError, ValueError):
            state.normalization_is_current = False
    reports = (applied, state.normalization_preview) if check_preview else (applied,)
    for report in reports:
        if not report:
            continue
        try:
            fingerprint = normalization_fingerprint(state, _options_from_report(report))
            # Verified identical bytes may have acquired a different filesystem timestamp.
            source = report.get("source", {})
            if source.get("path") == fingerprint["source"]["path"] and source.get("size") == fingerprint["source"]["size"]:
                report["source"] = fingerprint["source"]
            valid = all(report.get(key) == val for key, val in fingerprint.items())
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid and report is state.normalization_preview:
            state.normalization_preview = None
            payload["normalization_pixels"] = None
        if not valid and report is applied:
            state.normalization_is_current = False

    preview = payload["normalization_pixels"]
    if preview is not None and (preview.shape != working_shape[1:] or preview.dtype.kind not in "iuf"):
        raise ValueError("The checkpoint contains incompatible normalization preview data.")


def _validate_review_controls(filtering, session):
    if "volume_um3" not in filtering["properties"]:
        raise ValueError("The checkpoint contains incomplete scientific measurements.")
    controls = session.filter_controls
    if controls is None:
        controls = filtering["settings"]
    numeric = (
        "minimum_volume_um3", "minimum_sphericity", "z_guard_um",
    )
    if (
        any(not np.isfinite(controls.get(key, 0)) for key in numeric)
        or any(type(controls.get(key, 0)) is not int
               or not -(2**31) <= controls.get(key, 0) < 2**31
               for key in ("first_tissue_z", "last_tissue_z"))
        or any(not isinstance(controls.get(key, ""), str)
               for key in ("z_guard_mode", "intensity_combination", "perinuclear_combination"))
    ):
        raise ValueError("The checkpoint contains invalid review controls.")


def _validate_results(state):
    session = state.active_session
    original = state.original_dataset
    working_shape = state.working_dataset.standardized_shape
    channel_names = {channel.index: channel.name for channel in state.channels}
    grid = {
        "source_path": str(original.source_path), "source_series": original.series_index,
        "working_shape_czyx": working_shape, "voxel_spacing_zyx_um": state.voxel_spacing_zyx_um,
        "working_offset_yx": state.working_offset_yx,
        "crop_type": state.crop_provenance.selection_type if state.crop_provenance else None,
        "segmentation_channel": state.segmentation_channel,
    }

    def compatible(provenance):
        return bool(provenance) and all(provenance.get(key) == value for key, value in grid.items())

    segmentation = state.segmentation_result
    ids = set()
    if segmentation is not None:
        masks = segmentation["masks"]
        shape = segmentation["mask_shape"]
        ids = _label_ids(masks, shape)
        if (
            not ids or type(segmentation["object_count"]) is not int
            or segmentation["object_count"] != len(ids)
            or segmentation["maximum_label_id"] != max(ids)
        ):
            raise ValueError("The raw label IDs do not match the segmentation record.")
        for key in ("edited_masks", "model_input_volume", "raw_3d_probs"):
            array = segmentation.get(key)
            if array is not None and array.shape != shape:
                raise ValueError(f"Incompatible {key} shape in the checkpoint.")
        if segmentation.get("manual_review") is not None:
            ManualReview(masks, ids, segmentation["manual_review"])
        validate_history(segmentation, session)
        edited = segmentation.get("edited_masks")
        if edited is not None:
            ids = _label_ids(edited, shape)
            edited.setflags(write=False)
            if segmentation.get("edit_history"):
                if segmentation.get("edited_object_count") != len(ids):
                    raise ValueError("The committed label count differs from the edited geometry.")
            elif not np.array_equal(masks, edited):
                # Older checkpoints could store edits while measuring the original labels.
                segmentation["edited_object_count"] = len(ids)
                if segmentation.get("manual_review") is not None:
                    segmentation["original_manual_review"] = segmentation.pop("manual_review")
                if (state.filtering_result or {}).get("geometry_revision") != session.geometry_revision:
                    state.clear_filtering_result(emit=False)
                    state.scientific_roi_reviewed = False
        for key, expected in (("session_id", session.session_id), ("scientific_role", session.scientific_role),
                              ("geometry_revision", session.geometry_revision)):
            if key in segmentation and segmentation[key] != expected:
                raise ValueError("The segmentation record belongs to a different session or mask revision.")
        if state.segmentation_is_current:
            try:
                config = segmentation["scientific_config"]
                validate_scientific_input(state, config, completed_result=segmentation)
                provenance = segmentation.get("input_provenance")
                state.segmentation_is_current = (
                    shape == working_shape[1:] and compatible(provenance)
                    and provenance["z_corrections"] == config["Z_CORRECTIONS"]
                    and provenance["cellpose_normalize"] == config["CELLPOSE_NORMALIZE"]
                )
            except (KeyError, ValueError):
                state.segmentation_is_current = False
        if "device" not in segmentation:
            raise ValueError("The checkpoint contains incomplete segmentation metadata.")
        masks.setflags(write=False)
    else:
        state.segmentation_is_current = False

    filtering = state.filtering_result
    roi = state.scientific_roi
    effective_planes = None
    if filtering is not None:
        if segmentation and segmentation.get("edit_history") and (
            filtering.get("geometry_revision") != session.geometry_revision
            or filtering.get("session_id") != session.session_id
        ):
            raise ValueError("Measurements belong to a different geometry revision.")
        _validate_review_controls(filtering, session)
        intensity_controls = session.intensity_controls
        if intensity_controls is not None and (
            set(intensity_controls) != {item["index"] for item in filtering["intensity_definitions"]}
            or any(type(checked) is not bool or not np.isfinite([lower, upper]).all()
                   for checked, lower, upper in intensity_controls.values())
        ):
            raise ValueError("The checkpoint contains incompatible intensity controls.")
        retained = filtering["retained_ids"]
        filtered = filtering["filtered_masks"]
        if segmentation is None or (
            set(filtering["properties"].index) != ids or not filtering["properties"].index.is_unique
            or _label_ids(filtered, segmentation["mask_shape"]) != set(retained)
            or not set(retained) <= ids or len(set(retained)) != len(retained)
            or filtering["raw_mask_shape"] != segmentation["mask_shape"]
        ):
            raise ValueError("The checkpoint's filtered masks or measurements do not match its segmentation.")
        if any(not np.array_equal(saved, np.where(np.isin(raw, retained), raw, 0))
               for saved, raw in zip(filtered, effective_masks(segmentation))):
            raise ValueError("Filtered label geometry does not match the raw masks.")
        filtered.setflags(write=False)
        measured_roi = filtering["scientific_roi"]
        if measured_roi is not None and (measured_roi.shape != filtering["raw_mask_shape"] or measured_roi.dtype != bool):
            raise ValueError("The checkpoint contains incompatible measurement ROI geometry.")
        provenance = filtering.get("measurement_provenance", {})
        expected_markers = {
            (item["channel_index"], channel_names[item["channel_index"]], item["ring_inner_um"],
             item["ring_outer_um"], item["pixel_threshold"])
            for item in state.perinuclear_markers if item["enabled"]
        }
        measured_markers = {
            (item["channel_index"], item["name"], item["ring_inner_um"],
             item["ring_outer_um"], item["configured_pixel_threshold"])
            for item in filtering["perinuclear_definitions"]
        }
        matching_roi_grid = (
            compatible(provenance) and roi is not None and roi.dtype == bool
            and roi.shape == working_shape[1:] and roi.any()
            and state.scientific_roi_provenance == dict(provenance, mask_shape=roi.shape)
            and np.array_equal(roi, measured_roi)
        )
        state.scientific_roi_reviewed = bool(
            state.scientific_roi_reviewed and filtering["roi_reviewed"] and matching_roi_grid
            and session.roi_grid_compatible
        )
        filtering["roi_reviewed"] = state.scientific_roi_reviewed
        state.filtering_is_current = bool(
            state.filtering_is_current and state.segmentation_is_current and compatible(provenance)
            and filtering["input_segmentation_provenance"] == segmentation.get("input_provenance")
            and provenance == segmentation.get("input_provenance")
            and expected_markers == measured_markers
            and (measured_roi is not None or session.scientific_role == "cytoplasmic")
            and session.roi_grid_compatible
        )
        if state.filtering_is_current and session.scientific_role == "nuclear":
            try:
                _, effective_planes = density_z_planes(
                    filtering["settings"], working_shape[1], state.voxel_spacing_zyx_um[0]
                )
            except ValueError:
                state.filtering_is_current = False
    else:
        state.filtering_is_current = False
        state.scientific_roi_reviewed = bool(
            state.legacy_import and state.scientific_roi_reviewed and roi is not None
            and state.legacy_import.get("roi_sha256") == mask_digest(roi)
            and state.segmentation_is_current and state.scientific_roi_provenance
            == dict(segmentation["input_provenance"], mask_shape=roi.shape)
        )
    if roi is not None and (roi.shape != working_shape[1:] or roi.dtype != bool):
        state.scientific_roi = None
        state.scientific_roi_provenance = None
        state.scientific_roi_reviewed = False
    if filtering and not session.roi_grid_compatible:
        # A transformed viewer layer cannot be accepted in the fixed scientific grid.
        state.scientific_roi_provenance = None

    quant = state.quantification_result
    state.quantification_is_current = bool(
        state.quantification_is_current and quant and state.filtering_is_current
        and (not segmentation.get("edit_history") or (quant.get("geometry_revision") == session.geometry_revision
             and quant.get("session_id") == session.session_id))
        and state.scientific_roi_reviewed and quant["density_status"] == "valid"
        and np.isfinite(quant["roi_effective_volume_um3"]) and quant["roi_effective_volume_um3"] > 0
        and quant["roi_reviewed"] and effective_planes is not None
        and tuple(effective_planes.tolist()) == quant["effective_sampling_planes"]
        and bool(roi[effective_planes].any())
        and quant["scientific_roi_sha256"] == mask_digest(roi)
        and quant["scientific_roi_provenance"] == state.scientific_roi_provenance
        and quant["source_identity"] == filtering["measurement_provenance"]
        and quant["filter_settings"] == filtering["settings"]
        and (session.filter_controls is None or filtering["settings"] == session.filter_controls)
        and session.scientific_role == "nuclear"
        and quant["retained_mask_ids"] == filtering["retained_ids"]
        and quant["executed_segmentation_config"] == segmentation["scientific_config"]
        and quant["voxel_spacing_zyx_um"] == state.voxel_spacing_zyx_um
        and all(quant.get(key) == val for key, val in quant["density"].items())
    )


class ProjectWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    missing_source = Signal(str)
    finished = Signal()

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self):
        try:
            self.succeeded.emit(self.operation())
        except MissingProjectSource as error:
            self.missing_source.emit(str(error))
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            self.finished.emit()
