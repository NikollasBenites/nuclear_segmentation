"""Session-scoped label transactions. History stores changed voxels per commit."""
from copy import deepcopy
from datetime import datetime, timezone

import numpy as np

from .manual_review import mask_signature


def effective_masks(result):
    edited = result.get("edited_masks")
    return result["masks"] if edited is None else edited


def retained_geometry(masks, ids):
    """Materialize a read-only display projection with plane-sized scratch space."""
    out = np.zeros_like(masks)
    for z, plane in enumerate(masks):
        np.copyto(out[z], plane, where=np.isin(plane, ids))
    out.setflags(write=False)
    return out


def previous_reviewed_ids(record):
    if "previous_reviewed_ids" in record:
        return record["previous_reviewed_ids"]
    if record.get("editing_scope") == "filtered" and "reviewed_ids" in record:
        # Phase 2D.4.3 filtered edits could only create/remove visible IDs.
        return sorted(set(record["reviewed_ids"]).difference(record["new_ids"]) | set(record["removed_ids"]))
    return None


def reconcile_all_mask_edit(record, previous):
    """Propagate a proofreading selection only when the edited labels have clear membership."""
    kept = _update_retained(record, set(previous))
    return None if kept is None else sorted(kept)


def _update_retained(record, kept):
    params = record["parameters"]
    if record["operation"] == "split" and params["parent"] in kept:
        kept.add(params["new_id"])
    elif record["operation"] == "merge":
        members = set(params["ids"])
        if members & kept and members - kept:
            return None
    elif record["operation"] == "paint/erase":
        before, after = record["before"], record["after"]
        transferred = (before != 0) & (after != 0)
        if record["new_ids"]:
            return None
        if transferred.any():
            source, target = before[transferred], after[transferred]
            members = np.union1d(source, target)
            retained = [label for label in members if label in kept]
            if len(retained) < len(members) and np.any(np.isin(source, retained) != np.isin(target, retained)):
                return None
    kept.difference_update(record["removed_ids"])
    return kept


def voxel_step(operation, parameters, indexes, before, after, scope):
    return dict(operation=operation, parameters=parameters, indexes=indexes, before=before,
                after=after, editing_scope=scope, affected_ids=sorted(set(map(int, np.union1d(before, after))) - {0}))


def delta_between(start, end, operation, parameters, scope):
    indexes = np.flatnonzero(start != end)
    return voxel_step(operation, parameters, indexes, start.flat[indexes], end.flat[indexes], scope)


def replay_sequence(base, steps, previous, dtype):
    """Verify sparse operations in order on one scratch volume, including intermediate IDs."""
    working = base.astype(dtype, copy=True)
    labels, sizes = np.unique(base, return_counts=True)
    counts = dict(zip(map(int, labels), map(int, sizes), strict=True))
    kept = None if previous is None else set(previous)
    affected = set()
    excluded = set() if kept is None else set(counts).difference(kept, {0})
    for step in steps:
        op, params = step["operation"], step["parameters"]
        idx, before, after = (step[k] for k in ("indexes", "before", "after"))
        if (not isinstance(params, dict) or step["editing_scope"] not in ("filtered", "all")
                or any(not isinstance(a, np.ndarray) or a.ndim != 1 or a.dtype.kind not in "iu"
                       for a in (idx, before, after)) or not idx.size or idx.shape != before.shape
                or idx.shape != after.shape or idx.min() < 0 or idx.max() >= working.size
                or np.any(idx[1:] <= idx[:-1]) or np.any(before == after) or after.min() < 0
                or after.max() > np.iinfo(dtype).max or not np.array_equal(working.flat[idx], before)):
            raise ValueError("The operation sequence does not match the working geometry.")
        old, old_n = np.unique(before, return_counts=True)
        new, new_n = np.unique(after, return_counts=True)
        touched = set(map(int, old)) | set(map(int, new))
        if step.get("affected_ids") != sorted(touched - {0}):
            raise ValueError("Affected labels disagree with the operation delta.")
        if step["editing_scope"] == "filtered" and (previous is None or touched & excluded
                or not excluded.isdisjoint(np.unique(base.flat[idx]))):
            raise ValueError("Filtered operation touches an excluded object.")
        existed = {i for i in touched if counts.get(i, 0)}
        if op == "split":
            parent, child = params["parent"], params["new_id"]
            if parent <= 0 or child <= 0 or counts.get(child, 0) or not np.all(before == parent) or not np.all(after == child):
                raise ValueError("The split must transfer only parent voxels to an unused child ID.")
            if counts[parent] <= idx.size:
                raise ValueError("The parent must keep at least one voxel. Erase some of the child region.")
        elif op == "merge":
            ids, target = params["ids"], params["retained_id"]
            inputs = {str(i): counts.get(i, 0) for i in ids}
            if (len(set(ids)) != len(ids) or len(ids) < 2 or target not in ids or min(ids) <= 0
                    or not all(inputs.values()) or params["input_counts"] != inputs
                    or params["result_count"] != sum(inputs.values()) or not np.all(after == target)
                    or set(old) != set(ids) - {target} or idx.size != sum(inputs.values()) - counts[target]):
                raise ValueError("Merge must relabel every source voxel with the selected target ID.")
        elif op == "remove":
            label = params["id"]
            if label <= 0 or not np.all(before == label) or np.any(after) or idx.size != counts.get(label):
                raise ValueError("Removal must cover the entire selected object.")
        elif op != "paint/erase" or params:
            raise ValueError("Unknown operation in geometry transaction.")
        working.flat[idx] = after
        for label, n in zip(old, old_n, strict=True):
            counts[int(label)] -= int(n)
        for label, n in zip(new, new_n, strict=True):
            counts[int(label)] = counts.get(int(label), 0) + int(n)
        present = {i for i in touched if counts[i]}
        added, removed = sorted(present - existed - {0}), sorted(existed - present - {0})
        if kept is not None:
            if step["editing_scope"] == "filtered":
                kept.difference_update(removed)
                kept.update(added)
            else:
                kept = _update_retained(dict(step, new_ids=added, removed_ids=removed), kept)
        affected.update(touched - {0})
    return working, None if kept is None else sorted(kept), sorted(affected)


def undo_stack(history):
    stack = []
    for record in history:
        if record["operation"] == "undo":
            if not stack or stack[-1]["result_revision"] != record["parameters"]["revision"]:
                raise ValueError("Invalid geometry undo history.")
            stack.pop()
        else:
            stack.append(record)
    return stack


class GeometryEdit:
    def __init__(self, state, *, filtered=False, track_strokes=False):
        if not state.segmentation_is_current:
            raise ValueError("Current segmentation is required for editing.")
        self.state = state
        self.owner = state.session_revision()
        result = state.segmentation_result
        self.base = effective_masks(result)
        previous_ids = (label for record in result.get("edit_history", ()) for label in record["affected_ids"])
        self.next_id = 1 + max(int(self.base.max()), int(result["masks"].max()), max(previous_ids, default=0))
        self.filter_record = state.filtering_result if state.filtering_is_current else None
        self.retained_ids = state.proofreading_ids
        if filtered and self.retained_ids is None:
            raise ValueError("Current filtering is unavailable.")
        self.filtered = filtered
        self.steps = []
        self.redo_steps = []
        self.undo_floor = 0
        self.pending_source = None
        self.tracks_strokes = track_strokes
        self._excluded_ids = None
        if filtered:
            self._prepare_filtered_scope()
        source = self.filter_record["filtered_masks"] if filtered and self.filter_record is not None else self.base
        self.preview = np.array(source, dtype=np.promote_types(self.base.dtype, np.uint32),
                                order="C", copy=True)
        if filtered and self.filter_record is None:
            self._restore_or_hide(self.preview, True)
        self.paused = False
        self.operation = "paint/erase"
        self.parameters = {}

    @property
    def filter_current(self):
        if self.filter_record is None:
            return (self.retained_ids is not None and self.state.provisional_ids is self.retained_ids
                    and self.owner == self.state.session_revision())
        return (self.state.filtering_is_current
                and self.state.filtering_result is self.filter_record)

    @property
    def selection_current(self):
        if self.retained_ids is None:
            return self.state.proofreading_ids is None
        return self.filter_current

    def has_changes(self):
        source = self._visible_planes() if self.filtered else self.base
        return any(not np.array_equal(a, b) for a, b in zip(self.preview, source, strict=True))

    def is_pristine(self):
        return self.operation == "paint/erase" and not self.has_changes()

    def _prepare_filtered_scope(self):
        """Resolve protected IDs once against the immutable transaction source."""
        if self._excluded_ids is not None:
            return
        retained = set(self.retained_ids)
        kept = self.filter_record["filtered_masks"] if self.filter_record is not None else None
        error = "Retained selection disagrees with committed geometry. Recalculate measurements."
        if kept is not None and kept.shape != self.base.shape:
            raise ValueError(error)
        ids = set()
        for z, plane in enumerate(self.base):
            labels = set(map(int, np.unique(plane)))
            ids.update(labels)
            if kept is not None:
                hidden = labels.difference(retained, {0})
                expected = np.where(np.isin(plane, tuple(hidden)), 0, plane) if hidden else plane
                if not np.array_equal(kept[z], expected):
                    raise ValueError(error)
        if 0 in retained or not retained.issubset(ids):
            raise ValueError(error)
        self._excluded_ids = frozenset(ids.difference(retained, {0}))

    @property
    def excluded_ids(self):
        self._prepare_filtered_scope()
        return tuple(self._excluded_ids)

    def excluded_values(self, values):
        # Restrict the membership array to labels in this brush footprint.
        excluded = [v for v in np.unique(values) if v in self._excluded_ids]
        return np.isin(values, excluded)

    def protected_voxels(self, key):
        source = self.base[key]
        if self.filter_record is not None:
            return source != self.filter_record["filtered_masks"][key]
        return self.excluded_values(source)

    def _visible_planes(self):
        if self.filter_record is not None:
            yield from self.filter_record["filtered_masks"]
        else:
            for plane in self.base:
                yield np.where(np.isin(plane, self.retained_ids), plane, 0)

    def allows_label(self, label):
        return not self.filtered or label not in self._excluded_ids

    def _validate_scope(self):
        if not self.filter_current:
            raise ValueError("Filtering changed. Discard the preview and start again.")
        visible = self._visible_planes()
        excluded = self.excluded_ids
        for base, kept, preview in zip(self.base, visible, self.preview, strict=True):
            hidden = base != kept
            expected = kept if self.filtered else base
            changed = preview != expected
            if np.any(changed & hidden) or np.isin(preview[changed], excluded).any():
                if self.filtered:
                    raise ValueError("Edit touches an excluded object or uses its ID. Undo the stroke or discard and use Edit All Masks.")
                raise ValueError("This edit changes an excluded object. Save or discard before switching to filtered masks.")

    def set_filtered(self, filtered):
        if filtered == self.filtered:
            return
        if self.paused or self.operation != "paint/erase":
            raise ValueError("Resume ordinary brush editing before changing scope.")
        if not self.filter_current:
            raise ValueError("Current filtering is unavailable.")
        self._validate_scope()
        brush = self._ordinary_delta()
        if brush is not None:
            self.add_step(brush)
        self.undo_floor = len(self.steps)
        self.redo_steps.clear()
        self._restore_or_hide(self.preview, filtered)
        self.filtered = filtered

    def _restore_or_hide(self, target, hide):
        for base, kept, plane in zip(self.base, self._visible_planes(), target, strict=True):
            hidden = base != kept
            plane[hidden] = 0 if hide else base[hidden]

    @property
    def scope(self):
        return "filtered" if self.filtered else "all"

    def add_step(self, step):
        self.next_id = max(self.next_id, int(step["after"].max()) + 1)
        self.steps.append(step)
        self.redo_steps.clear()

    def _ordinary_delta(self):
        # Direct array-based callers may stage brush changes without a Labels event.
        # The interactive preview records each completed stroke at its write boundary.
        if self.tracks_strokes:
            return None
        reference, _, _ = replay_sequence(self.base, self.steps, self.retained_ids, self.preview.dtype)
        if self.filtered:
            self._restore_or_hide(reference, True)
        delta = delta_between(reference, self.preview, "paint/erase", {}, self.scope)
        return delta if delta["indexes"].size else None

    def _begin(self):
        if self.paused or self.operation != "paint/erase":
            raise ValueError("Apply or cancel the pending operation and resume editing first.")
        if self.filtered:
            self._validate_scope()
        brush = self._ordinary_delta()
        if brush is not None:
            self.add_step(brush)

    def _object(self, label):
        if label is None or label <= 0 or not self.allows_label(label) or not np.any(self.preview == label):
            raise ValueError(f"Object {label} is absent from the working preview.")

    def remove(self, label):
        self._object(label)
        self._begin()
        idx = np.flatnonzero(self.preview == label)
        before = self.preview.flat[idx]
        self.preview.flat[idx] = 0
        self.add_step(voxel_step("remove", {"id": label}, idx, before, np.zeros_like(before), self.scope))

    def merge(self, ids, target):
        ids = sorted(set(ids))
        if len(ids) < 2 or target not in ids:
            raise ValueError("Choose at least two objects and one of their IDs to retain.")
        counts = {}
        for label in ids:
            self._object(label)
            counts[str(label)] = int(np.count_nonzero(self.preview == label))
        self._begin()
        self.pending_source = self.preview.copy()
        self.pending_source.setflags(write=False)
        self.operation = "merge"
        self.parameters = dict(ids=ids, retained_id=target, input_counts=counts, result_count=sum(counts.values()))
        self.preview[np.isin(self.preview, ids)] = target

    def split(self, parent):
        self._object(parent)
        self._begin()
        new = max(self.next_id, int(self.preview.max()) + 1)
        if new > np.iinfo(self.preview.dtype).max:
            raise ValueError("No unused higher label ID is available.")
        self.pending_source = self.preview.copy()
        self.pending_source.setflags(write=False)
        self.operation, self.parameters = "split", dict(parent=parent, new_id=new)
        return new

    def cancel_operation(self):
        if self.pending_source is None:
            raise ValueError("There is no pending operation.")
        np.copyto(self.preview, self.pending_source)
        self._finish_operation()

    def _finish_operation(self):
        self.pending_source = None
        self.operation, self.parameters = "paint/erase", {}

    def apply_operation(self):
        source = self.pending_source
        if source is None:
            raise ValueError("There is no pending operation.")
        step = delta_between(source, self.preview, self.operation, self.parameters, self.scope)
        if self.operation == "split":
            parent, child = self.parameters["parent"], self.parameters["new_id"]
            if not step["indexes"].size:
                raise ValueError("Paint a child region inside the selected parent first.")
            if np.any(step["before"] != parent):
                raise ValueError("The split changed voxels outside the original parent.")
            if np.any(step["after"] != child):
                raise ValueError("Split voxels must belong to the parent or its proposed child.")
            if not np.any(self.preview == parent):
                raise ValueError("The parent must keep at least one voxel. Erase some of the child region.")
            self.next_id = child + 1
        else:
            target = self.parameters["retained_id"]
            sources = [i for i in self.parameters["ids"] if i != target]
            if (not np.all(step["after"] == target) or not np.isin(step["before"], sources).all()
                    or np.isin(self.preview, sources).any()):
                raise ValueError("Merge must relabel every source voxel with the selected target ID.")
        self.add_step(step)
        self._finish_operation()

    def replay(self, redo, native=None):
        source = self.redo_steps if redo else self.steps
        target = self.steps if redo else self.redo_steps
        step = source[-1]
        old, new = ("before", "after") if redo else ("after", "before")
        if not np.array_equal(self.preview.flat[step["indexes"]], step[old]):
            raise ValueError("The preview no longer matches this history step.")
        if native is None:
            self.preview.flat[step["indexes"]] = step[new]
        else:
            native()
        target.append(source.pop())

    def commit(self):
        state = self.state
        if (self.owner != state.session_revision() or not state.segmentation_is_current
                or self.base is not effective_masks(state.segmentation_result)):
            raise ValueError("The project, session, or scientific inputs changed.")
        if not self.selection_current:
            raise ValueError("The retained selection changed. Discard the preview and start again.")
        if self.preview.shape != self.base.shape or self.preview.dtype.kind not in "iu" or self.preview.min() < 0:
            raise ValueError("Edited geometry must contain nonnegative labels on the original ZYX grid.")
        if self.filtered:
            self._validate_scope()
        if self.pending_source is not None:
            raise ValueError(f"Apply or cancel the pending {self.operation} before saving.")
        steps = self.steps.copy()
        operation, parameters = self.operation, self.parameters
        reviewed = None
        if operation == "undo":
            edited = self.preview.copy()
        else:
            brush = self._ordinary_delta()
            if brush is not None:
                steps.append(brush)
            edited, reviewed, affected = replay_sequence(self.base, steps, self.retained_ids, self.preview.dtype)
            if self.filtered:
                # The replay scratch becomes the authoritative result. Compare the
                # protected projection using only a plane of temporary storage.
                matches = all(np.array_equal(complete, np.where(base != kept, base, preview))
                              for complete, base, kept, preview in
                              zip(edited, self.base, self._visible_planes(), self.preview, strict=True))
            else:
                matches = np.array_equal(edited, self.preview)
            if not matches:
                raise ValueError("Preview geometry differs from the recorded operation sequence.")
            operation = steps[0]["operation"] if len(steps) == 1 else "transaction"
            parameters = steps[0]["parameters"] if len(steps) == 1 else {}
        indexes = np.flatnonzero(self.base != edited)
        if not indexes.size:
            raise ValueError("The preview contains no changes.")
        before, after = self.base.flat[indexes], edited.flat[indexes]
        if operation == "undo":
            affected = sorted(set(map(int, np.union1d(before, after))) - {0})
        result = state.segmentation_result
        revision = self.owner.geometry_revision
        source_ids = set(map(int, np.unique(self.base))) - {0}
        result_ids = set(map(int, np.unique(edited))) - {0}
        record = dict(session_id=self.owner.session_id, source_revision=revision,
                      result_revision=revision + 1, operation=operation, parameters=parameters,
                      timestamp=datetime.now(timezone.utc).isoformat(),
                      new_ids=sorted(result_ids - source_ids), removed_ids=sorted(source_ids - result_ids),
                      affected_ids=affected,
                      before_signature=mask_signature(self.base), after_signature=mask_signature(edited),
                      indexes=indexes, before=before, after=after)
        record["editing_scope"] = "filtered" if self.filtered else "all"
        if len(steps) > 1:
            record["steps"] = steps
        elif steps:
            record["editing_scope"] = steps[0]["editing_scope"]
        if self.retained_ids is not None:
            record["previous_reviewed_ids"] = sorted(map(int, self.retained_ids))
        if operation == "undo":
            reviewed = previous_reviewed_ids(undo_stack(result["edit_history"])[-1])
        if reviewed is not None:
            record["reviewed_ids"] = reviewed
        filtering = state.filtering_result
        if filtering is not None:
            record["filter_definitions"] = {
                "intensity": {item["index"]: item["column"] for item in filtering.get("intensity_definitions", ())},
                "perinuclear": {item["name"]: (item["column"], item["channel_index"], item["ring_inner_um"],
                                               item["ring_outer_um"], item["configured_pixel_threshold"])
                                for item in filtering.get("perinuclear_definitions", ())},
            }
        elif result.get("edit_history") and "filter_definitions" in result["edit_history"][-1]:
            record["filter_definitions"] = result["edit_history"][-1]["filter_definitions"]
        if result.get("manual_review") is not None:
            record["previous_manual_review"] = result["manual_review"]
        edited.setflags(write=False)
        stored = dict(result, edited_masks=edited, edit_history=[*result.get("edit_history", ()), record],
                      edit_base_revision=result.get("edit_base_revision", revision),
                      edit_base_signature=result.get("edit_base_signature", record["before_signature"]),
                      edited_object_count=len(result_ids),
                      geometry_revision=revision + 1, manual_review=None)
        if state.filtering_result is not None and "settings" in state.filtering_result:
            state.active_session.filter_controls = deepcopy(state.filtering_result["settings"])
        elif state.active_session.pending_geometry_revision != revision:
            state.active_session.filter_controls = None
            state.active_session.intensity_controls = None
        state.clear_filtering_result(emit=False)
        state.scientific_roi_reviewed = False
        state.segmentation_result = stored
        display = state.active_session.display_state
        state.active_session.pending_geometry_revision = revision + 1
        state.active_session.measurement_error = None
        if "reviewed_ids" in record:
            if display.get("mode") == "selected" and display.get("selected_id") in record["reviewed_ids"]:
                display.update(selected_layer="filtered", geometry_revision=revision + 1)
            elif display.get("mode") != "custom":
                display["mode"] = "filtered"
        else:
            display["mode"] = "edited"
        state.scientific_roi_changed.emit(state.scientific_roi, False)
        state.filtering_result_changed.emit(None, False)
        state.quantification_result_changed.emit(None, False)
        state.segmentation_result_changed.emit(stored, True)

    @classmethod
    def undo(cls, state):
        edit = cls(state)
        stack = undo_stack(state.segmentation_result.get("edit_history", ()))
        if not stack:
            raise ValueError("There are no committed edits to undo.")
        record = stack[-1]
        if mask_signature(edit.base) != record["after_signature"]:
            raise ValueError("Undo geometry no longer matches this session.")
        edit.preview.flat[record["indexes"]] = record["before"]
        if mask_signature(edit.preview) != record["before_signature"]:
            raise ValueError("Undo history is incomplete.")
        edit.operation, edit.parameters = "undo", dict(revision=record["result_revision"])
        edit.commit()


def validate_history(result, session):
    history = result.get("edit_history")
    if history is None:
        if any(key in result for key in ("edit_base_revision", "edit_base_signature")):
            raise ValueError("Committed geometry is missing its edit history.")
        return
    if not isinstance(history, list) or not history or result.get("edited_masks") is None:
        raise ValueError("Incomplete committed edit history.")
    masks = effective_masks(result).copy()
    signature = mask_signature(masks)
    revision = session.geometry_revision
    required = {"operation", "session_id", "timestamp", "source_revision", "result_revision",
                "parameters", "affected_ids", "new_ids", "removed_ids", "before_signature",
                "after_signature", "indexes", "before", "after"}
    for record in history:
        if not isinstance(record, dict) or not required.issubset(record):
            raise ValueError("Incomplete geometry edit record.")
        if ("steps" in record) != (record["operation"] == "transaction"):
            raise ValueError("Inconsistent transaction history format.")
        if not isinstance(record["parameters"], dict) or any(
            not isinstance(record[key], list) or any(type(label) is not int or label <= 0 for label in record[key])
            for key in ("affected_ids", "new_ids", "removed_ids")
        ):
            raise ValueError("Invalid geometry operation parameters or IDs.")
        if any(not isinstance(record[key], np.ndarray) or record[key].dtype.kind not in "iu"
               for key in ("indexes", "before", "after")):
            raise ValueError("Invalid voxel data in edit history.")
    undo_stack(history)
    for record in reversed(history):
        if record["operation"] not in {"merge", "split", "remove", "paint/erase", "undo", "transaction"}:
            raise ValueError("Unknown geometry operation.")
        if datetime.fromisoformat(record["timestamp"]).tzinfo is None:
            raise ValueError("Geometry timestamps require a time zone.")
        if "reviewed_ids" in record:
            reviewed = record["reviewed_ids"]
            if (record.get("editing_scope") not in ("all", "filtered") or not isinstance(reviewed, list)
                    or any(type(label) is not int or label <= 0 for label in reviewed)
                    or reviewed != sorted(set(reviewed)) or not set(reviewed).issubset(np.unique(masks))):
                raise ValueError("Invalid provisional retained IDs in geometry history.")
        indexes = record["indexes"]
        if (record["session_id"] != session.session_id or record["result_revision"] != revision
                or record["source_revision"] != revision - 1 or record["after_signature"] != signature
                or indexes.ndim != 1 or indexes.dtype.kind not in "iu" or not indexes.size
                or indexes.min() < 0 or indexes.max() >= masks.size or np.any(indexes[1:] <= indexes[:-1])
                or record["before"].shape != indexes.shape or record["after"].shape != indexes.shape
                or not np.array_equal(masks.flat[indexes], record["after"])):
            raise ValueError("Incompatible geometry edit history.")
        masks.flat[indexes] = record["before"]
        previous = record.get("previous_reviewed_ids")
        if previous is not None and (not isinstance(previous, list)
                or any(type(label) is not int or label <= 0 for label in previous)
                or previous != sorted(set(previous)) or not set(previous).issubset(np.unique(masks))):
            raise ValueError("Invalid pre-edit retained IDs in geometry history.")
        if record["operation"] == "transaction":
            steps = record.get("steps")
            if not isinstance(steps, list) or len(steps) < 2:
                raise ValueError("Missing ordered geometry operations.")
            try:
                verified, expected, affected = replay_sequence(masks, steps, previous, masks.dtype)
            except (KeyError, TypeError, IndexError) as error:
                raise ValueError("Invalid saved geometry operation.") from error
            original_ids = set(np.unique(masks)) - {0}
            final_ids = set(np.unique(verified)) - {0}
            if (record["new_ids"] != sorted(final_ids - original_ids)
                    or record["removed_ids"] != sorted(original_ids - final_ids)
                    or mask_signature(verified) != record["after_signature"] or affected != record["affected_ids"]
                    or expected != record.get("reviewed_ids")
                    or not np.array_equal(np.flatnonzero(masks != verified), indexes)):
                raise ValueError("The saved operation sequence disagrees with the transaction.")
        elif previous is not None and record["operation"] != "undo":
            if record["editing_scope"] == "filtered":
                expected = sorted(set(previous).difference(record["removed_ids"]) | set(record["new_ids"]))
                if not np.isin(record["before"], [0, *previous]).all():
                    raise ValueError("Filtered history modifies an excluded object.")
            else:
                expected = reconcile_all_mask_edit(record, previous)
            if record.get("reviewed_ids") != expected:
                raise ValueError("Retained IDs disagree with the recorded geometry edit.")
        signature = mask_signature(masks)
        if signature != record["before_signature"]:
            raise ValueError("Invalid geometry edit signature.")
        revision -= 1
    if revision != result.get("edit_base_revision") or signature != result.get("edit_base_signature"):
        raise ValueError("The geometry history does not reach its initial revision.")
