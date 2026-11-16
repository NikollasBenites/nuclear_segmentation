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


class ProjectState(QObject):
    """Own imported-dataset identity without changing analysis schemas."""

    changed = Signal(object)
    import_status_changed = Signal(str)
    channels_changed = Signal(object)
    calibration_changed = Signal(object, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.dataset: ImportedDataset | None = None
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
