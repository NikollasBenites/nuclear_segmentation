"""Project-owned normalization preview using the validated legacy mathematics."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import perf_counter

import numpy as np
from qtpy.QtCore import QObject, QThread, Signal, Slot

from .normalization import compare_percentiles, mask_digest, normalize_preview


def validate_percentiles(lower: float, upper: float) -> tuple[float, float]:
    values = np.asarray((lower, upper), dtype=float)
    if not np.isfinite(values).all() or not 0 <= values[0] < values[1] <= 100:
        raise ValueError("Percentiles must satisfy 0 <= lower < upper <= 100.")
    return float(values[0]), float(values[1])


def polygon_roi_on_original_grid(project_state) -> np.ndarray:
    provenance = project_state.crop_provenance
    mask = project_state.roi_mask_yx
    original = project_state.original_dataset
    if (
        provenance is None
        or provenance.selection_type != "polygon"
        or mask is None
        or original is None
    ):
        raise ValueError("ROI-derived normalization requires an applied polygon ROI.")
    y0, y1, x0, x1 = provenance.bounding_box_y0_y1_x0_x1
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != (y1 - y0, x1 - x0) or not mask.any():
        raise ValueError("The polygon ROI mask is empty or incompatible with the crop.")
    if not (0 <= y0 < y1 <= original.data_czyx.shape[2]):
        raise ValueError("The polygon ROI Y coordinates are outside the original image.")
    if not (0 <= x0 < x1 <= original.data_czyx.shape[3]):
        raise ValueError("The polygon ROI X coordinates are outside the original image.")
    roi = np.zeros(original.data_czyx.shape[1:], dtype=bool)
    roi[:, y0:y1, x0:x1] = mask[np.newaxis, :, :]
    return roi


def normalization_fingerprint(project_state, options: dict) -> dict:
    dataset = project_state.working_dataset
    source = project_state.original_dataset
    if dataset is None or source is None:
        raise ValueError("Load a project image before calculating normalization.")
    path = Path(source.source_path).resolve()
    try:
        stat = path.stat()
        file_identity = {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        file_identity = {"path": str(path), "size": None, "mtime_ns": None}
    roi_digest = None
    if (
        project_state.crop_provenance is not None
        and project_state.crop_provenance.selection_type == "polygon"
        and project_state.roi_mask_yx is not None
    ):
        roi_digest = mask_digest(project_state.roi_mask_yx)
    channel_index = int(options["channel_index"])
    channel = next(
        (item for item in project_state.channels if item.index == channel_index), None
    )
    if channel is None:
        raise ValueError(f"Normalization channel index {channel_index} is unavailable.")
    lower, upper = validate_percentiles(options["lower"], options["upper"])
    return {
        "source": file_identity,
        "series_index": int(source.series_index),
        "series_identifier": str(source.series_identifier),
        "channel_index": channel_index,
        "channel_name": channel.name,
        "original_shape_czyx": list(source.standardized_shape),
        "working_shape_czyx": list(dataset.standardized_shape),
        "voxel_spacing_zyx_um": list(project_state.voxel_spacing_zyx_um),
        "working_offset_yx": list(project_state.working_offset_yx),
        "roi_sha256": roi_digest,
        "mode": str(options["mode"]),
        "percentiles": [lower, upper],
        "norm3D": bool(options["norm3D"]),
    }


def calculate_normalization(project_state, options: dict) -> tuple[dict, np.ndarray]:
    fingerprint = normalization_fingerprint(project_state, options)
    mode = fingerprint["mode"]
    if mode not in {"cellpose_default", "percentile", "roi_fixed", "disabled"}:
        raise ValueError(f"Unsupported normalization mode: {mode}")
    channel_index = fingerprint["channel_index"]
    lower, upper = fingerprint["percentiles"]
    original = np.asarray(project_state.original_dataset.data_czyx[channel_index])
    working = np.asarray(project_state.working_dataset.data_czyx[channel_index])
    if not np.isfinite(original).all() or not np.isfinite(working).all():
        raise ValueError("The image contains NaN or infinite values.")
    full_limits = np.percentile(original, [lower, upper]).tolist()
    polygon_roi = fingerprint["roi_sha256"] is not None
    comparison = None
    if polygon_roi:
        roi = polygon_roi_on_original_grid(project_state)
        comparison = compare_percentiles(original, roi, lower, upper)
        full_limits = comparison["full_lowhigh"]
    elif mode == "roi_fixed":
        raise ValueError("ROI-derived normalization requires an applied polygon ROI.")

    if mode == "roi_fixed":
        effective_limits = comparison["roi_lowhigh"]
    else:
        effective_limits = full_limits
    if mode == "disabled":
        preview = np.asarray(working, dtype=np.float32)
    else:
        preview = normalize_preview(working, effective_limits)
    if polygon_roi:
        local_mask = np.asarray(project_state.roi_mask_yx, dtype=bool)
        if local_mask.shape != preview.shape[-2:]:
            raise ValueError("The polygon ROI mask does not match the working image.")
        preview[:, ~local_mask] = 0.0

    full_span = float(full_limits[1] - full_limits[0])
    effective_span = float(effective_limits[1] - effective_limits[0])
    report = {
        **fingerprint,
        "full_lowhigh": [float(value) for value in full_limits],
        "roi_lowhigh": None if comparison is None else comparison["roi_lowhigh"],
        "roi_voxels": None if comparison is None else comparison["roi_voxels"],
        "outside_fraction": None if comparison is None else comparison["outside_fraction"],
        "roi_zero_fraction": None if comparison is None else comparison["roi_zero_fraction"],
        "effective_lowhigh": [float(value) for value in effective_limits],
        "relative_gain": (
            None if effective_span <= 1e-3 else float(full_span / effective_span)
        ),
        "calculation_method": "exact_numpy_percentile_linear",
        "preview_transform": "legacy normalize_preview affine mapping; no clipping",
        "polygon_scope": "XY polygon extended through every Z plane" if polygon_roi else None,
    }
    return report, preview


def cellpose_configuration(report: dict) -> dict:
    mode = report["mode"]
    if mode == "cellpose_default":
        value = True
    elif mode == "disabled":
        value = False
    elif mode == "percentile":
        value = {
            "normalize": True,
            "percentile": list(report["percentiles"]),
            "norm3D": bool(report["norm3D"]),
        }
    elif mode == "roi_fixed":
        value = {
            "normalize": True,
            "lowhigh": list(report["effective_lowhigh"]),
            "norm3D": bool(report["norm3D"]),
            "invert": False,
        }
    else:
        raise ValueError(f"Unsupported normalization mode: {mode}")
    return {
        "mode": mode,
        "cellpose_normalize": value,
        "provenance": deepcopy(report),
    }


def _options_from_report(report: dict) -> dict:
    return {
        "channel_index": report["channel_index"],
        "mode": report["mode"],
        "lower": report["percentiles"][0],
        "upper": report["percentiles"][1],
        "norm3D": report["norm3D"],
    }


class NormalizationWorker(QObject):
    succeeded = Signal(object, object, float)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, project_state, options, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self.options = dict(options)

    @Slot()
    def run(self):
        started = perf_counter()
        try:
            report, preview = calculate_normalization(self.project_state, self.options)
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(report, preview, perf_counter() - started)
        finally:
            self.finished.emit()


class NormalizationController(QObject):
    status_changed = Signal(str)

    def __init__(self, viewer, project_state, panel, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.project_state = project_state
        self.panel = panel
        self.preview_layer = None
        self._report = None
        self._fingerprint = None
        self._thread = None
        self._worker = None
        panel.normalization_calculate_requested.connect(self.calculate)
        panel.normalization_apply_requested.connect(self.apply)
        panel.normalization_reset_requested.connect(self.reset)
        panel.normalization_settings_changed.connect(self.invalidate)

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def calculate(self, options) -> bool:
        if self.busy:
            self._status("A normalization calculation is already running.")
            return False
        try:
            normalization_fingerprint(self.project_state, options)
        except ValueError as error:
            self._status(str(error))
            return False
        self.remove_preview()
        self._report = None
        self.panel.normalization_calculate_button.setEnabled(False)
        self._status("Calculating normalization statistics…")
        worker = NormalizationWorker(self.project_state, options)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._show_preview)
        worker.failed.connect(self._calculation_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._calculation_finished)
        thread.finished.connect(thread.deleteLater)
        self._worker = worker
        self._thread = thread
        thread.start()
        return True

    def _show_preview(self, report, preview, elapsed: float) -> None:
        try:
            current = normalization_fingerprint(
                self.project_state, _options_from_report(report)
            )
        except ValueError as error:
            self._calculation_failed(str(error))
            return
        if current != {key: report[key] for key in current}:
            self._calculation_failed(
                "Normalization inputs changed while the preview was being calculated."
            )
            return
        channel = next(
            item for item in self.project_state.channels
            if item.index == report["channel_index"]
        )
        spacing = tuple(self.project_state.voxel_spacing_zyx_um)
        offset_y, offset_x = self.project_state.working_offset_yx
        self.preview_layer = self.viewer.add_image(
            preview,
            name=f"Normalization preview · {channel.name}",
            colormap=channel.colormap,
            contrast_limits=(0.0, 1.0) if report["mode"] != "disabled" else None,
            scale=spacing,
            translate=(0.0, offset_y * spacing[1], offset_x * spacing[2]),
            units=("µm", "µm", "µm"),
            metadata={
                "nucytoseg_normalization_preview": True,
                "channel_index": channel.index,
            },
        )
        self._report = deepcopy(report)
        self._fingerprint = current
        self.project_state.set_normalization_preview(report)
        full_low, full_high = report["full_lowhigh"]
        lines = [
            "Normalization Preview",
            f"Source channel: {report['channel_name']}",
            f"Mode: {report['mode'].replace('_', ' ')}",
            f"Percentiles: {report['percentiles'][0]:g} → {report['percentiles'][1]:g}",
            f"Original full-volume limits: {full_low:.6g} → {full_high:.6g}",
        ]
        if report["roi_lowhigh"] is not None:
            roi_low, roi_high = report["roi_lowhigh"]
            lines.extend(
                (
                    f"Polygon ROI limits: {roi_low:.6g} → {roi_high:.6g}",
                    f"ROI voxels: {report['roi_voxels']:,}",
                    f"Outside ROI: {100 * report['outside_fraction']:.2f}%",
                    f"Genuine zeros inside ROI: {100 * report['roi_zero_fraction']:.2f}%",
                )
            )
        gain = report["relative_gain"]
        lines.append(
            "Relative normalization gain: undefined"
            if gain is None
            else f"Relative normalization gain: {gain:.6g}×"
        )
        lines.append(f"Status: Preview ready ({elapsed:.2f} s)")
        self.panel.set_normalization_status("\n".join(lines), preview_ready=True)
        self.status_changed.emit("Normalization preview ready")

    def _calculation_failed(self, message: str) -> None:
        self.remove_preview()
        self._report = None
        self._fingerprint = None
        self._status(f"Normalization preview failed: {message}")

    def _calculation_finished(self) -> None:
        self._thread = None
        self._worker = None
        self.panel.normalization_calculate_button.setEnabled(
            self.project_state.dataset_ready
        )

    def apply(self) -> bool:
        if self._report is None or self._fingerprint is None:
            self._status(
                "Normalization settings changed. Recalculate the preview before applying."
            )
            return False
        try:
            options = self.panel.normalization_options()
            current = normalization_fingerprint(self.project_state, options)
        except ValueError as error:
            self._status(str(error))
            return False
        if current != self._fingerprint:
            self.invalidate()
            return False
        self.project_state.apply_normalization(cellpose_configuration(self._report))
        self.panel.set_normalization_status(
            self.panel.normalization_status.text()
            + "\nApplied configuration is current. No segmentation model has run.",
            preview_ready=True,
        )
        self.status_changed.emit("Normalization configuration applied")
        return True

    def invalidate(self, *args) -> None:
        had_state = self._report is not None or self.project_state.normalization_is_current
        self.remove_preview()
        self._report = None
        self._fingerprint = None
        self.project_state.invalidate_normalization()
        if had_state and self.project_state.dataset_ready:
            self._status(
                "Normalization settings changed. Recalculate the preview before applying."
            )

    def reset(self) -> None:
        self.remove_preview()
        self._report = None
        self._fingerprint = None
        self.project_state.reset_normalization()
        self.panel.reset_normalization_controls()
        self.status_changed.emit("Normalization reset to Cellpose default")

    def remove_preview(self) -> None:
        if self.preview_layer is not None and self.preview_layer in self.viewer.layers:
            self.viewer.layers.remove(self.preview_layer)
        self.preview_layer = None

    def clear_project(self) -> None:
        self.remove_preview()
        self._report = None
        self._fingerprint = None
        self.panel.reset_normalization_controls()

    def _status(self, message: str) -> None:
        self.panel.set_normalization_status(str(message), preview_ready=False)
        self.status_changed.emit(str(message))

    def dispose(self) -> None:
        self.remove_preview()
        self.panel.normalization_calculate_requested.disconnect(self.calculate)
        self.panel.normalization_apply_requested.disconnect(self.apply)
        self.panel.normalization_reset_requested.disconnect(self.reset)
        self.panel.normalization_settings_changed.disconnect(self.invalidate)
