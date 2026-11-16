"""Optional BioIO-backed Leica LIF series discovery and loading."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from qtpy.QtCore import QObject, Signal, Slot

from .image_import import DEFAULT_COLORMAPS
from .project_state import ChannelDescriptor, ImportedDataset


class LifDependencyError(ImportError):
    pass


@dataclass(frozen=True)
class LifSeriesDescriptor:
    index: int
    identifier: str
    name: str
    dimensions: str
    shape: tuple[int, ...]
    channel_count: int
    z_planes: int
    time_points: int
    voxel_spacing_zyx_um: tuple[float, float, float] | None
    objective_magnification: float | None = None
    acquisition_date: str | None = None
    supported: bool = True
    limitation: str | None = None


def _default_bioimage_factory(path):
    try:
        from bioio import BioImage
        from bioio_lif import Reader
    except ImportError as error:
        raise LifDependencyError(
            "Leica LIF support is optional. Install it with "
            "python -m pip install -e '.[lif]'."
        ) from error
    return BioImage(path, reader=Reader, reconstruct_mosaic=False)


def _dimension_size(image, name: str) -> int:
    return int(getattr(image.dims, name, 1))


def _spacing(image) -> tuple[float, float, float] | None:
    try:
        sizes = image.physical_pixel_sizes
        result = tuple(float(getattr(sizes, axis)) for axis in "ZYX")
    except (AttributeError, TypeError, ValueError):
        return None
    if not all(np.isfinite(value) and value > 0 for value in result):
        return None
    return result


def _standard_metadata_value(image, name: str):
    try:
        value = getattr(image.standard_metadata, name)
    except (AttributeError, TypeError, ValueError):
        return None
    return value


def _objective_magnification(image) -> float | None:
    objective = _standard_metadata_value(image, "objective")
    for name in ("nominal_magnification", "magnification"):
        value = getattr(objective, name, None)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _leica_channel_metadata(image) -> list[dict[str, str]]:
    """Preserve available Leica channel attributes without interpreting LUT names."""
    try:
        root = image.metadata
        if hasattr(root, "getroot"):
            root = root.getroot()
        elements = list(root.findall(".//ChannelDescription"))
    except (AttributeError, TypeError, ValueError):
        return []
    result = []
    for index, element in enumerate(elements):
        record = {"channel_index": str(index)}
        for key, value in element.attrib.items():
            normalized = key.lower()
            if any(
                token in normalized
                for token in (
                    "lut",
                    "name",
                    "wavelength",
                    "excitation",
                    "emission",
                    "channel",
                )
            ):
                record[str(key)] = str(value)
        result.append(record)
    return result


def enumerate_lif_series(
    path, *, bioimage_factory: Callable | None = None
) -> tuple[LifSeriesDescriptor, ...]:
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".lif":
        raise ValueError("Leica series enumeration requires a .lif file.")
    factory = bioimage_factory or _default_bioimage_factory
    image = factory(source)
    descriptors = []
    try:
        scenes = tuple(image.scenes)
        for index, identifier in enumerate(scenes):
            image.set_scene(index)
            order = str(image.dims.order)
            shape = tuple(int(value) for value in image.shape)
            mosaic_tiles = _dimension_size(image, "M")
            samples = _dimension_size(image, "S")
            supported = mosaic_tiles <= 1 and samples <= 1
            limitation = None
            if mosaic_tiles > 1:
                limitation = (
                    "Unstitched mosaic tiles are not loaded automatically because "
                    "their spatial interpretation requires validation."
                )
            elif samples > 1:
                limitation = "Multi-sample pixel data is not supported in Phase 2A."
            acquisition = _standard_metadata_value(image, "imaging_datetime")
            descriptors.append(
                LifSeriesDescriptor(
                    index=index,
                    identifier=str(identifier),
                    name=str(identifier),
                    dimensions=order,
                    shape=shape,
                    channel_count=_dimension_size(image, "C"),
                    z_planes=_dimension_size(image, "Z"),
                    time_points=_dimension_size(image, "T"),
                    voxel_spacing_zyx_um=_spacing(image),
                    objective_magnification=_objective_magnification(image),
                    acquisition_date=str(acquisition) if acquisition else None,
                    supported=supported,
                    limitation=limitation,
                )
            )
    finally:
        close = getattr(image, "close", None)
        if callable(close):
            close()
    return tuple(descriptors)


def load_lif_dataset(
    path,
    series_index: int,
    *,
    time_index: int = 0,
    spacing_override=None,
    bioimage_factory: Callable | None = None,
) -> ImportedDataset:
    source = Path(path).expanduser().resolve()
    factory = bioimage_factory or _default_bioimage_factory
    image = factory(source)
    try:
        scenes = tuple(image.scenes)
        if not 0 <= int(series_index) < len(scenes):
            raise ValueError(f"LIF series index {series_index} is unavailable.")
        image.set_scene(int(series_index))
        if _dimension_size(image, "M") > 1:
            raise ValueError(
                "This series contains unstitched mosaic tiles. Automatic stitching "
                "is disabled until its spatial interpretation is validated."
            )
        if _dimension_size(image, "S") > 1:
            raise ValueError("Multi-sample LIF pixels are not supported in Phase 2A.")
        time_points = _dimension_size(image, "T")
        if not 0 <= int(time_index) < time_points:
            raise ValueError(
                f"Time index {time_index} is outside 0..{time_points - 1}."
            )
        if spacing_override is None:
            spacing = _spacing(image)
            calibration_source = "Leica metadata"
            if spacing is None:
                raise ValueError(
                    "Complete Leica Z/Y/X calibration is unavailable. Enter a "
                    "manual calibration before loading this series."
                )
        else:
            spacing = tuple(float(value) for value in spacing_override)
            if len(spacing) != 3 or not all(
                np.isfinite(value) and value > 0 for value in spacing
            ):
                raise ValueError(f"Invalid manual Z/Y/X calibration: {spacing}")
            calibration_source = "manual override"
        lazy = image.get_image_dask_data("CZYX", T=int(time_index))
        data = np.asarray(lazy.compute())
        if data.ndim != 4:
            raise ValueError(
                f"BioIO returned unsupported dimensions {data.shape}; expected CZYX."
            )
        raw_names = tuple(str(value) for value in (image.channel_names or ()))
        channels = tuple(
            ChannelDescriptor(
                index=index,
                original_name=raw_names[index] if index < len(raw_names) else "",
                name=(raw_names[index] if index < len(raw_names) else "")
                or f"Channel {index + 1}",
                role="segmentation" if index == 0 else "measurement",
                colormap=DEFAULT_COLORMAPS[index % len(DEFAULT_COLORMAPS)],
                visible=index == 0,
            )
            for index in range(data.shape[0])
        )
        identifier = str(scenes[int(series_index)])
        metadata: dict[str, Any] = {
            "reader": "BioIO bioio-lif",
            "standardized_axes": "CZYX",
            "bioio_dimensions": str(image.dims.order),
        }
        leica_channels = _leica_channel_metadata(image)
        if leica_channels:
            metadata["leica_channel_metadata"] = leica_channels
        objective = _objective_magnification(image)
        acquisition = _standard_metadata_value(image, "imaging_datetime")
        if objective is not None:
            metadata["objective_magnification"] = objective
        if acquisition is not None:
            metadata["acquisition_date"] = str(acquisition)
        return ImportedDataset(
            source_path=source,
            source_format="Leica LIF",
            series_index=int(series_index),
            series_identifier=identifier,
            series_name=identifier,
            reported_axes=str(image.dims.order),
            reported_shape=tuple(int(value) for value in image.shape),
            standardized_shape=tuple(int(value) for value in data.shape),
            dtype=str(data.dtype),
            data_czyx=data,
            voxel_spacing_zyx_um=spacing,
            calibration_source=calibration_source,
            channels=channels,
            time_index=int(time_index),
            acquisition_metadata=metadata,
        )
    finally:
        close = getattr(image, "close", None)
        if callable(close):
            close()


class LifEnumerationWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, path, bioimage_factory=None, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.bioimage_factory = bioimage_factory

    @Slot()
    def run(self):
        try:
            result = enumerate_lif_series(
                self.path, bioimage_factory=self.bioimage_factory
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class LifImportWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, path, series_index, options, bioimage_factory=None, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.series_index = int(series_index)
        self.options = dict(options)
        self.bioimage_factory = bioimage_factory

    @Slot()
    def run(self):
        try:
            result = load_lif_dataset(
                self.path,
                self.series_index,
                bioimage_factory=self.bioimage_factory,
                **self.options,
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()
