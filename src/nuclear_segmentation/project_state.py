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

    @property
    def dataset_ready(self) -> bool:
        return self.dataset is not None

    def set_import_status(self, status: str) -> None:
        self.import_status = str(status)
        self.import_status_changed.emit(self.import_status)

    def set_dataset(self, dataset: ImportedDataset) -> None:
        self.dataset = dataset
        self.original_dataset = dataset
        self.working_dataset = dataset
        self.crop_provenance = None
        self.roi_mask_yx = None
        self.working_offset_yx = (0, 0)
        self.voxel_spacing_zyx_um = dataset.voxel_spacing_zyx_um
        self.calibration_source = dataset.calibration_source
        self.channels = list(dataset.channels)
        self.segmentation_channel_index = next(
            (
                channel.index
                for channel in self.channels
                if channel.role == "segmentation"
            ),
            self.channels[0].index if self.channels else None,
        )
        self.set_import_status("Dataset ready")
        self.changed.emit(dataset)
        self.channels_changed.emit(tuple(self.channels))

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
        return self.working_dataset

    def reset_crop(self) -> ImportedDataset:
        if self.original_dataset is None:
            raise ValueError("No original dataset is available.")
        self.working_dataset = self.original_dataset
        self.dataset = self.original_dataset
        self.crop_provenance = None
        self.roi_mask_yx = None
        self.working_offset_yx = (0, 0)
        return self.original_dataset

    def set_calibration(self, spacing, source: str = "manual override") -> None:
        values = tuple(float(value) for value in spacing)
        if len(values) != 3 or not all(
            np.isfinite(value) and value > 0 for value in values
        ):
            raise ValueError("Calibration must contain positive finite Z/Y/X values.")
        self.voxel_spacing_zyx_um = values
        self.calibration_source = str(source)
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
        self.channels_changed.emit(tuple(self.channels))

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
