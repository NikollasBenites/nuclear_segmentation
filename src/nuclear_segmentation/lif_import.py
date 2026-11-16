"""Optional BioIO-backed Leica LIF series discovery and loading."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import numpy as np
from qtpy.QtCore import QObject, Signal, Slot

from .image_import import DEFAULT_COLORMAPS
from .project_state import ChannelDescriptor, ImportedDataset


LOGGER = logging.getLogger("nuclear_segmentation.lif_import")


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


def _metadata_root(image):
    try:
        root = image.metadata
        return root.getroot() if hasattr(root, "getroot") else root
    except (AttributeError, TypeError, ValueError):
        return None


def _selected_scene_xml(image, identifier: str):
    """Find one exact Leica Element path; never fall back to another scene."""
    root = _metadata_root(image)
    if root is None:
        return None
    parts = tuple(part for part in str(identifier).replace("\\", "/").split("/") if part)
    if not parts:
        return None
    parents = {child: parent for parent in root.iter() for child in parent}
    matches = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "Element":
            continue
        if element.attrib.get("Name") != parts[-1]:
            continue
        names = []
        current = element
        while current is not None:
            if current.tag.rsplit("}", 1)[-1] == "Element":
                name = current.attrib.get("Name")
                if name:
                    names.append(name)
            current = parents.get(current)
        names.reverse()
        if len(names) >= len(parts) and tuple(names[-len(parts) :]) == parts:
            matches.append(element)
    return matches[0] if len(matches) == 1 else None


def _xml_optional_metadata(image, identifier: str) -> dict[str, Any]:
    scene = _selected_scene_xml(image, identifier)
    if scene is None:
        return {}
    result: dict[str, Any] = {}
    for element in scene.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        for key, raw_value in element.attrib.items():
            normalized = key.lower().replace("_", "")
            value = str(raw_value).strip()
            if not value:
                continue
            if "objective" in tag and normalized in {
                "magnification",
                "nominalmagnification",
                "mag",
            }:
                try:
                    result.setdefault("objective_magnification", float(value))
                except ValueError:
                    pass
            if normalized in {
                "acquisitiondate",
                "acquisitiondatetime",
                "imagingdatetime",
                "datetime",
            }:
                result.setdefault("acquisition_date", value)
    return result


def _diagnostic(callback, stage: str, message: str, started=None) -> None:
    elapsed = None if started is None else perf_counter() - started
    suffix = "" if elapsed is None else f" elapsed={elapsed:.3f}s"
    text = f"stage={stage} {message}{suffix}"
    LOGGER.info(text)
    if callback is not None:
        callback(stage, message, elapsed)
    return None


def _leica_channel_metadata(image, identifier: str) -> list[dict[str, str]]:
    """Preserve available Leica channel attributes without interpreting LUT names."""
    try:
        root = _selected_scene_xml(image, identifier)
        elements = [] if root is None else list(root.findall(".//ChannelDescription"))
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
    path, *, bioimage_factory: Callable | None = None, diagnostic=None
) -> tuple[LifSeriesDescriptor, ...]:
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".lif":
        raise ValueError("Leica series enumeration requires a .lif file.")
    factory = bioimage_factory or _default_bioimage_factory
    started = perf_counter()
    image = factory(source)
    _diagnostic(diagnostic, "file_opened", f"file={source.name}", started)
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
            optional = _xml_optional_metadata(image, str(identifier))
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
                    objective_magnification=optional.get("objective_magnification"),
                    acquisition_date=optional.get("acquisition_date"),
                    supported=supported,
                    limitation=limitation,
                )
            )
    finally:
        close = getattr(image, "close", None)
        if callable(close):
            close()
    result = tuple(descriptors)
    _diagnostic(
        diagnostic,
        "series_enumerated",
        f"file={source.name} count={len(result)}",
        started,
    )
    return result


def load_lif_dataset(
    path,
    series_index: int,
    *,
    time_index: int = 0,
    spacing_override=None,
    bioimage_factory: Callable | None = None,
    diagnostic=None,
) -> ImportedDataset:
    source = Path(path).expanduser().resolve()
    factory = bioimage_factory or _default_bioimage_factory
    started = perf_counter()
    image = factory(source)
    _diagnostic(diagnostic, "file_opened", f"file={source.name}", started)
    try:
        scenes = tuple(image.scenes)
        if not 0 <= int(series_index) < len(scenes):
            raise ValueError(f"LIF series index {series_index} is unavailable.")
        image.set_scene(int(series_index))
        identifier = str(scenes[int(series_index)])
        _diagnostic(
            diagnostic,
            "series_selected",
            f"file={source.name} index={series_index} identifier={identifier!r}",
        )
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
        _diagnostic(
            diagnostic,
            "dimensions_validated",
            f"axes={image.dims.order} shape={tuple(int(value) for value in image.shape)}",
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
        _diagnostic(
            diagnostic,
            "calibration_validated",
            f"spacing_zyx_um={spacing} source={calibration_source!r}",
        )
        pixel_started = perf_counter()
        _diagnostic(
            diagnostic,
            "pixel_loading_started",
            f"file={source.name} series={series_index} identifier={identifier!r}",
        )
        lazy = image.get_image_dask_data("CZYX", T=int(time_index))
        data = np.asarray(lazy.compute())
        _diagnostic(
            diagnostic,
            "pixel_loading_completed",
            f"shape={data.shape} dtype={data.dtype}",
            pixel_started,
        )
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
        metadata: dict[str, Any] = {
            "reader": "BioIO bioio-lif",
            "standardized_axes": "CZYX",
            "bioio_dimensions": str(image.dims.order),
        }
        leica_channels = _leica_channel_metadata(image, identifier)
        if leica_channels:
            metadata["leica_channel_metadata"] = leica_channels
        optional = _xml_optional_metadata(image, identifier)
        metadata.update(optional)
        result = ImportedDataset(
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
        _diagnostic(
            diagnostic,
            "dataset_validated",
            f"file={source.name} series={series_index} CZYX={result.standardized_shape}",
            started,
        )
        return result
    finally:
        close = getattr(image, "close", None)
        if callable(close):
            close()


class LifEnumerationWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()
    diagnostic = Signal(str)

    def __init__(self, path, bioimage_factory=None, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.bioimage_factory = bioimage_factory

    @Slot()
    def run(self):
        current_stage = "opening_file"

        def report(stage, message, elapsed):
            nonlocal current_stage
            current_stage = stage
            suffix = "" if elapsed is None else f" ({elapsed:.2f} s)"
            self.diagnostic.emit(f"{stage}: {message}{suffix}")

        try:
            result = enumerate_lif_series(
                self.path,
                bioimage_factory=self.bioimage_factory,
                diagnostic=report,
            )
        except Exception as error:
            LOGGER.exception(
                "LIF enumeration failed file=%s stage=%s exception=%s",
                self.path.name,
                current_stage,
                type(error).__name__,
            )
            self.failed.emit(
                f"Could not read Leica series from {self.path.name} during "
                f"{current_stage}: {type(error).__name__}: {error}"
            )
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class LifImportWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()
    diagnostic = Signal(str)

    def __init__(self, path, series_index, options, bioimage_factory=None, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.series_index = int(series_index)
        self.options = dict(options)
        self.bioimage_factory = bioimage_factory

    @Slot()
    def run(self):
        current_stage = "opening_file"

        def report(stage, message, elapsed):
            nonlocal current_stage
            current_stage = stage
            suffix = "" if elapsed is None else f" ({elapsed:.2f} s)"
            self.diagnostic.emit(f"{stage}: {message}{suffix}")

        try:
            result = load_lif_dataset(
                self.path,
                self.series_index,
                bioimage_factory=self.bioimage_factory,
                diagnostic=report,
                **self.options,
            )
        except Exception as error:
            LOGGER.exception(
                "LIF import failed file=%s series_index=%s stage=%s exception=%s",
                self.path.name,
                self.series_index,
                current_stage,
                type(error).__name__,
            )
            self.failed.emit(
                f"Could not load {self.path.name}, series {self.series_index}, during "
                f"{current_stage}: {type(error).__name__}: {error}"
            )
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()
