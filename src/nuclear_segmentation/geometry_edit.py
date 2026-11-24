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
    kept = set(previous)
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
        if record["new_ids"] or np.any(np.isin(before[transferred], previous)
                                       != np.isin(after[transferred], previous)):
            return None
    return sorted(kept.difference(record["removed_ids"]))


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
    def __init__(self, state, *, filtered=False):
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
        self._excluded_ids = None
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

    def has_changes(self):
        source = self._visible_planes() if self.filtered else self.base
        return any(not np.array_equal(a, b) for a, b in zip(self.preview, source, strict=True))

    def is_pristine(self):
        return self.operation == "paint/erase" and not self.has_changes()

    @property
    def excluded_ids(self):
        if self._excluded_ids is None:
            ids = set()
            for plane in self.base:
                ids.update(map(int, np.unique(plane)))
            self._excluded_ids = tuple(ids.difference(self.retained_ids, {0}))
        return self._excluded_ids

    def _visible_planes(self):
        if self.filter_record is not None:
            yield from self.filter_record["filtered_masks"]
        else:
            for plane in self.base:
                yield np.where(np.isin(plane, self.retained_ids), plane, 0)

    def allows_label(self, label):
        return not self.filtered or label not in self.excluded_ids

    def _validate_scope(self):
        if not self.filter_current:
            raise ValueError("Filtering changed. Discard the preview and start again.")
        visible = self._visible_planes()
        for base, kept, preview in zip(self.base, visible, self.preview, strict=True):
            hidden = base != kept
            expected = kept if self.filtered else base
            changed = preview != expected
            if np.any(changed & hidden) or np.isin(preview[changed], self.excluded_ids).any():
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
        self._restore_or_hide(self.preview, filtered)
        self.filtered = filtered

    def _restore_or_hide(self, target, hide):
        for base, kept, plane in zip(self.base, self._visible_planes(), target, strict=True):
            hidden = base != kept
            plane[hidden] = 0 if hide else base[hidden]

    def _object(self, label):
        if label <= 0 or not self.allows_label(label) or not np.any(self.base == label):
            raise ValueError(f"Object {label} is absent from this session.")

    def _pristine(self):
        if self.paused or not self.is_pristine():
            raise ValueError("Save or discard the current preview before starting another operation.")

    def remove(self, label):
        self._pristine()
        self._object(label)
        self.preview[self.base == label] = 0
        self.operation, self.parameters = "remove", {"id": label}

    def merge(self, ids, target):
        self._pristine()
        ids = sorted(set(ids))
        if len(ids) < 2 or target not in ids:
            raise ValueError("Choose at least two objects and one of their IDs to retain.")
        counts = {}
        for label in ids:
            self._object(label)
            counts[str(label)] = int(np.count_nonzero(self.base == label))
        self.preview[np.isin(self.base, ids)] = target
        self.operation = "merge"
        self.parameters = dict(ids=ids, retained_id=target, input_counts=counts,
                               result_count=sum(counts.values()))

    def split(self, parent):
        self._pristine()
        self._object(parent)
        new = self.next_id
        if new > np.iinfo(self.preview.dtype).max:
            raise ValueError("No unused higher label ID is available.")
        self.operation, self.parameters = "split", dict(parent=parent, new_id=new)
        return new

    def commit(self):
        state = self.state
        if self.owner != state.session_revision() or not state.segmentation_is_current:
            raise ValueError("The project, session, or scientific inputs changed.")
        if self.preview.shape != self.base.shape or self.preview.dtype.kind not in "iu" or self.preview.min() < 0:
            raise ValueError("Edited geometry must contain nonnegative labels on the original ZYX grid.")
        if self.filtered:
            self._validate_scope()
        edited = self.preview.copy()
        if self.filtered:
            self._restore_or_hide(edited, False)
        indexes = np.flatnonzero(self.base != edited)
        if not indexes.size:
            raise ValueError("The preview contains no changes.")
        before, after = self.base.flat[indexes], edited.flat[indexes]
        if self.operation == "split":
            parent, new = self.parameters["parent"], self.parameters["new_id"]
            if np.any(before != parent) or np.any(after != new) or not np.any(edited == parent):
                raise ValueError("Paint only part of the parent with the proposed new ID.")
        if self.operation == "remove":
            label = self.parameters["id"]
            if np.any(before != label) or np.any(after) or np.any(edited == label):
                raise ValueError("Removal preview must remove only the entire selected object.")
        if self.operation == "merge":
            ids, target = self.parameters["ids"], self.parameters["retained_id"]
            sources = [label for label in ids if label != target]
            if (not np.isin(before, sources).all() or np.any(after != target)
                    or np.isin(edited, sources).any()):
                raise ValueError("Merge preview must relabel only the complete source objects.")
        result = state.segmentation_result
        revision = self.owner.geometry_revision
        source_ids = set(map(int, np.unique(self.base))) - {0}
        result_ids = set(map(int, np.unique(edited))) - {0}
        record = dict(session_id=self.owner.session_id, source_revision=revision,
                      result_revision=revision + 1, operation=self.operation, parameters=self.parameters,
                      timestamp=datetime.now(timezone.utc).isoformat(),
                      new_ids=sorted(result_ids - source_ids), removed_ids=sorted(source_ids - result_ids),
                      affected_ids=sorted(set(map(int, np.union1d(before, after))) - {0}),
                      before_signature=mask_signature(self.base), after_signature=mask_signature(edited),
                      indexes=indexes, before=before, after=after)
        record["editing_scope"] = "filtered" if self.filtered else "all"
        if self.filtered:
            # Record the accepted visible labels before hidden geometry is restored
            # into the authoritative result. This includes split children and merge targets.
            visible_ids = set()
            for plane in self.preview:
                visible_ids.update(map(int, np.unique(plane)))
            record["reviewed_ids"] = sorted(visible_ids - {0})
        if self.retained_ids is not None:
            record["previous_reviewed_ids"] = sorted(map(int, self.retained_ids))
        if self.operation == "undo":
            restored = previous_reviewed_ids(undo_stack(result["edit_history"])[-1])
            if restored is not None:
                record["reviewed_ids"] = restored
        elif not self.filtered and self.retained_ids is not None:
            reconciled = reconcile_all_mask_edit(record, record["previous_reviewed_ids"])
            if reconciled is not None:
                record["reviewed_ids"] = reconciled
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
        if record["operation"] not in {"merge", "split", "remove", "paint/erase", "undo"}:
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
        if previous is not None and record["operation"] != "undo":
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
