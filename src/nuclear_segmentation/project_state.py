"""Desktop project state, separate from workspace and analysis persistence."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from qtpy.QtCore import QObject, Signal


@dataclass(frozen=True)
class ChannelDescriptor:
    index: int
    original_name: str
    name: str
    role: str = "measurement"
    colormap: str = "gray"
    visible: bool = True


@dataclass(frozen=True)
class ImportedDataset:
    source_path: Path
    source_format: str
    series_index: int
    series_identifier: str
    series_name: str
    reported_axes: str
    reported_shape: tuple[int, ...]
    standardized_shape: tuple[int, int, int, int]
    dtype: str
    data_czyx: np.ndarray
    voxel_spacing_zyx_um: tuple[float, float, float]
    calibration_source: str
    channels: tuple[ChannelDescriptor, ...]
    time_index: int = 0
    acquisition_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_filename(self) -> str:
        return self.source_path.name


@dataclass(frozen=True)
class CropProvenance:
    original_shape_czyx: tuple[int, int, int, int]
    cropped_shape_czyx: tuple[int, int, int, int]
    bounding_box_y0_y1_x0_x1: tuple[int, int, int, int]
    selection_vertices_yx: tuple[tuple[float, float], ...]
    voxel_spacing_zyx_um: tuple[float, float, float]
    spatial_offset_yx_um: tuple[float, float]
    selection_type: str
    cropped_vertices_yx: tuple[tuple[float, float], ...]
    original_vertices_yx_um: tuple[tuple[float, float], ...]
    roi_mask_shape_yx: tuple[int, int]
    roi_pixel_count: int
    roi_area_um2: float


class ProjectState(QObject):
    """Own imported-dataset identity without changing analysis schemas."""

    changed = Signal(object)
    import_status_changed = Signal(str)
    channels_changed = Signal(object)
    calibration_changed = Signal(object, str)
    normalization_changed = Signal(object, bool)
    normalization_roi_changed = Signal(object)
    z_correction_changed = Signal(object)
    segmentation_result_changed = Signal(object, bool)
    filtering_result_changed = Signal(object, bool)
    perinuclear_markers_changed = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.dataset: ImportedDataset | None = None
        self.original_dataset: ImportedDataset | None = None
        self.working_dataset: ImportedDataset | None = None
        self.crop_provenance: CropProvenance | None = None
        self.roi_mask_yx: np.ndarray | None = None
        self.working_offset_yx = (0, 0)
        self.analysis_mode = "segment"
        self.import_status = "No image loaded"
        self.voxel_spacing_zyx_um = None
        self.calibration_source = None
        self.channels: list[ChannelDescriptor] = []
        self.segmentation_channel_index: int | None = None
        self.normalization_configuration: dict[str, Any] | None = None
        self.normalization_preview: dict[str, Any] | None = None
        self.normalization_is_current = False
        self.normalization_roi_mask_yx: np.ndarray | None = None
        self.normalization_roi_vertices_yx: tuple[tuple[float, float], ...] = ()
        self.normalization_roi_provenance: dict[str, Any] | None = None
        self.z_correction_options: dict[int, dict[str, Any]] = {}
        self.z_correction_reports: dict[int, dict[str, Any]] = {}
        self.z_correction_applied: set[int] = set()
        self.segmentation_result: dict[str, Any] | None = None
        self.segmentation_is_current = False
        self.filtering_result: dict[str, Any] | None = None
        self.filtering_is_current = False
        self.perinuclear_markers: list[dict[str, Any]] = []

    @property
    def dataset_ready(self) -> bool:
        return self.dataset is not None

    def set_import_status(self, status: str) -> None:
        self.import_status = str(status)
        self.import_status_changed.emit(self.import_status)

    def set_dataset(self, dataset: ImportedDataset) -> None:
        self.clear_segmentation_result()
        self.dataset = dataset
        self.original_dataset = dataset
        self.working_dataset = dataset
        self.crop_provenance = None
        self.roi_mask_yx = None
        self.working_offset_yx = (0, 0)
        self.voxel_spacing_zyx_um = dataset.voxel_spacing_zyx_um
        self.calibration_source = dataset.calibration_source
        self.channels = list(dataset.channels)
        from .config import defaults

        channel_indexes = {channel.name: int(channel.index) for channel in self.channels}
        self.perinuclear_markers = [
            {
                "channel_index": channel_indexes[item["name"]],
                "enabled": bool(item.get("enabled", True)),
                "ring_inner_um": float(item.get("ring_inner_um", 0.5)),
                "ring_outer_um": float(item.get("ring_outer_um", 3.0)),
                "pixel_threshold": item.get("pixel_threshold"),
                "initial_min_positive_fraction": float(
                    item.get("initial_min_positive_fraction", 0.0)
                ),
            }
            for item in defaults()["PERINUCLEAR_MARKERS"]
            if item["name"] in channel_indexes
        ]
        self.segmentation_channel_index = next(
            (
                channel.index
                for channel in self.channels
                if channel.role == "segmentation"
            ),
            self.channels[0].index if self.channels else None,
        )
        self.reset_normalization(emit=False)
        self.clear_normalization_roi(emit=False)
        self.reset_all_z_corrections(emit=False)
        self.set_import_status("Dataset ready")
        self.changed.emit(dataset)
        self.channels_changed.emit(tuple(self.channels))
        self.perinuclear_markers_changed.emit(
            tuple(dict(item) for item in self.perinuclear_markers)
        )

    def apply_crop(
        self,
        bounding_box,
        selection_vertices,
        roi_mask_yx=None,
        selection_type="rectangle",
    ) -> ImportedDataset:
        from .projection_crop import crop_czyx, masked_crop_czyx

        if self.original_dataset is None:
            raise ValueError("Load a dataset before applying a crop.")
        y0, y1, x0, x1 = (int(value) for value in bounding_box)
        rectangular = crop_czyx(self.original_dataset.data_czyx, (y0, y1, x0, x1))
        if roi_mask_yx is None:
            roi_mask = np.ones(rectangular.shape[-2:], dtype=bool)
            cropped = rectangular
        else:
            roi_mask = np.asarray(roi_mask_yx, dtype=bool)
            cropped = masked_crop_czyx(
                self.original_dataset.data_czyx,
                (y0, y1, x0, x1),
                roi_mask,
            )
        spacing = tuple(self.voxel_spacing_zyx_um)
        vertices = tuple(
            (float(vertex[0]), float(vertex[1])) for vertex in selection_vertices
        )
        cropped_vertices = tuple((y - y0, x - x0) for y, x in vertices)
        physical_vertices = tuple(
            (y * spacing[1], x * spacing[2]) for y, x in vertices
        )
        pixel_count = int(np.count_nonzero(roi_mask))
        provenance = CropProvenance(
            original_shape_czyx=self.original_dataset.standardized_shape,
            cropped_shape_czyx=tuple(int(value) for value in cropped.shape),
            bounding_box_y0_y1_x0_x1=(y0, y1, x0, x1),
            selection_vertices_yx=vertices,
            voxel_spacing_zyx_um=spacing,
            spatial_offset_yx_um=(y0 * spacing[1], x0 * spacing[2]),
            selection_type=str(selection_type),
            cropped_vertices_yx=cropped_vertices,
            original_vertices_yx_um=physical_vertices,
            roi_mask_shape_yx=tuple(int(value) for value in roi_mask.shape),
            roi_pixel_count=pixel_count,
            roi_area_um2=pixel_count * spacing[1] * spacing[2],
        )
        metadata = dict(self.original_dataset.acquisition_metadata)
        metadata["crop"] = {
            "bounding_box_y0_y1_x0_x1": list(provenance.bounding_box_y0_y1_x0_x1),
            "selection_vertices_yx": [list(vertex) for vertex in provenance.selection_vertices_yx],
            "original_shape_czyx": list(provenance.original_shape_czyx),
            "cropped_shape_czyx": list(provenance.cropped_shape_czyx),
            "spatial_offset_yx_um": list(provenance.spatial_offset_yx_um),
            "selection_type": provenance.selection_type,
            "cropped_vertices_yx": [list(vertex) for vertex in cropped_vertices],
            "original_vertices_yx_um": [list(vertex) for vertex in physical_vertices],
            "roi_mask_shape_yx": list(provenance.roi_mask_shape_yx),
            "roi_pixel_count": provenance.roi_pixel_count,
            "roi_area_um2": provenance.roi_area_um2,
        }
        self.working_dataset = replace(
            self.original_dataset,
            data_czyx=cropped,
            standardized_shape=provenance.cropped_shape_czyx,
            voxel_spacing_zyx_um=spacing,
            calibration_source=str(self.calibration_source),
            acquisition_metadata=metadata,
        )
        self.dataset = self.working_dataset
        self.working_offset_yx = (y0, x0)
        self.crop_provenance = provenance
        self.roi_mask_yx = np.array(roi_mask, copy=True)
        self.invalidate_segmentation()
        self.invalidate_normalization()
        self.invalidate_z_corrections()
        return self.working_dataset

    def reset_crop(self) -> ImportedDataset:
        if self.original_dataset is None:
            raise ValueError("No original dataset is available.")
        self.working_dataset = self.original_dataset
        self.dataset = self.original_dataset
        self.crop_provenance = None
        self.roi_mask_yx = None
        self.working_offset_yx = (0, 0)
        self.invalidate_segmentation()
        self.invalidate_normalization()
        self.invalidate_z_corrections()
        return self.original_dataset

    def set_normalization_preview(self, report: dict[str, Any]) -> None:
        self.normalization_preview = dict(report)
        self.normalization_is_current = False
        self.normalization_changed.emit(self.normalization_preview, False)

    def apply_normalization(self, configuration: dict[str, Any]) -> None:
        self.normalization_configuration = dict(configuration)
        self.normalization_is_current = True
        self.invalidate_segmentation()
        self.normalization_changed.emit(self.normalization_configuration, True)

    def invalidate_normalization(self) -> None:
        if self.normalization_preview is None and self.normalization_configuration is None:
            return
        self.normalization_preview = None
        self.normalization_is_current = False
        self.normalization_changed.emit(self.normalization_configuration, False)

    def reset_normalization(self, *, emit: bool = True) -> None:
        self.normalization_configuration = {
            "mode": "cellpose_default",
            "cellpose_normalize": True,
        }
        self.normalization_preview = None
        self.normalization_is_current = False
        self.invalidate_segmentation()
        if emit:
            self.normalization_changed.emit(self.normalization_configuration, False)

    def set_normalization_roi(
        self, mask_yx, vertices_yx, provenance: dict[str, Any]
    ) -> None:
        if self.original_dataset is None:
            raise ValueError("Load a dataset before defining a normalization ROI.")
        mask = np.asarray(mask_yx, dtype=bool)
        expected = self.original_dataset.data_czyx.shape[-2:]
        if mask.shape != expected or not mask.any():
            raise ValueError(
                f"Normalization ROI mask must be nonempty with shape {expected}."
            )
        self.normalization_roi_mask_yx = np.array(mask, copy=True)
        self.normalization_roi_vertices_yx = tuple(
            (float(y), float(x)) for y, x in vertices_yx
        )
        self.normalization_roi_provenance = dict(provenance)
        self.invalidate_normalization()
        self.normalization_roi_changed.emit(self.normalization_roi_provenance)

    def clear_normalization_roi(self, *, emit: bool = True) -> None:
        self.normalization_roi_mask_yx = None
        self.normalization_roi_vertices_yx = ()
        self.normalization_roi_provenance = None
        self.invalidate_normalization()
        if emit:
            self.normalization_roi_changed.emit(None)

    def set_z_correction_preview(
        self, channel_index: int, options: dict[str, Any], report: dict[str, Any]
    ) -> None:
        index = int(channel_index)
        self.z_correction_options[index] = dict(options)
        self.z_correction_reports[index] = dict(report)
        self.z_correction_applied.discard(index)
        self.invalidate_segmentation()
        self.z_correction_changed.emit(index)

    def apply_z_corrections(self, channel_indexes) -> None:
        indexes = {int(index) for index in channel_indexes}
        if not indexes or not indexes.issubset(self.z_correction_reports):
            raise ValueError("Calculate current Z-correction previews before applying.")
        self.z_correction_applied.update(indexes)
        self.invalidate_segmentation()
        self.z_correction_changed.emit(tuple(sorted(indexes)))

    def reset_z_corrections(self, channel_indexes, *, emit: bool = True) -> None:
        for index in tuple(int(value) for value in channel_indexes):
            self.z_correction_options.pop(index, None)
            self.z_correction_reports.pop(index, None)
            self.z_correction_applied.discard(index)
        self.invalidate_segmentation()
        if emit:
            self.z_correction_changed.emit(None)

    def reset_all_z_corrections(self, *, emit: bool = True) -> None:
        self.z_correction_options = {}
        self.z_correction_reports = {}
        self.z_correction_applied = set()
        self.invalidate_segmentation()
        if emit:
            self.z_correction_changed.emit(None)

    def invalidate_z_corrections(self) -> None:
        if not self.z_correction_reports and not self.z_correction_applied:
            return
        self.z_correction_reports = {}
        self.z_correction_applied = set()
        self.invalidate_segmentation()
        self.z_correction_changed.emit(None)

    def invalidate_z_correction(self, channel_index: int) -> None:
        index = int(channel_index)
        self.z_correction_reports.pop(index, None)
        self.z_correction_applied.discard(index)
        self.invalidate_segmentation()
        self.z_correction_changed.emit(index)

    def legacy_z_corrections(self) -> dict[str, dict[str, Any]]:
        """Resolve stable channel indexes to the legacy name-keyed contract."""
        records = {}
        for index in sorted(self.z_correction_applied):
            channel = next((item for item in self.channels if item.index == index), None)
            if channel is not None and index in self.z_correction_options:
                records[channel.name] = dict(self.z_correction_options[index])
        return records

    def clear_project(self) -> None:
        self.clear_segmentation_result(emit=False)
        self.dataset = None
        self.original_dataset = None
        self.working_dataset = None
        self.crop_provenance = None
        self.roi_mask_yx = None
        self.working_offset_yx = (0, 0)
        self.voxel_spacing_zyx_um = None
        self.calibration_source = None
        self.channels = []
        self.perinuclear_markers = []
        self.segmentation_channel_index = None
        self.normalization_configuration = None
        self.normalization_preview = None
        self.normalization_is_current = False
        self.normalization_roi_mask_yx = None
        self.normalization_roi_vertices_yx = ()
        self.normalization_roi_provenance = None
        self.reset_all_z_corrections(emit=False)
        self.set_import_status("No image loaded")
        self.channels_changed.emit(())
        self.perinuclear_markers_changed.emit(())
        self.normalization_changed.emit(None, False)
        self.normalization_roi_changed.emit(None)
        self.z_correction_changed.emit(None)
        self.segmentation_result_changed.emit(None, False)
        self.changed.emit(None)

    def set_calibration(self, spacing, source: str = "manual override") -> None:
        values = tuple(float(value) for value in spacing)
        if len(values) != 3 or not all(
            np.isfinite(value) and value > 0 for value in values
        ):
            raise ValueError("Calibration must contain positive finite Z/Y/X values.")
        self.voxel_spacing_zyx_um = values
        self.calibration_source = str(source)
        self.invalidate_segmentation()
        self.calibration_changed.emit(values, self.calibration_source)

    def update_channel(self, index: int, **changes) -> None:
        position = next(
            (i for i, channel in enumerate(self.channels) if channel.index == index),
            None,
        )
        if position is None:
            raise ValueError(f"Unknown channel index: {index}")
        current = self.channels[position]
        if "name" in changes:
            from .core import safe_key

            name = str(changes["name"]).strip()
            if not name:
                raise ValueError("Channel names cannot be empty.")
            if any(
                channel.index != index and channel.name.casefold() == name.casefold()
                for channel in self.channels
            ):
                raise ValueError(f"Channel name {name!r} is already in use.")
            key = safe_key(name).casefold()
            if any(
                channel.index != index
                and safe_key(channel.name).casefold() == key
                for channel in self.channels
            ):
                raise ValueError(
                    "Channel names must remain unique after conversion to "
                    "analysis-safe names."
                )
            changes["name"] = name
        if "role" in changes:
            role = str(changes["role"])
            if role not in {"segmentation", "measurement", "reference"}:
                raise ValueError(f"Unsupported channel role: {role}")
            if role == "segmentation":
                for i, channel in enumerate(self.channels):
                    if channel.index != index and channel.role == "segmentation":
                        self.channels[i] = replace(channel, role="measurement")
                self.segmentation_channel_index = index
            elif self.segmentation_channel_index == index:
                raise ValueError(
                    "Assign another segmentation channel before changing this role."
                )
        if "colormap" in changes and not str(changes["colormap"]).strip():
            raise ValueError("A channel colormap is required.")
        self.channels[position] = replace(current, **changes)
        if {"name", "role"}.intersection(changes):
            self.invalidate_segmentation()
        self.invalidate_normalization()
        self.channels_changed.emit(tuple(self.channels))

    def set_segmentation_result(self, result: dict[str, Any]) -> None:
        self.clear_filtering_result()
        masks = np.asarray(result.get("masks"))
        if masks.ndim != 3 or not masks.size or int(masks.max()) == 0:
            raise ValueError("Segmentation results require a nonempty ZYX label mask.")
        masks.setflags(write=False)
        stored = dict(result)
        stored["masks"] = masks
        self.segmentation_result = stored
        self.segmentation_is_current = True
        self.segmentation_result_changed.emit(stored, True)

    def invalidate_segmentation(self) -> None:
        if self.segmentation_result is None or not self.segmentation_is_current:
            return
        self.segmentation_is_current = False
        self.invalidate_filtering()
        self.segmentation_result_changed.emit(self.segmentation_result, False)

    def clear_segmentation_result(self, *, emit: bool = True) -> None:
        self.clear_filtering_result(emit=emit)
        self.segmentation_result = None
        self.segmentation_is_current = False
        if emit:
            self.segmentation_result_changed.emit(None, False)

    def set_filtering_result(self, result: dict[str, Any]) -> None:
        stored = dict(result)
        filtered = np.asarray(stored["filtered_masks"])
        filtered.setflags(write=False)
        stored["filtered_masks"] = filtered
        self.filtering_result = stored
        self.filtering_is_current = True
        self.filtering_result_changed.emit(stored, True)

    def invalidate_filtering(self) -> None:
        if self.filtering_result is None or not self.filtering_is_current:
            return
        self.filtering_is_current = False
        self.filtering_result_changed.emit(self.filtering_result, False)

    def clear_filtering_result(self, *, emit: bool = True) -> None:
        self.filtering_result = None
        self.filtering_is_current = False
        if emit:
            self.filtering_result_changed.emit(None, False)

    @property
    def segmentation_channel(self) -> str | None:
        return next(
            (
                channel.name
                for channel in self.channels
                if channel.index == self.segmentation_channel_index
            ),
            None,
        )

    def analysis_channel_records(self) -> list[dict]:
        """Return legacy-compatible CHANNELS records without persisting them."""
        return [
            {
                "index": channel.index,
                "name": channel.name,
                "role": channel.role,
                "colormap": channel.colormap,
                "measure": True,
                "filter_enabled": True,
                "filter_min": None,
                "filter_max": None,
            }
            for channel in self.channels
        ]

    def set_perinuclear_markers(self, markers) -> bool:
        records = []
        seen = set()
        available = {int(channel.index) for channel in self.channels}
        for marker in markers:
            index = int(marker["channel_index"])
            if index not in available:
                raise ValueError(f"Unknown perinuclear marker channel index: {index}")
            if index in seen:
                raise ValueError("A channel can be configured as a perinuclear marker only once.")
            seen.add(index)
            inner = float(marker.get("ring_inner_um", 0.5))
            outer = float(marker.get("ring_outer_um", 3.0))
            threshold = marker.get("pixel_threshold")
            threshold = None if threshold is None else float(threshold)
            fraction = float(marker.get("initial_min_positive_fraction", 0.0))
            if not np.isfinite(inner) or not np.isfinite(outer) or not 0 <= inner < outer:
                raise ValueError("Perinuclear distances must satisfy 0 <= inner < outer.")
            if threshold is not None and not np.isfinite(threshold):
                raise ValueError("Manual perinuclear pixel thresholds must be finite.")
            if not np.isfinite(fraction) or not 0 <= fraction <= 1:
                raise ValueError("Perinuclear positive fraction must be between 0 and 1.")
            records.append({
                "channel_index": index,
                "enabled": bool(marker.get("enabled", True)),
                "ring_inner_um": inner,
                "ring_outer_um": outer,
                "pixel_threshold": threshold,
                "initial_min_positive_fraction": fraction,
            })
        measurement_keys = (
            "channel_index", "enabled", "ring_inner_um", "ring_outer_um",
            "pixel_threshold",
        )
        previous_measurement = [
            tuple(item.get(key) for key in measurement_keys)
            for item in self.perinuclear_markers
        ]
        current_measurement = [
            tuple(item.get(key) for key in measurement_keys) for item in records
        ]
        measurement_changed = previous_measurement != current_measurement
        self.perinuclear_markers = records
        if measurement_changed:
            self.invalidate_filtering()
        self.perinuclear_markers_changed.emit(tuple(dict(item) for item in records))
        return measurement_changed

    def legacy_perinuclear_markers(self) -> list[dict[str, Any]]:
        channels = {int(channel.index): channel.name for channel in self.channels}
        return [
            {
                "name": channels[item["channel_index"]],
                "enabled": item["enabled"],
                "ring_inner_um": item["ring_inner_um"],
                "ring_outer_um": item["ring_outer_um"],
                "pixel_threshold": item["pixel_threshold"],
                "initial_min_positive_fraction": item["initial_min_positive_fraction"],
            }
            for item in self.perinuclear_markers
            if item["channel_index"] in channels
        ]
