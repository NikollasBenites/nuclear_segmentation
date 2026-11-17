"""Desktop controller for memory-conscious multichannel image preparation."""
from __future__ import annotations

from copy import deepcopy

import numpy as np
from qtpy.QtCore import QObject, QThread, Signal, Slot

from .image_preparation import (
    prepare_channel,
    preparation_fingerprint,
    validate_preparation_options,
)
from .normalization import mask_digest


class ImagePreparationWorker(QObject):
    succeeded = Signal(object, object)
    failed = Signal(str)
    progress = Signal(int, int, int)
    finished = Signal()

    def __init__(self, data_by_index, plans, options, valid_mask_yx, fingerprint):
        super().__init__()
        self.data_by_index = data_by_index
        self.plans = plans
        self.options = options
        self.valid_mask_yx = valid_mask_yx
        self.fingerprint = fingerprint

    @Slot()
    def run(self):
        prepared = {}
        reports = {}
        try:
            total = len(self.plans)
            for done, (index, plan) in enumerate(self.plans.items(), start=1):
                data, report = prepare_channel(
                    self.data_by_index[index],
                    plan["route"],
                    self.options,
                    valid_mask_yx=self.valid_mask_yx,
                    z_options=plan.get("z_options"),
                )
                prepared[index] = data
                reports[index] = report
                self.progress.emit(done, total, index)
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(
                {"prepared": prepared, "reports": reports}, self.fingerprint
            )
        finally:
            self.finished.emit()


class ImagePreparationController(QObject):
    status_changed = Signal(str)

    def __init__(self, viewer, project_state, panel, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.project_state = project_state
        self.panel = panel
        self.prepared_channels: dict[int, np.ndarray] = {}
        self.provenance: dict[int, dict] = {}
        self.preview_layers = {}
        self._thread = None
        self._worker = None
        self._job_fingerprint = None
        panel.prepare_requested.connect(self.prepare)
        panel.clear_requested.connect(self.clear_previews)
        panel.settings_changed.connect(self.invalidate)
        project_state.channels_changed.connect(self._sync_channel_names)

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def _valid_mask(self) -> np.ndarray:
        dataset = self.project_state.working_dataset
        if dataset is None:
            raise ValueError("Load a project image before preparing channels.")
        mask = self.project_state.roi_mask_yx
        if mask is None:
            return np.ones(dataset.data_czyx.shape[-2:], dtype=bool)
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != dataset.data_czyx.shape[-2:] or not mask.any():
            raise ValueError("The polygon crop mask is empty or incompatible.")
        return mask

    def prepare(self, routes, options) -> bool:
        if self.busy:
            self._status("Image preparation is already running.")
            return False
        dataset = self.project_state.working_dataset
        if dataset is None:
            self._status("Load a project image before preparing channels.")
            return False
        try:
            routes = {int(index): str(route) for index, route in routes.items()}
            options = validate_preparation_options(options)
            valid_mask = self._valid_mask()
            plans = {}
            for index, route in routes.items():
                if index < 0 or index >= dataset.data_czyx.shape[0]:
                    raise ValueError(f"Unknown channel index: {index}")
                plan = {"route": route}
                if route in {"z_corrected", "z_corrected_normalized"}:
                    report = self.project_state.z_correction_reports.get(index)
                    if report is None:
                        raise ValueError(
                            f"Calculate a current Z correction for channel {index} first."
                        )
                    plan["z_options"] = deepcopy(report["options"])
                plans[index] = plan
            fingerprint = preparation_fingerprint(self.project_state, routes, options)
        except ValueError as error:
            self._status(str(error))
            return False
        self.clear_previews(status=False)
        data = {index: dataset.data_czyx[index] for index in routes}
        worker = ImagePreparationWorker(data, plans, options, valid_mask, fingerprint)
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
        self._job_fingerprint = fingerprint
        self.panel.preview_button.setEnabled(False)
        self._status(f"Preparing {len(routes)} channel(s)…")
        thread.start()
        return True

    def _progress(self, done: int, total: int, index: int) -> None:
        channel = self._channel(index)
        self._status(f"Image preparation {done}/{total}: {channel.name}")

    def _succeeded(self, payload, fingerprint) -> None:
        routes = fingerprint["routes"]
        if fingerprint != self._job_fingerprint or fingerprint != preparation_fingerprint(
            self.project_state, routes, fingerprint["options"]
        ):
            self._failed("Project inputs changed during image preparation.")
            return
        source = self.project_state.original_dataset
        spacing = tuple(self.project_state.voxel_spacing_zyx_um)
        offset_y, offset_x = self.project_state.working_offset_yx
        self.prepared_channels = payload["prepared"]
        self.provenance = {}
        result_lines = []
        for index, data in self.prepared_channels.items():
            channel = self._channel(index)
            report = payload["reports"][index]
            report.update(
                channel_index=int(index),
                channel_name=channel.name,
                source_path=str(source.source_path),
                source_format=source.source_format,
                series_index=int(source.series_index),
                series_identifier=str(source.series_identifier),
                original_shape_czyx=list(source.standardized_shape),
                working_shape_czyx=list(self.project_state.working_dataset.standardized_shape),
                working_offset_yx=list(self.project_state.working_offset_yx),
                voxel_spacing_zyx_um=list(spacing),
                valid_roi_sha256=(
                    None
                    if self.project_state.roi_mask_yx is None
                    else mask_digest(self.project_state.roi_mask_yx)
                ),
                intended_use="visualization and proofreading preparation",
            )
            self.provenance[index] = report
            layer = self.viewer.add_image(
                data,
                name=f"Prepared · {channel.name} · {report['route_label']}",
                colormap=channel.colormap,
                blending="additive",
                scale=spacing,
                translate=(0.0, offset_y * spacing[1], offset_x * spacing[2]),
                units=("µm", "µm", "µm"),
                metadata={
                    "nucytoseg_preparation_preview": True,
                    "channel_index": int(index),
                    "processing_route": report["route"],
                },
            )
            self.preview_layers[index] = layer
            normalization = report["global_normalization"]
            limits = (
                ""
                if normalization is None
                else f"; limits {normalization['limits'][0]:.6g}–{normalization['limits'][1]:.6g}"
            )
            result_lines.append(
                f"{index}: {channel.name} — {report['route_label']}{limits}; "
                f"output {report['output_range'][0]:.6g}–{report['output_range'][1]:.6g}"
            )
        self.panel.set_results("\n".join(result_lines))
        self.panel.set_preview_available(True)
        self._status(f"Prepared previews ready for {len(self.prepared_channels)} channel(s).")

    def _failed(self, message: str) -> None:
        self.clear_previews(status=False)
        self._status(f"Image preparation failed: {message}")

    def _finished(self) -> None:
        self._thread = None
        self._worker = None
        self._job_fingerprint = None
        self.panel.preview_button.setEnabled(self.project_state.dataset_ready)

    def clear_previews(self, *, status: bool = True) -> None:
        for layer in tuple(self.preview_layers.values()):
            if layer in self.viewer.layers:
                self.viewer.layers.remove(layer)
        self.preview_layers.clear()
        self.prepared_channels.clear()
        self.provenance.clear()
        self.panel.set_preview_available(False)
        self.panel.set_results("No prepared channels.")
        if status:
            self._status("Prepared image previews cleared.")

    def invalidate(self, *args) -> None:
        if self.prepared_channels:
            self.clear_previews(status=False)
            self._status("Image preparation settings changed. Generate new previews.")

    def clear_project(self) -> None:
        self.clear_previews(status=False)
        self.panel.reset_controls()

    def _sync_channel_names(self, channels) -> None:
        """Keep stable-index previews/provenance aligned with channel renaming."""
        by_index = {channel.index: channel for channel in channels}
        for index, layer in self.preview_layers.items():
            channel = by_index.get(index)
            report = self.provenance.get(index)
            if channel is not None and report is not None:
                report["channel_name"] = channel.name
                layer.name = f"Prepared · {channel.name} · {report['route_label']}"

    def _channel(self, index: int):
        channel = next(
            (item for item in self.project_state.channels if item.index == int(index)),
            None,
        )
        if channel is None:
            raise ValueError(f"Unknown channel index: {index}")
        return channel

    def _status(self, message: str) -> None:
        self.panel.set_status(message)
        self.status_changed.emit(str(message))

    def dispose(self) -> None:
        self.clear_project()
        self.panel.prepare_requested.disconnect(self.prepare)
        self.panel.clear_requested.disconnect(self.clear_previews)
        self.panel.settings_changed.disconnect(self.invalidate)
        self.project_state.channels_changed.disconnect(self._sync_channel_names)
