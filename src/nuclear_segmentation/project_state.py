"""Desktop project state, separate from workspace and analysis persistence."""
from __future__ import annotations

from dataclasses import dataclass, field
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

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.dataset: ImportedDataset | None = None
        self.analysis_mode = "segment"
        self.import_status = "No image loaded"

    @property
    def dataset_ready(self) -> bool:
        return self.dataset is not None

    def set_import_status(self, status: str) -> None:
        self.import_status = str(status)
        self.import_status_changed.emit(self.import_status)

    def set_dataset(self, dataset: ImportedDataset) -> None:
        self.dataset = dataset
        self.set_import_status("Dataset ready")
        self.changed.emit(dataset)
