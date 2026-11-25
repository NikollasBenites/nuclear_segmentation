"""Validated, project-owned inputs for integrated nuclear–cytoplasmic matching."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from weakref import ref

import numpy as np

from .association_review import AssociationReview
from .automatic_matching import MatchingSettings, OverlapStatistics
from .geometry_edit import effective_masks
from .scientific_input import same_input_except_z_corrections

if TYPE_CHECKING:
    from .project_state import ProjectState, SessionRevision


MATCHING_ROLES = ("nuclear", "cytoplasmic")
MATCHING_LAYER = "nucytoseg_matching_input"


@dataclass(frozen=True)
class MatchingGrid:
    source_path: str
    source_sha256: str | None
    series_index: int
    series_identifier: str
    time_index: int
    reported_axes: str
    axes_override: str | None
    working_shape_czyx: tuple[int, int, int, int]
    spacing_zyx_um: tuple[float, float, float]
    offset_yx: tuple[int, int]
    crop_type: str | None
    crop_bounds_y0_y1_x0_x1: tuple[int, int, int, int] | None
    crop_vertices_yx: tuple[tuple[float, float], ...]
    axes: str = "ZYX"

    @property
    def shape_zyx(self):
        return self.working_shape_czyx[1:]

    @property
    def translation_zyx_um(self):
        y, x = self.offset_yx
        return (0.0, y * self.spacing_zyx_um[1], x * self.spacing_zyx_um[2])

    def agrees_with(self, provenance, channel_name):
        # Saved desktop provenance uses standardized CZYX and has no per-session
        # orientation transform. File verification and series/time import are project-owned.
        expected = {
            "source_path": self.source_path,
            "source_series": self.series_index,
            "working_shape_czyx": self.working_shape_czyx,
            "voxel_spacing_zyx_um": self.spacing_zyx_um,
            "working_offset_yx": self.offset_yx,
            "crop_type": self.crop_type,
            "segmentation_channel": channel_name,
        }
        return isinstance(provenance, dict) and all(
            key in provenance and provenance[key] == value for key, value in expected.items()
        )


@dataclass(frozen=True)
class MatchingInput:
    owner: SessionRevision
    measurement_revision: int
    source_channel_index: int
    retained_ids: tuple[int, ...]
    labels: np.ndarray = field(repr=False, compare=False)

    @property
    def population_identity(self):
        return self.owner, self.source_channel_index, self.retained_ids


@dataclass(frozen=True)
class MatchingPairSnapshot:
    grid: MatchingGrid
    nuclear: MatchingInput
    cytoplasmic: MatchingInput

    @property
    def measurement_revisions(self):
        return self.nuclear.measurement_revision, self.cytoplasmic.measurement_revision

    @property
    def population_identity(self):
        return self.grid, self.nuclear.population_identity, self.cytoplasmic.population_identity


@dataclass(frozen=True, eq=False)
class MatchingJob:
    snapshot: MatchingPairSnapshot
    settings: MatchingSettings
    cache: OverlapStatistics | None


@dataclass(frozen=True)
class InputCheck:
    message: str
    input: MatchingInput | None = None


def _validate_labels(effective, filtered, retained, shape):
    for volume in (effective, filtered):
        if (not isinstance(volume, np.ndarray) or volume.shape != shape
                or volume.dtype.kind not in "iu" or volume.flags.writeable):
            raise ValueError("Expected read-only integer ZYX labels on the working grid. Recalculate the inputs.")
    if effective.dtype != filtered.dtype or (retained and retained[-1] > np.iinfo(effective.dtype).max):
        raise ValueError("Retained labels do not match the committed label type. Recalculate measurements.")
    wanted = np.asarray(retained, dtype=effective.dtype)
    found = set()
    # Bound temporary allocations by validating one Z plane at a time.
    for raw, kept in zip(effective, filtered, strict=True):
        if raw.dtype.kind == "i" and raw.min() < 0:
            raise ValueError("Committed geometry has negative labels. Rerun segmentation.")
        if not np.array_equal(kept, np.where(np.isin(raw, wanted), raw, 0)):
            raise ValueError("Retained masks differ from committed geometry. Recalculate measurements.")
        found.update(map(int, np.unique(kept)))
    found.discard(0)
    if found != set(retained):
        raise ValueError("Retained IDs differ from the label volume. Recalculate measurements.")


class MatchingPairContext:
    """Keep session identities and capture only current retained inputs."""

    def __init__(self, state: ProjectState):
        self._state = state
        self.review = AssociationReview(self)
        self._project_id = state.session_revision().project_id
        self._ids = dict.fromkeys(MATCHING_ROLES)
        self._verified = {}
        self.pending_settings = MatchingSettings()
        self.job = None
        self._result = None
        self.executed_settings = None
        self.attempted = False
        self.error = None

    def discard_results(self):
        self.job = None
        self._result = None
        self.error = None

    def session_id(self, role):
        return self._ids[role]

    def _owns_project(self):
        return (self is self._state.matching_pair
                and self._project_id == self._state.session_revision().project_id)

    def select(self, role, session_id):
        if not self._owns_project():
            raise ValueError("The matching project has changed.")
        if role not in MATCHING_ROLES:
            raise ValueError("Unknown matching role.")
        if session_id is not None:
            session = self._state.sessions.get(session_id)
            if session is None or session.scientific_role != role:
                raise ValueError(f"Select a {role} session from the current project.")
        if self._ids[role] != session_id:
            self.discard_results()
            self._ids[role] = session_id
            self._verified.pop(role, None)
            self._state.matching_selection_changed.emit()

    def grid(self):
        state = self._state
        source, working = state.original_dataset, state.working_dataset
        if source is None or working is None:
            return None
        spacing = tuple(state.voxel_spacing_zyx_um or ())
        shape = tuple(working.standardized_shape)
        offset = tuple(state.working_offset_yx)
        crop = state.crop_provenance
        bounds = crop.bounding_box_y0_y1_x0_x1 if crop else None
        if (len(spacing) != 3 or not all(np.isfinite(v) and v > 0 for v in spacing)
                or shape != working.data_czyx.shape or len(shape) != 4 or min(shape) <= 0
                or len(offset) != 2 or min(offset) < 0
                or offset != ((bounds[0], bounds[2]) if bounds else (0, 0))
                or (crop and (shape != crop.cropped_shape_czyx
                              or source.standardized_shape != crop.original_shape_czyx
                              or shape[-2:] != (bounds[1] - bounds[0], bounds[3] - bounds[2])))):
            return None
        source_fields = (
            "source_path", "source_sha256", "series_index", "series_identifier",
            "time_index", "reported_axes", "axes_override",
        )
        if any(getattr(source, key) != getattr(working, key) for key in source_fields):
            return None
        return MatchingGrid(
            str(source.source_path), source.source_sha256, source.series_index, source.series_identifier,
            source.time_index, source.reported_axes, source.axes_override,
            shape, spacing, offset, crop.selection_type if crop else None, bounds,
            crop.selection_vertices_yx if crop else (),
        )

    def inspect(self):
        if not self._owns_project():
            return None, {role: InputCheck("The matching project has changed.") for role in MATCHING_ROLES}
        grid = self.grid()
        checks = {role: self._check(role, grid) for role in MATCHING_ROLES}
        nuclear, cytoplasmic = (checks[role].input for role in MATCHING_ROLES)
        if grid is not None and nuclear is not None and cytoplasmic is not None:
            return MatchingPairSnapshot(grid, nuclear, cytoplasmic), checks
        return None, checks

    def capture(self):
        """Capture immutable metadata and references to the published read-only labels."""
        snapshot, _ = self.inspect()
        return snapshot

    def is_current(self, snapshot: MatchingPairSnapshot) -> bool:
        return snapshot is not None and self.capture() == snapshot

    def set_settings(self, settings: MatchingSettings | None):
        if self.pending_settings != settings:
            self.pending_settings = settings
            self.job = self.error = None
            self._state.matching_result_changed.emit()

    def reconcile(self):
        """Observe population changes even when the Matching workspace is hidden."""
        if self.job is None and self._result is None:
            return
        snapshot, checks = self.inspect()
        if self.job is not None and self.job.snapshot != snapshot:
            self.job = None
        if self._result is None:
            return
        grid, *inputs = self._result.statistics.population_identity
        if not self._owns_project() or grid != self.grid():
            self._result = None
            return
        for role, identity in zip(MATCHING_ROLES, inputs, strict=True):
            owner, _, _ = identity
            check = checks[role].input
            if (owner.session_id != self.session_id(role) or owner.session_id not in self._state.sessions
                    or owner != self._state.session_revision(owner.session_id)
                    or (check is not None and check.population_identity != identity)):
                self._result = None
                return

    @property
    def current_result(self):
        return self._result_for(self.capture())

    def _result_for(self, snapshot):
        result = self._result
        if (snapshot is not None and result is not None and self.job is None and self.error is None
                and result.settings == self.pending_settings
                and result.statistics.population_identity == snapshot.population_identity):
            return result
        return None

    def begin(self):
        if self.pending_settings is None:
            raise ValueError("Enter valid overlap fractions before running matching.")
        snapshot = self.capture()
        if snapshot is None or self.job is not None:
            raise ValueError("Select two compatible, measured populations before running matching.")
        cache = self._result.statistics if self._result is not None else None
        if cache is not None and cache.population_identity != snapshot.population_identity:
            cache = self._result = None
        self.job = MatchingJob(snapshot, self.pending_settings, cache)
        self.attempted = True
        self.error = None
        self._state.matching_result_changed.emit()
        return self.job

    def _finish_job(self, job):
        if self.job is not job:
            return False
        self.job = None
        return self.pending_settings == job.settings and self.is_current(job.snapshot)

    def publish(self, job, result):
        if not self._finish_job(job):
            return False
        if (result.statistics.population_identity != job.snapshot.population_identity
                or result.settings != job.settings
                or result.measurement_revisions != job.snapshot.measurement_revisions):
            return False
        self._result = result
        self.executed_settings = result.settings
        self._state.matching_result_changed.emit()
        return True

    def fail(self, job, message):
        if self._finish_job(job):
            self.error = message
            self._state.matching_result_changed.emit()

    def _check(self, role, grid):
        state = self._state
        session = state.sessions.get(self.session_id(role))
        if session is None or session.scientific_role != role:
            return InputCheck(f"Select a {role} session from the current project.")
        if grid is None:
            return InputCheck("Load a calibrated image or restore a compatible working grid.")
        segmentation = session.segmentation_result
        if segmentation is None:
            return InputCheck("Run segmentation for this session.")
        if not session.segmentation_is_current:
            return InputCheck(session.outdated_segmentation_status)
        filtering = session.filtering_result
        if (session.pending_geometry_revision is not None or not session.filtering_is_current
                or not session.roi_grid_compatible or not filtering
                or filtering.get("incompatible_settings")):
            if session.pending_geometry_revision is not None:
                reason = "Committed geometry has changed."
            elif session.measurement_invalidation_reason:
                reason = f"Measurements are outdated ({session.measurement_invalidation_reason})."
            elif filtering and filtering.get("incompatible_settings"):
                return InputCheck("Review the incompatible filter settings in Mask Review.")
            elif filtering:
                reason = "Measurements are outdated."
            else:
                reason = "Validated filtering is unavailable."
            return InputCheck(f"{reason} Recalculate measurements in Mask Review.")
        owner = state.session_revision(session.session_id)
        if (filtering.get("session_id") != owner.session_id
                or filtering.get("geometry_revision") != owner.geometry_revision
                or any(key in segmentation and segmentation[key] != expected
                       for key, expected in (("session_id", owner.session_id), ("scientific_role", role),
                                             ("geometry_revision", owner.geometry_revision)))):
            return InputCheck("Filtering or segmentation belongs to a different revision. Recalculate measurements.")

        index = session.source_channel_index
        if index is None:
            index = state.segmentation_channel_index
        channel = next((c for c in state.channels if c.index == index), None)
        provenance = segmentation.get("input_provenance")
        measurement = filtering.get("measurement_provenance")
        if (channel is None or not grid.agrees_with(provenance, channel.name)
                or not grid.agrees_with(measurement, channel.name)):
            return InputCheck("Acquisition or calibrated grid is incompatible. Rerun segmentation and measurements on the current image.")
        if (filtering.get("input_segmentation_provenance") != provenance
                or not same_input_except_z_corrections(provenance, measurement)
                or filtering.get("raw_mask_shape") != grid.shape_zyx):
            return InputCheck("Measurements were calculated from different inputs. Recalculate measurements.")

        retained = filtering.get("retained_ids")
        if (not isinstance(retained, (tuple, list))
                or any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, np.integer))
                       or v <= 0 for v in retained)
                or len(set(retained)) != len(retained)
                or filtering.get("retained_count") != len(retained)):
            return InputCheck("Retained IDs are invalid. Recalculate measurements.")
        ids = tuple(sorted(map(int, retained)))
        try:
            effective = effective_masks(segmentation)
            labels = filtering["filtered_masks"]
            cached = self._verified.get(role)
            if (cached is None or cached[0]() is not effective or cached[1]() is not labels
                    or cached[2:] != (ids, grid.shape_zyx)
                    or effective.flags.writeable or labels.flags.writeable):
                _validate_labels(effective, labels, ids, grid.shape_zyx)
                self._verified[role] = (ref(effective), ref(labels), ids, grid.shape_zyx)
        except (KeyError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) else "Label geometry is missing. Recalculate measurements."
            return InputCheck(reason)
        message = f"{len(ids):,} validated retained objects." if ids else "Validated retained population is empty."
        return InputCheck(message, MatchingInput(owner, session.measurement_revision, index, ids, labels.view()))
