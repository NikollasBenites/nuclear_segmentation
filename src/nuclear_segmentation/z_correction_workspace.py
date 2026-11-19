"""Multichannel desktop adapter for the validated Z-correction algorithm."""
from __future__ import annotations

from copy import deepcopy
from time import perf_counter

import numpy as np
from qtpy.QtCore import QObject, QThread, Signal, Slot

from .normalization import mask_digest
from .z_correction import correct_z, validate_options


def working_valid_mask_yx(project_state) -> np.ndarray:
    dataset = project_state.working_dataset
    if dataset is None:
        raise ValueError("Load a project image before calculating Z correction.")
    provenance = project_state.crop_provenance
    if provenance is not None and provenance.selection_type == "polygon":
        mask = np.asarray(project_state.roi_mask_yx, dtype=bool)
    else:
        mask = np.ones(dataset.data_czyx.shape[-2:], dtype=bool)
    if mask.shape != dataset.data_czyx.shape[-2:] or not mask.any():
        raise ValueError("The working crop mask is empty or incompatible.")
    return mask


def diagnostic_profiles(original, corrected, valid_mask_yx, include_zeros=True):
    """P99 and mean on an explicit valid spatial scope for every Z plane."""
    source = np.asarray(original)
    output = np.asarray(corrected)
    mask = np.asarray(valid_mask_yx, dtype=bool)
    if source.ndim != 3 or output.shape != source.shape or mask.shape != source.shape[-2:]:
        raise ValueError("Diagnostic profiles require matching ZYX images and XY mask.")
    result = {}
    for title, volume in (("original", source), ("corrected", output)):
        p99 = np.full(source.shape[0], np.nan, dtype=float)
        mean = np.full(source.shape[0], np.nan, dtype=float)
        for z, plane in enumerate(volume):
            valid = mask & np.isfinite(source[z]) & np.isfinite(output[z])
            if not include_zeros:
                valid &= source[z] > 0
            values = plane[valid]
            if values.size:
                p99[z] = np.percentile(values, 99)
                mean[z] = np.mean(values, dtype=np.float64)
        result[title] = {"p99": p99.tolist(), "mean": mean.tolist()}
    return result


def calculate_z_corrections(
    data_by_index: dict[int, np.ndarray],
    options_by_index: dict[int, dict],
    preview_index: int,
    valid_mask_yx,
    include_zeros: bool,
    progress=None,
):
    reports = {}
    previews = {}
    total = len(options_by_index)
    for position, (index, options) in enumerate(options_by_index.items(), start=1):
        if index not in data_by_index:
            raise ValueError(f"Z-correction channel index {index} is unavailable.")
        validated = validate_options(options)
        started = perf_counter()
        corrected, report = correct_z(data_by_index[index], validated)
        report = deepcopy(report)
        report.update(
            channel_index=int(index),
            processing_seconds=float(perf_counter() - started),
            shape_zyx=list(corrected.shape),
            source_dtype=str(np.asarray(data_by_index[index]).dtype),
            output_dtype=str(corrected.dtype),
            valid_xy_sha256=mask_digest(valid_mask_yx),
            diagnostic_include_zeros=bool(include_zeros),
            diagnostic_scope=(
                "valid working pixels including genuine zeros"
                if include_zeros
                else "valid working pixels with original intensity > 0"
            ),
            intensity_profiles=diagnostic_profiles(
                data_by_index[index], corrected, valid_mask_yx, include_zeros
            ),
        )
        reports[int(index)] = report
        previews[int(index)] = corrected
        if progress is not None:
            progress(position, total, int(index))
    if int(preview_index) not in previews:
        raise ValueError("The inspected channel was not included in the correction batch.")
    return reports, previews


class ZCorrectionWorker(QObject):
    succeeded = Signal(object, object, int)
    failed = Signal(str)
    progress = Signal(int, int, int)
    finished = Signal()

    def __init__(
        self,
        data_by_index,
        options_by_index,
        preview_index,
        valid_mask_yx,
        include_zeros,
        parent=None,
    ):
        super().__init__(parent)
        self.data_by_index = data_by_index
        self.options_by_index = options_by_index
        self.preview_index = int(preview_index)
        self.valid_mask_yx = valid_mask_yx
        self.include_zeros = bool(include_zeros)

    @Slot()
    def run(self):
        try:
            reports, previews = calculate_z_corrections(
                self.data_by_index,
                self.options_by_index,
                self.preview_index,
                self.valid_mask_yx,
                self.include_zeros,
                progress=lambda done, total, index: self.progress.emit(
                    done, total, index
                ),
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(reports, previews, self.preview_index)
        finally:
            self.finished.emit()


class ZCorrectionController(QObject):
    status_changed = Signal(str)
    correction_applied = Signal()

    def __init__(self, viewer, project_state, panel, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.project_state = project_state
        self.panel = panel
        self.preview_layer = None
        self.preview_channel_index = None
        self.preview_layers = {}
        self._thread = None
        self._worker = None
        self._job_fingerprint = None
        panel.calculate_requested.connect(self.calculate)
        panel.apply_requested.connect(self.apply)
        panel.reset_selected_requested.connect(self.reset_selected)
        panel.reset_all_requested.connect(self.reset_all)
        panel.profiles_requested.connect(self.show_profiles)
        panel.settings_changed.connect(self.invalidate_channel)
        panel.inspected_channel_changed.connect(self.inspect_channel)
        panel.results_channel_changed.connect(self.show_results)

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def calculate(self, options_by_index, inspected_index, include_zeros) -> bool:
        if self.busy:
            self._status("A Z-correction calculation is already running.")
            return False
        dataset = self.project_state.working_dataset
        if dataset is None:
            self._status("Load a project image before calculating Z correction.")
            return False
        try:
            options = {
                int(index): validate_options(value)
                for index, value in options_by_index.items()
            }
            valid_mask = working_valid_mask_yx(self.project_state)
        except ValueError as error:
            self._status(str(error))
            return False
        data = {index: dataset.data_czyx[index] for index in options}
        self._job_fingerprint = self._fingerprint(options)
        worker = ZCorrectionWorker(
            data, options, inspected_index, valid_mask, include_zeros
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._progress)
        worker.succeeded.connect(self._calculation_succeeded)
        worker.failed.connect(self._calculation_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._calculation_finished)
        thread.finished.connect(thread.deleteLater)
        self._worker = worker
        self._thread = thread
        self.panel.calculate_button.setEnabled(False)
        self._status(f"Calculating Z correction for {len(options)} channel(s)…")
        thread.start()
        return True

    def _progress(self, done: int, total: int, channel_index: int) -> None:
        channel = self._channel(channel_index)
        self._status(f"Z correction {done}/{total}: {channel.name}")

    def _calculation_succeeded(self, reports, previews, preview_index: int) -> None:
        options = {index: report["options"] for index, report in reports.items()}
        if self._job_fingerprint != self._fingerprint(options):
            self._calculation_failed(
                "Project inputs changed while Z correction was being calculated."
            )
            return
        source = self.project_state.original_dataset
        for index, report in reports.items():
            report.update(
                source_path=str(source.source_path),
                series_index=int(source.series_index),
                series_identifier=str(source.series_identifier),
                channel_name_at_calculation=self._channel(index).name,
                working_shape_czyx=list(
                    self.project_state.working_dataset.standardized_shape
                ),
                voxel_spacing_zyx_um=list(
                    self.project_state.voxel_spacing_zyx_um
                ),
                working_offset_yx=list(self.project_state.working_offset_yx),
            )
            self.project_state.set_z_correction_preview(
                index, report["options"], report
            )
            self.panel.set_channel_state(index, "Preview ready")
        self._set_previews(previews, preview_index)
        outdated = []
        for index in reports:
            if report := self.project_state.z_correction_reports.get(index):
                if report["options"] != self.panel.options_for(index):
                    outdated.append(index)
            self.invalidate_channel(index)
        self.show_results(preview_index)
        if outdated:
            self._status(
                "Z-correction calculation finished, but settings changed during "
                "calculation. Previous-result previews are marked outdated."
            )
        else:
            self._status(
                f"Z-correction preview ready for {len(reports)} channel(s)."
            )

    def _set_previews(self, previews: dict[int, np.ndarray], inspected_index: int) -> None:
        spacing = tuple(self.project_state.voxel_spacing_zyx_um)
        offset_y, offset_x = self.project_state.working_offset_yx
        for channel_index, data in previews.items():
            channel = self._channel(channel_index)
            report = self.project_state.z_correction_reports[int(channel_index)]
            layer = self.preview_layers.get(int(channel_index))
            if layer is None or layer not in self.viewer.layers:
                layer = self.viewer.add_image(
                    data,
                    name=f"Z-corrected preview · {channel.name}",
                    colormap=channel.colormap,
                    blending="additive",
                    scale=spacing,
                    translate=(0.0, offset_y * spacing[1], offset_x * spacing[2]),
                    units=("µm", "µm", "µm"),
                    metadata={
                        "nucytoseg_z_correction_preview": True,
                        "channel_index": int(channel_index),
                    },
                )
            else:
                # Replace only after successful calculation. Display choices such
                # as visibility, colormap, opacity and blending remain untouched.
                layer.data = data
                layer.name = f"Z-corrected preview · {channel.name}"
                layer.scale = spacing
                layer.translate = (
                    0.0, offset_y * spacing[1], offset_x * spacing[2]
                )
                layer.units = ("µm", "µm", "µm")
            layer.metadata.update(
                nucytoseg_z_correction_preview=True,
                channel_index=int(channel_index),
                z_correction_options=deepcopy(report["options"]),
                z_correction_preview_status="current",
                z_correction_applied=False,
            )
            self.preview_layers[int(channel_index)] = layer
        self.preview_channel_index = int(inspected_index)
        self.preview_layer = self.preview_layers.get(self.preview_channel_index)

    def _calculation_failed(self, message: str) -> None:
        self._status(f"Z correction failed: {message}")

    def _calculation_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._job_fingerprint = None
        self.panel.calculate_button.setEnabled(self.project_state.dataset_ready)

    def apply(self, channel_indexes) -> bool:
        indexes = [int(index) for index in channel_indexes]
        if not indexes:
            self._status("Select one or more calculated corrections to apply.")
            return False
        normalization = self.project_state.normalization_configuration or {}
        if (
            self.project_state.normalization_is_current
            and normalization.get("mode") == "roi_fixed"
        ):
            self._status(
                "ROI-fixed normalization limits describe uncorrected intensities. "
                "Reset normalization before applying Z correction, then recalculate it."
            )
            return False
        try:
            for index in indexes:
                expected = self.panel.options_for(index)
                report = self.project_state.z_correction_reports.get(index)
                if report is None or report["options"] != expected:
                    raise ValueError(
                        f"Recalculate current settings for {self._channel(index).name}."
                    )
            self.project_state.apply_z_corrections(indexes)
        except ValueError as error:
            self._status(str(error))
            return False
        self.correction_applied.emit()
        for index in indexes:
            self.panel.set_channel_state(index, "Applied")
            self.show_results(index)
            layer = self.preview_layers.get(index)
            if layer is not None and layer in self.viewer.layers:
                layer.name = f"Z-corrected applied · {self._channel(index).name}"
                layer.metadata.update(
                    z_correction_preview_status="current",
                    z_correction_applied=True,
                )
        self._status(
            "Z correction applied as project configuration. No Cellpose model has run."
        )
        return True

    def invalidate_channel(self, channel_index: int) -> None:
        index = int(channel_index)
        report = self.project_state.z_correction_reports.get(index)
        if report is None:
            return
        current = self.panel.options_for(index)
        matches = report["options"] == current
        applied = index in self.project_state.z_correction_applied
        self.panel.set_channel_state(
            index,
            "Applied" if matches and applied
            else "Preview ready" if matches
            else "Outdated settings",
        )
        layer = self.preview_layers.get(index)
        if layer is not None and layer in self.viewer.layers:
            layer.metadata["z_correction_preview_status"] = (
                "current" if matches else "outdated"
            )
            if not matches:
                layer.name = (
                    f"Z-corrected preview (outdated) · {self._channel(index).name}"
                )
            elif applied:
                layer.name = f"Z-corrected applied · {self._channel(index).name}"
            else:
                layer.name = f"Z-corrected preview · {self._channel(index).name}"
        if not matches:
            self._status(
                f"{self._channel(index).name} settings changed; the previous "
                "preview remains visible but is outdated. Recalculate before applying."
            )

    def invalidate_all(self) -> None:
        self.remove_preview()
        self.project_state.invalidate_z_corrections()
        for channel in self.project_state.channels:
            if channel.index in self.project_state.z_correction_options:
                self.panel.set_channel_state(channel.index, "Outdated after crop")
        self.panel.results.set_results("Z-correction results are outdated.")

    def reset_selected(self, channel_indexes) -> None:
        indexes = [int(index) for index in channel_indexes]
        self.project_state.reset_z_corrections(indexes)
        self.panel.reset_channel_controls(indexes)
        for index in indexes:
            self.panel.set_channel_state(index, "No correction")
        for index in indexes:
            self.remove_preview(index)
        self._status("Selected Z corrections reset.")
        self._refresh_results()

    def reset_all(self) -> None:
        self.remove_preview()
        self.project_state.reset_all_z_corrections()
        self.panel.reset_controls()
        self._status("All Z corrections reset.")

    def show_results(self, channel_index: int) -> None:
        index = int(channel_index)
        report = self.project_state.z_correction_reports.get(index)
        if report is None:
            self.panel.results.set_results(
                f"{self._channel(index).name}: no current correction report."
            )
            self.panel.clear_profiles()
            return
        options = report["options"]
        observed = np.asarray(
            [np.nan if value is None else value for value in report["observed_profile"]]
        )
        smooth = np.asarray(report["smoothed_profile"], dtype=float)
        gain = np.asarray(report["gain"], dtype=float)
        applied = index in self.project_state.z_correction_applied
        current = report["options"] == self.panel.options_for(index)
        text = "\n".join(
            (
                f"Channel: {self._channel(index).name} (index {index})",
                f"Reference percentile: {options['percentile']:g}",
                f"Gaussian sigma: {options['sigma_planes']:g} Z planes",
                f"Configured gain bounds: {options['min_gain']:g}–{options['max_gain']:g}",
                f"Z planes: {len(gain)}",
                f"Observed reference: {np.nanmin(observed):.6g}–{np.nanmax(observed):.6g}",
                f"Smoothed reference: {np.nanmin(smooth):.6g}–{np.nanmax(smooth):.6g}",
                f"Applied gain: {np.nanmin(gain):.6g}–{np.nanmax(gain):.6g}",
                f"Processing duration: {report['processing_seconds']:.3f} s",
                f"Diagnostic sampling: {report['diagnostic_scope']}",
                f"Preview status: {'current' if current else 'outdated settings'}",
                f"Applied configuration: {'yes' if applied else 'no'}",
                "Segmentation routing: "
                + (
                    "corrected when designated primary"
                    if options["segmentation"]
                    else "original"
                ),
                f"Measurement routing: {'corrected' if options['measurement'] else 'original'}",
            )
        )
        self.panel.set_results(index, text)
        self.panel.set_profiles(
            self._channel(index).name,
            report,
            self.project_state.voxel_spacing_zyx_um[0],
        )

    def inspect_channel(self, channel_index: int) -> None:
        index = int(channel_index)
        self.show_results(index)
        if index in self.preview_layers:
            self.preview_channel_index = index
            self.preview_layer = self.preview_layers[index]
        elif self.preview_layers:
            self._status(
                f"Inspecting {self._channel(index).name}. Recalculate to create its "
                "corrected image preview."
            )

    def _refresh_results(self) -> None:
        index = self.panel.results_channel_combo.currentData()
        if index is not None:
            self.show_results(int(index))

    def show_profiles(self, channel_index: int, include_zeros: bool) -> bool:
        index = int(channel_index)
        report = self.project_state.z_correction_reports.get(index)
        if report is None:
            self._status("Calculate this channel before opening Z profiles.")
            return False
        if report["diagnostic_include_zeros"] != bool(include_zeros):
            self._status(
                "The diagnostic zero-pixel option changed. Recalculate before plotting."
            )
            return False
        self.panel.set_profiles(
            self._channel(index).name,
            report,
            self.project_state.voxel_spacing_zyx_um[0],
        )
        self.panel.results.set_expanded(True)
        return True

    def remove_preview(self, channel_index: int | None = None) -> None:
        indexes = (
            tuple(self.preview_layers)
            if channel_index is None
            else (int(channel_index),)
        )
        for index in indexes:
            layer = self.preview_layers.pop(index, None)
            if layer is not None and layer in self.viewer.layers:
                self.viewer.layers.remove(layer)
        if self.preview_channel_index not in self.preview_layers:
            self.preview_channel_index = next(iter(self.preview_layers), None)
        self.preview_layer = self.preview_layers.get(self.preview_channel_index)

    def clear_project(self) -> None:
        self.remove_preview()
        self.panel.clear_profiles()
        self.panel.reset_controls()

    def _channel(self, index: int):
        channel = next(
            (item for item in self.project_state.channels if item.index == int(index)),
            None,
        )
        if channel is None:
            raise ValueError(f"Unknown channel index: {index}")
        return channel

    def _fingerprint(self, options_by_index) -> dict:
        source = self.project_state.original_dataset
        working = self.project_state.working_dataset
        crop_digest = None
        if self.project_state.roi_mask_yx is not None:
            crop_digest = mask_digest(self.project_state.roi_mask_yx)
        return {
            "source_path": None if source is None else str(source.source_path),
            "series_index": None if source is None else int(source.series_index),
            "working_shape": None if working is None else list(working.standardized_shape),
            "working_offset": list(self.project_state.working_offset_yx),
            "crop_sha256": crop_digest,
            "options": {
                int(index): validate_options(options)
                for index, options in options_by_index.items()
            },
        }

    def _status(self, message: str) -> None:
        self.panel.set_status(message)
        self.status_changed.emit(str(message))

    def dispose(self) -> None:
        self.clear_project()
        self.panel.calculate_requested.disconnect(self.calculate)
        self.panel.apply_requested.disconnect(self.apply)
        self.panel.reset_selected_requested.disconnect(self.reset_selected)
        self.panel.reset_all_requested.disconnect(self.reset_all)
        self.panel.profiles_requested.disconnect(self.show_profiles)
        self.panel.settings_changed.disconnect(self.invalidate_channel)
        self.panel.inspected_channel_changed.disconnect(self.inspect_channel)
        self.panel.results_channel_changed.disconnect(self.show_results)
