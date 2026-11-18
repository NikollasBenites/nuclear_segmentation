"""Qt worker boundary for the authoritative legacy Cellpose stage."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from time import perf_counter
import traceback

import numpy as np
from qtpy.QtCore import QObject, QThread, QTimer, Signal, Slot

from .runtime import execute_stage
from .scientific_input import prepare_scientific_input, validate_scientific_input


def execute_cellpose_stage(scientific_input, progress=print):
    """Inject lazy dependencies and execute the unchanged legacy segment stage."""
    progress("Initializing PyTorch and Cellpose backend…")
    try:
        import torch
        from cellpose import io, models
    except ImportError as error:
        raise RuntimeError(
            "Cellpose execution requires PyTorch and Cellpose in this environment."
        ) from error

    namespace = dict(scientific_input.namespace)
    namespace.update(
        torch=torch,
        io=io,
        models=models,
        np=np,
        perf_counter=perf_counter,
        print=lambda *args, **kwargs: progress(" ".join(str(item) for item in args)),
    )
    progress("Loading model and running the existing Cellpose segmentation stage…")
    execute_stage(namespace, "segment")
    progress("Validating segmentation results and finalizing application state…")
    masks = np.asarray(namespace["masks"])
    masks.setflags(write=False)
    result = {
        "masks": masks,
        "mask_shape": tuple(int(value) for value in masks.shape),
        "object_count": int(len(namespace["detected_labels"])),
        "maximum_label_id": int(masks.max()),
        "segmentation_channel": namespace["SEGMENTATION_CHANNEL"],
        "model_name": namespace["MODEL_NAME"],
        "device": str(namespace["DEVICE"]),
        "device_type": str(namespace["DEVICE"].type),
        "parameters": deepcopy(namespace["eval_parameters"]),
        "input_provenance": deepcopy(scientific_input.provenance),
        "z_corrections": deepcopy(namespace["Z_CORRECTIONS"]),
        "cellpose_normalize": deepcopy(namespace["CELLPOSE_NORMALIZE"]),
        "model_load_seconds": namespace["CELLPOSE_MODEL_LOAD_SECONDS"],
        "evaluation_seconds": namespace["CELLPOSE_EVAL_SECONDS"],
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_3d_probs": namespace.get("raw_3d_probs"),
    }
    for key in ("model", "flows", "styles"):
        namespace.pop(key, None)
    return result


class SegmentationWorker(QObject):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, project_state, config, runner=execute_cellpose_stage):
        super().__init__()
        self.project_state = project_state
        self.config = deepcopy(config)
        self.runner = runner

    @Slot()
    def run(self):
        try:
            self.progress.emit("Validating and preparing the in-memory scientific input…")
            scientific_input = prepare_scientific_input(
                self.project_state, self.config
            )
            result = self.runner(scientific_input, self.progress.emit)
        except Exception:
            self.failed.emit(traceback.format_exc())
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class SegmentationController(QObject):
    status_changed = Signal(str)

    def __init__(
        self, viewer, project_state, panel, conflict_widgets=(),
        conflict_operations=(), parent=None, runner=execute_cellpose_stage,
    ):
        super().__init__(parent)
        self.viewer = viewer
        self.project_state = project_state
        self.panel = panel
        self.conflict_widgets = tuple(conflict_widgets)
        self.conflict_operations = tuple(conflict_operations)
        self.runner = runner
        self._thread = None
        self._worker = None
        self._result_layer = None
        self._started = None
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._update_elapsed)
        self._availability_timer = QTimer(self)
        self._availability_timer.setInterval(250)
        self._availability_timer.timeout.connect(self._update_availability)
        self._availability_timer.start()
        self._conflict_enabled = ()
        panel.run_requested.connect(self.run)
        panel.configuration_changed.connect(self.project_state.invalidate_segmentation)
        project_state.segmentation_result_changed.connect(self._sync_result_layer)

    @property
    def busy(self) -> bool:
        return self._thread is not None

    @property
    def result_layer(self):
        layer = self._result_layer
        return layer if layer is not None and layer in self.viewer.layers else None

    def run(self) -> bool:
        if self.busy:
            self._status("Cellpose segmentation is already running.")
            return False
        self._update_availability()
        if any(operation.busy for operation in self.conflict_operations):
            self._status("Wait for the active project operation before segmentation.")
            return False
        try:
            config = self.panel.scientific_config()
            validate_scientific_input(self.project_state, config)
        except (TypeError, ValueError) as error:
            self._status(f"Segmentation input is not ready: {error}")
            return False

        worker = SegmentationWorker(self.project_state, config, self.runner)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._progress)
        worker.succeeded.connect(self._succeeded)
        worker.failed.connect(self._failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._worker = worker
        self._thread = thread
        self._started = perf_counter()
        self.panel.execution_log.clear()
        self.panel.set_execution_running(True)
        self._conflict_enabled = tuple(
            widget.isEnabled() for widget in self.conflict_widgets
        )
        for widget in self.conflict_widgets:
            widget.setEnabled(False)
        self._timer.start()
        self._status(
            f"Starting {config['MODEL_NAME']} on {config['PROCESSING_DEVICE']} "
            f"({'3D' if config['CELLPOSE_DO_3D'] else '2D + stitching'})…"
        )
        thread.start()
        return True

    def _progress(self, message: str) -> None:
        self.panel.set_execution_status(message)
        self.status_changed.emit(str(message))

    def _succeeded(self, result) -> None:
        try:
            expected = tuple(self.project_state.working_dataset.data_czyx.shape[1:])
            if tuple(result.get("mask_shape", ())) != expected:
                raise ValueError(
                    f"Segmentation mask shape {result.get('mask_shape')} does not "
                    f"match {expected}."
                )
            self.project_state.set_segmentation_result(result)
        except (AttributeError, TypeError, ValueError) as error:
            self._failed(f"Invalid segmentation result: {error}")
            return
        self._status(
            f"Segmentation complete: {result['object_count']:,} objects on "
            f"{result['device']}."
        )

    def _failed(self, traceback_text: str) -> None:
        message = str(traceback_text).strip()
        final_line = message.splitlines()[-1] if message else "Unknown error"
        self.panel.set_execution_status(f"Segmentation failed: {final_line}")
        self.panel.execution_log.appendPlainText(message)
        self.status_changed.emit(f"Segmentation failed: {final_line}")

    def _finished(self) -> None:
        self._timer.stop()
        self._update_elapsed()
        self._thread = None
        self._worker = None
        self._started = None
        for widget, enabled in zip(
            self.conflict_widgets, self._conflict_enabled, strict=True
        ):
            widget.setEnabled(enabled)
        self._conflict_enabled = ()
        self.panel.set_execution_running(False)

    def _update_elapsed(self) -> None:
        if self._started is not None:
            self.panel.set_elapsed(perf_counter() - self._started)

    def _update_availability(self) -> None:
        self.panel.set_external_busy(
            any(operation.busy for operation in self.conflict_operations)
        )

    def _sync_result_layer(self, result, current: bool) -> None:
        if result is None:
            self.clear_project()
            return
        if current:
            if self._result_layer is not None and self._result_layer in self.viewer.layers:
                self.viewer.layers.remove(self._result_layer)
            spacing = tuple(self.project_state.voxel_spacing_zyx_um)
            offset_y, offset_x = self.project_state.working_offset_yx
            self._result_layer = self.viewer.add_labels(
                result["masks"], name="Raw Cellpose masks", scale=spacing,
                translate=(0.0, offset_y * spacing[1], offset_x * spacing[2]),
                metadata={"mask_type": "nuclear", "nucytoseg_raw_segmentation": True},
            )
            self._result_layer.editable = False
        elif self._result_layer is not None and self._result_layer in self.viewer.layers:
            self._result_layer.name = "Raw Cellpose masks — outdated"

    def clear_project(self) -> None:
        if self._result_layer is not None and self._result_layer in self.viewer.layers:
            self.viewer.layers.remove(self._result_layer)
        self._result_layer = None

    def _status(self, message: str) -> None:
        self.panel.set_execution_status(message)
        self.status_changed.emit(str(message))

    def dispose(self) -> None:
        self._timer.stop()
        self._availability_timer.stop()
        self.clear_project()
        self.panel.run_requested.disconnect(self.run)
        self.panel.configuration_changed.disconnect(
            self.project_state.invalidate_segmentation
        )
        self.project_state.segmentation_result_changed.disconnect(
            self._sync_result_layer
        )
