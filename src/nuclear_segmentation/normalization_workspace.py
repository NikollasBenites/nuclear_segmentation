"""Project-owned normalization preview using the validated legacy mathematics."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import perf_counter

import numpy as np
from qtpy.QtCore import QObject, QThread, Signal, Slot

from .normalization import compare_percentiles, mask_digest, normalize_preview
from .projection_crop import generate_xy_projection, polygon_roi_mask


def validate_percentiles(lower: float, upper: float) -> tuple[float, float]:
    values = np.asarray((lower, upper), dtype=float)
    if not np.isfinite(values).all() or not 0 <= values[0] < values[1] <= 100:
        raise ValueError("Percentiles must satisfy 0 <= lower < upper <= 100.")
    return float(values[0]), float(values[1])


def normalization_roi_on_original_grid(project_state) -> np.ndarray:
    mask = project_state.normalization_roi_mask_yx
    original = project_state.original_dataset
    if mask is None or original is None:
        raise ValueError("ROI-derived normalization requires a normalization ROI.")
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != original.data_czyx.shape[-2:] or not mask.any():
        raise ValueError("The normalization ROI is empty or incompatible with the image.")
    valid = working_region_on_original_grid(project_state)
    return mask[np.newaxis, :, :] & valid


def working_region_on_original_grid(project_state) -> np.ndarray:
    original = project_state.original_dataset
    if original is None:
        raise ValueError("Load a project image before calculating normalization.")
    valid_yx = np.ones(original.data_czyx.shape[-2:], dtype=bool)
    provenance = project_state.crop_provenance
    if provenance is not None:
        valid_yx[:] = False
        y0, y1, x0, x1 = provenance.bounding_box_y0_y1_x0_x1
        if provenance.selection_type == "polygon":
            crop_mask = np.asarray(project_state.roi_mask_yx, dtype=bool)
        else:
            crop_mask = np.ones((y1 - y0, x1 - x0), dtype=bool)
        if crop_mask.shape != (y1 - y0, x1 - x0):
            raise ValueError("The crop mask is incompatible with crop provenance.")
        valid_yx[y0:y1, x0:x1] = crop_mask
    return np.broadcast_to(valid_yx, original.data_czyx.shape[1:])


def normalization_roi_on_working_grid(project_state) -> np.ndarray:
    roi = np.asarray(project_state.normalization_roi_mask_yx, dtype=bool)
    dataset = project_state.working_dataset
    if dataset is None or roi.ndim != 2:
        raise ValueError("A valid normalization ROI is required.")
    provenance = project_state.crop_provenance
    if provenance is None:
        local = roi
    else:
        y0, y1, x0, x1 = provenance.bounding_box_y0_y1_x0_x1
        local = roi[y0:y1, x0:x1]
        if provenance.selection_type == "polygon":
            local = local & np.asarray(project_state.roi_mask_yx, dtype=bool)
    if local.shape != dataset.data_czyx.shape[-2:] or not local.any():
        raise ValueError("The normalization ROI does not overlap the working image.")
    return local


# Backward-compatible internal name used by Phase 2B.2 tests and callers.
polygon_roi_on_original_grid = normalization_roi_on_original_grid


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
        project_state.normalization_roi_mask_yx is not None
    ):
        roi_digest = mask_digest(project_state.normalization_roi_mask_yx)
    crop_digest = None
    if project_state.crop_provenance is not None and project_state.roi_mask_yx is not None:
        crop_digest = mask_digest(project_state.roi_mask_yx)
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
        "crop_sha256": crop_digest,
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
        roi = normalization_roi_on_original_grid(project_state)
        comparison = compare_percentiles(original, roi, lower, upper)
        full_limits = comparison["full_lowhigh"]
    elif mode == "roi_fixed":
        raise ValueError("ROI-derived normalization requires a normalization ROI.")

    if mode == "roi_fixed":
        effective_limits = comparison["roi_lowhigh"]
    else:
        effective_limits = full_limits
    if mode == "disabled":
        preview = np.asarray(working, dtype=np.float32)
    else:
        preview = normalize_preview(working, effective_limits)
    if polygon_roi:
        local_mask = normalization_roi_on_working_grid(project_state)
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


class NormalizationRoiProjectionWorker(QObject):
    succeeded = Signal(object, int)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, volume, channel_index: int, parent=None):
        super().__init__(parent)
        self.volume = volume
        self.channel_index = int(channel_index)

    @Slot()
    def run(self):
        try:
            projection = generate_xy_projection(self.volume, "maximum")
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(projection, self.channel_index)
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
        self.roi_projection_layer = None
        self.roi_shapes_layer = None
        self._roi_event_connected = False
        self._report = None
        self._fingerprint = None
        self._thread = None
        self._worker = None
        self._roi_thread = None
        self._roi_worker = None
        panel.normalization_calculate_requested.connect(self.calculate)
        panel.normalization_apply_requested.connect(self.apply)
        panel.normalization_reset_requested.connect(self.reset)
        panel.normalization_settings_changed.connect(self.invalidate)
        panel.normalization_roi_draw_requested.connect(self.draw_roi)
        panel.normalization_roi_edit_requested.connect(self.edit_roi)
        panel.normalization_roi_clear_requested.connect(self.clear_roi)
        panel.normalization_roi_visibility_requested.connect(self.set_roi_visible)
        panel.normalization_roi_reuse_crop_requested.connect(self.reuse_crop_roi)

    @property
    def busy(self) -> bool:
        return self._thread is not None or self._roi_thread is not None

    def draw_roi(self) -> bool:
        if self.busy:
            self._status("Wait for the active normalization calculation to finish.")
            return False
        dataset = self.project_state.working_dataset
        try:
            options = self.panel.normalization_options()
        except ValueError as error:
            self._status(str(error))
            return False
        if dataset is None:
            self._status("Load a project image before drawing a normalization ROI.")
            return False
        worker = NormalizationRoiProjectionWorker(
            dataset.data_czyx[options["channel_index"]], options["channel_index"]
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._show_roi_projection)
        worker.failed.connect(self._status)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._roi_projection_finished)
        thread.finished.connect(thread.deleteLater)
        self._roi_worker = worker
        self._roi_thread = thread
        self._status("Generating normalization ROI projection…")
        thread.start()
        return True

    def _show_roi_projection(self, projection, channel_index: int) -> None:
        self._remove_roi_layers()
        if self.project_state.normalization_roi_mask_yx is not None:
            self.project_state.clear_normalization_roi()
        channel = next(
            item for item in self.project_state.channels if item.index == channel_index
        )
        spacing = tuple(self.project_state.voxel_spacing_zyx_um)
        offset_y, offset_x = self.project_state.working_offset_yx
        # Establish a 2D displayed transform before constructing a 2D Shapes
        # layer. This avoids transient 2D-vertex/3D-transform mismatches.
        self.viewer.dims.ndisplay = 2
        self.roi_projection_layer = self.viewer.add_image(
            projection,
            name=f"Normalization ROI projection · {channel.name}",
            colormap=channel.colormap,
            scale=spacing[1:],
            translate=(offset_y * spacing[1], offset_x * spacing[2]),
            units=("µm", "µm"),
            metadata={"nucytoseg_normalization_roi_projection": True},
        )
        self.roi_shapes_layer = self.viewer.add_shapes(
            name="Normalization ROI · MNTB",
            ndim=2,
            shape_type="polygon",
            edge_color="#ffd166",
            face_color="#ffd16644",
            edge_width=2,
            scale=spacing[1:],
            translate=(offset_y * spacing[1], offset_x * spacing[2]),
            metadata={
                "nucytoseg_normalization_roi": True,
                "coordinate_space": "working",
                "coordinate_offset_yx": [offset_y, offset_x],
                "coordinate_shape_yx": list(
                    self.project_state.working_dataset.data_czyx.shape[-2:]
                ),
            },
        )
        self.roi_shapes_layer.events.data.connect(self._roi_shapes_changed)
        self._roi_event_connected = True
        self.roi_shapes_layer.mode = "add_polygon"
        self.viewer.layers.selection.active = self.roi_shapes_layer
        self.panel.normalization_roi_visibility_button.setChecked(True)
        self.panel.set_normalization_roi_status(
            "Draw one polygon. The binary normalization mask is generated automatically."
        )

    def _roi_projection_finished(self) -> None:
        self._roi_thread = None
        self._roi_worker = None

    def _roi_shapes_changed(self, event=None) -> None:
        layer = self.roi_shapes_layer
        if layer is None or layer not in self.viewer.layers:
            return
        if len(layer.data) == 0:
            if self.project_state.normalization_roi_mask_yx is not None:
                self.project_state.clear_normalization_roi()
            self.panel.set_normalization_roi_status("Normalization ROI is empty.")
            return
        if len(layer.data) != 1:
            self.panel.set_normalization_roi_status(
                "Only one normalization ROI is supported. Delete extra shapes."
            )
            return
        shape_type = str(getattr(layer.shape_type[0], "value", layer.shape_type[0]))
        if shape_type.lower() != "polygon":
            self.panel.set_normalization_roi_status(
                "Normalization ROI must be a polygon."
            )
            return
        vertices = np.asarray(layer.data[0], dtype=float)
        if vertices.ndim != 2 or vertices.shape[1] != 2:
            self.panel.set_normalization_roi_status(
                "Normalization ROI vertices must contain exactly Y and X coordinates."
            )
            return
        dataset = self.project_state.working_dataset
        if dataset is None:
            return
        coordinate_space = layer.metadata.get("coordinate_space", "working")
        if coordinate_space == "original":
            height, width = self.project_state.original_dataset.data_czyx.shape[-2:]
            offset_y, offset_x = 0, 0
        else:
            height, width = layer.metadata.get(
                "coordinate_shape_yx", dataset.data_czyx.shape[-2:]
            )
            offset_y, offset_x = layer.metadata.get(
                "coordinate_offset_yx", self.project_state.working_offset_yx
            )
        if (
            np.any(vertices[:, 0] < 0)
            or np.any(vertices[:, 1] < 0)
            or np.any(vertices[:, 0] > height - 1)
            or np.any(vertices[:, 1] > width - 1)
        ):
            self.panel.set_normalization_roi_status(
                "Normalization ROI vertices must remain inside the working image."
            )
            return
        try:
            bbox, local_mask = polygon_roi_mask(vertices, (height, width))
        except ValueError as error:
            self.panel.set_normalization_roi_status(str(error))
            return
        y0, y1, x0, x1 = bbox
        original = self.project_state.original_dataset
        full_mask = np.zeros(original.data_czyx.shape[-2:], dtype=bool)
        full_mask[
            y0 + offset_y : y1 + offset_y,
            x0 + offset_x : x1 + offset_x,
        ] = local_mask
        original_vertices = vertices + np.asarray((offset_y, offset_x), dtype=float)
        spacing = tuple(self.project_state.voxel_spacing_zyx_um)
        provenance = {
            "source": str(original.source_path),
            "series_index": int(original.series_index),
            "channel_index": int(self.panel.normalization_channel_combo.currentData()),
            "vertices_original_yx": original_vertices.tolist(),
            "vertices_original_yx_um": (
                original_vertices * np.asarray(spacing[1:])
            ).tolist(),
            "working_offset_yx": [offset_y, offset_x],
            "voxel_spacing_zyx_um": list(spacing),
            "mask_sha256": mask_digest(full_mask),
            "scope": "XY polygon extended through all Z planes",
        }
        self.project_state.set_normalization_roi(
            full_mask, original_vertices, provenance
        )
        self.panel.set_normalization_roi_status(
            f"Normalization ROI ready: {int(full_mask.sum()):,} XY pixels, "
            "extended through the working Z stack."
        )

    def edit_roi(self) -> bool:
        if self.roi_shapes_layer is None or self.roi_shapes_layer not in self.viewer.layers:
            self._status("Draw or reuse a normalization ROI before editing it.")
            return False
        self.roi_shapes_layer.mode = "select"
        self.viewer.layers.selection.active = self.roi_shapes_layer
        self.panel.set_normalization_roi_status(
            "ROI edit mode active. Changes invalidate dependent normalization results."
        )
        return True

    def prepare_display_change(self, dimensions: int) -> None:
        """Stop 2D ROI editing before switching the embedded viewer to 3D."""
        if int(dimensions) != 3:
            return
        layer = self.roi_shapes_layer
        if layer is not None and layer in self.viewer.layers:
            layer.mode = "pan_zoom"
            if self.viewer.layers.selection.active is layer:
                replacement = next(
                    (
                        item for item in self.viewer.layers
                        if type(item).__name__.lower() == "image" and item.ndim >= 3
                    ),
                    None,
                )
                self.viewer.layers.selection.active = replacement

    def reuse_crop_roi(self) -> bool:
        provenance = self.project_state.crop_provenance
        crop_mask = self.project_state.roi_mask_yx
        original = self.project_state.original_dataset
        if (
            provenance is None
            or provenance.selection_type != "polygon"
            or crop_mask is None
            or original is None
        ):
            self._status("Apply a polygon crop before reusing it as normalization ROI.")
            return False
        y0, y1, x0, x1 = provenance.bounding_box_y0_y1_x0_x1
        full_mask = np.zeros(original.data_czyx.shape[-2:], dtype=bool)
        full_mask[y0:y1, x0:x1] = np.asarray(crop_mask, dtype=bool)
        vertices = np.asarray(provenance.selection_vertices_yx, dtype=float)
        roi_provenance = {
            "source": str(original.source_path),
            "series_index": int(original.series_index),
            "channel_index": int(self.panel.normalization_channel_combo.currentData()),
            "vertices_original_yx": vertices.tolist(),
            "vertices_original_yx_um": [
                list(value) for value in provenance.original_vertices_yx_um
            ],
            "working_offset_yx": [y0, x0],
            "voxel_spacing_zyx_um": list(provenance.voxel_spacing_zyx_um),
            "mask_sha256": mask_digest(full_mask),
            "scope": "Explicitly reused polygon crop; XY mask extended through Z",
        }
        self.project_state.set_normalization_roi(full_mask, vertices, roi_provenance)
        self._show_existing_roi_overlay()
        self.panel.set_normalization_roi_status(
            "Polygon crop explicitly reused as the independent normalization ROI."
        )
        return True

    def _show_existing_roi_overlay(self) -> None:
        vertices = np.asarray(self.project_state.normalization_roi_vertices_yx, dtype=float)
        if len(vertices) < 3:
            return
        self._remove_roi_layers()
        spacing = tuple(self.project_state.voxel_spacing_zyx_um)
        self.roi_shapes_layer = self.viewer.add_shapes(
            [vertices],
            name="Normalization ROI · MNTB",
            shape_type="polygon",
            edge_color="#ffd166",
            face_color="#ffd16644",
            edge_width=2,
            scale=spacing[1:],
            units=("µm", "µm"),
            metadata={
                "nucytoseg_normalization_roi": True,
                "coordinate_space": "original",
            },
        )
        self.roi_shapes_layer.events.data.connect(self._roi_shapes_changed)
        self._roi_event_connected = True

    def set_roi_visible(self, visible: bool) -> None:
        if self.roi_shapes_layer is not None and self.roi_shapes_layer in self.viewer.layers:
            self.roi_shapes_layer.visible = bool(visible)
        self.panel.normalization_roi_visibility_button.setText(
            "Hide ROI Mask" if visible else "Show ROI Mask"
        )

    def clear_roi(self) -> None:
        self._remove_roi_layers()
        self.project_state.clear_normalization_roi()
        self.panel.set_normalization_roi_status(
            "No normalization ROI. ROI-derived fixed limits require one polygon."
        )

    def _remove_roi_layers(self) -> None:
        if self.roi_shapes_layer is not None:
            if self._roi_event_connected:
                try:
                    self.roi_shapes_layer.events.data.disconnect(
                        self._roi_shapes_changed
                    )
                except (TypeError, ValueError):
                    pass
            self._roi_event_connected = False
            if self.roi_shapes_layer in self.viewer.layers:
                self.viewer.layers.remove(self.roi_shapes_layer)
            self.roi_shapes_layer = None
        if (
            self.roi_projection_layer is not None
            and self.roi_projection_layer in self.viewer.layers
        ):
            self.viewer.layers.remove(self.roi_projection_layer)
        self.roi_projection_layer = None

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
        detail = "\n".join(lines)
        self.panel.set_normalization_results(detail)
        self.panel.set_normalization_status("Normalization preview ready", preview_ready=True)
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
        if (
            self._report["mode"] == "roi_fixed"
            and self.project_state.z_correction_applied
        ):
            self._status(
                "Applied Z correction changes the model input intensities. Reset "
                "Z correction before applying ROI-fixed normalization limits."
            )
            return False
        self.project_state.apply_normalization(cellpose_configuration(self._report))
        self.panel.set_normalization_status(
            "Normalization applied — no segmentation model has run.",
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
        self.panel.set_normalization_results("No results calculated.")
        self.status_changed.emit("Normalization reset to Cellpose default")

    def remove_preview(self) -> None:
        if self.preview_layer is not None and self.preview_layer in self.viewer.layers:
            self.viewer.layers.remove(self.preview_layer)
        self.preview_layer = None

    def clear_project(self) -> None:
        self.remove_preview()
        self._remove_roi_layers()
        self._report = None
        self._fingerprint = None
        self.panel.reset_normalization_controls()
        self.panel.set_normalization_results("No results calculated.")

    def _status(self, message: str) -> None:
        self.panel.set_normalization_status(str(message), preview_ready=False)
        self.status_changed.emit(str(message))

    def dispose(self) -> None:
        self.remove_preview()
        self._remove_roi_layers()
        self.panel.normalization_calculate_requested.disconnect(self.calculate)
        self.panel.normalization_apply_requested.disconnect(self.apply)
        self.panel.normalization_reset_requested.disconnect(self.reset)
        self.panel.normalization_settings_changed.disconnect(self.invalidate)
        self.panel.normalization_roi_draw_requested.disconnect(self.draw_roi)
        self.panel.normalization_roi_edit_requested.disconnect(self.edit_roi)
        self.panel.normalization_roi_clear_requested.disconnect(self.clear_roi)
        self.panel.normalization_roi_visibility_requested.disconnect(
            self.set_roi_visible
        )
        self.panel.normalization_roi_reuse_crop_requested.disconnect(
            self.reuse_crop_roi
        )
