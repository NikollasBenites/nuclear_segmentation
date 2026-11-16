"""Format-independent desktop import boundary; TIFF implementation for 2A.1."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import tifffile
from qtpy.QtCore import QObject, Signal, Slot

from .core import load_tiff_czyx, read_voxel_spacing_um
from .project_state import ChannelDescriptor, ImportedDataset


TIFF_SUFFIXES = (".tif", ".tiff", ".ome.tif", ".ome.tiff")
DEFAULT_COLORMAPS = ("cyan", "magenta", "green", "red", "yellow", "gray")


@dataclass(frozen=True)
class TiffInspection:
    source_path: Path
    source_format: str
    reported_axes: str
    reported_shape: tuple[int, ...]
    dtype: str
    channel_names: tuple[str, ...]
    voxel_spacing_zyx_um: tuple[float, float, float] | None
    calibration_source: str | None


def _is_tiff(path: Path) -> bool:
    return any(path.name.lower().endswith(suffix) for suffix in TIFF_SUFFIXES)


def _ome_channel_names(xml: str | None) -> tuple[str, ...]:
    if not xml:
        return ()
    root = ET.fromstring(xml)
    names = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "Channel":
            name = str(element.attrib.get("Name") or "").strip()
            names.append(name)
    return tuple(names)


def inspect_tiff(path) -> TiffInspection:
    """Read lightweight series metadata without materializing pixel data."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Image file does not exist: {source}")
    if not _is_tiff(source):
        raise ValueError("Select a TIFF, OME-TIFF, or Leica LIF image.")
    with tifffile.TiffFile(source) as tif:
        if not tif.series:
            raise ValueError("The TIFF contains no readable image series.")
        series = tif.series[0]
        ome_xml = tif.ome_metadata
        names = _ome_channel_names(ome_xml)
        source_format = "OME-TIFF" if ome_xml else "TIFF"
        reported_axes = str(series.axes)
        reported_shape = tuple(int(value) for value in series.shape)
        dtype = str(series.dtype)
    try:
        spacing, calibration_source = read_voxel_spacing_um(source)
    except ValueError:
        spacing, calibration_source = None, None
    return TiffInspection(
        source_path=source,
        source_format=source_format,
        reported_axes=reported_axes,
        reported_shape=reported_shape,
        dtype=dtype,
        channel_names=names,
        voxel_spacing_zyx_um=spacing,
        calibration_source=calibration_source,
    )


def load_tiff_dataset(
    path,
    *,
    axes_override: str | None = None,
    spacing_override=None,
    time_index: int = 0,
) -> ImportedDataset:
    """Load one TIFF series through the existing validated scientific readers."""
    inspection = inspect_tiff(path)
    if spacing_override is None:
        spacing, calibration_source = read_voxel_spacing_um(inspection.source_path)
    else:
        spacing = tuple(float(value) for value in spacing_override)
        if len(spacing) != 3 or not all(
            np.isfinite(value) and value > 0 for value in spacing
        ):
            raise ValueError(
                f"Invalid manual physical voxel spacing (Z, Y, X): {spacing}"
            )
        calibration_source = "manual override"
    data, information = load_tiff_czyx(
        inspection.source_path,
        axes_override=axes_override,
        time_index=int(time_index),
    )
    channel_count = int(data.shape[0])
    original_names = list(inspection.channel_names[:channel_count])
    original_names.extend("" for _ in range(channel_count - len(original_names)))
    channels = tuple(
        ChannelDescriptor(
            index=index,
            original_name=original_names[index],
            name=original_names[index] or f"Channel {index + 1}",
            role="segmentation" if index == 0 else "measurement",
            colormap=DEFAULT_COLORMAPS[index % len(DEFAULT_COLORMAPS)],
        )
        for index in range(channel_count)
    )
    return ImportedDataset(
        source_path=inspection.source_path,
        source_format=inspection.source_format,
        series_index=0,
        series_identifier="0",
        series_name=inspection.source_path.stem,
        reported_axes=inspection.reported_axes,
        reported_shape=inspection.reported_shape,
        standardized_shape=tuple(int(value) for value in data.shape),
        dtype=str(data.dtype),
        data_czyx=data,
        voxel_spacing_zyx_um=tuple(float(value) for value in spacing),
        calibration_source=calibration_source,
        channels=channels,
        time_index=int(time_index),
        acquisition_metadata={
            "axes_interpreted_before_reorder": information[
                "axes_interpreted_before_reorder"
            ],
            "standardized_axes": "CZYX",
        },
    )


class TiffImportWorker(QObject):
    """Read pixels outside the GUI thread; layer creation remains GUI-owned."""

    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, path, options, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.options = dict(options)

    @Slot()
    def run(self) -> None:
        try:
            dataset = load_tiff_dataset(self.path, **self.options)
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(dataset)
        finally:
            self.finished.emit()
    def __init__(self, path, options: dict, parent: QObject | None = None):
        super().__init__(parent)
        self.path = Path(path)
        self.options = dict(options)

    @Slot()
    def run(self) -> None:
        try:
            dataset = load_tiff_dataset(self.path, **self.options)
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(dataset)
        finally:
            self.finished.emit()
