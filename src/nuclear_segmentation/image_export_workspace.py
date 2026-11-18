"""Desktop adapter for the validated calibrated multichannel TIFF exporter."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from qtpy.QtCore import QObject, QThread, Signal, Slot
from qtpy.QtWidgets import QFileDialog

from .image_export import export_images


class ImageExportWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, path, arrays, names, spacing, provenance, protected_paths, file_format, pixel_type):
        super().__init__()
        self.arguments = (
            path,
            arrays,
            names,
            spacing,
            provenance,
            protected_paths,
        )
        self.file_format = file_format
        self.pixel_type = pixel_type

    @Slot()
    def run(self):
        try:
            saved = export_images(
                *self.arguments,
                file_format=self.file_format,
                pixel_type=self.pixel_type,
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(saved)
        finally:
            self.finished.emit()


class ImageExportController(QObject):
    status_changed = Signal(str)

    def __init__(
        self,
        project_state,
        preparation_controller,
        panel,
        preprocessing_page,
        parent=None,
    ):
        super().__init__(parent)
        self.project_state = project_state
        self.preparation = preparation_controller
        self.panel = panel
        self.preprocessing_page = preprocessing_page
        self._thread = None
        self._worker = None
        self._pending = None
        panel.export_requested.connect(self.choose_path)
        preparation_controller.prepared_changed.connect(self.refresh_channels)
        project_state.channels_changed.connect(self.refresh_channels)
        self.refresh_channels()

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def refresh_channels(self, *args) -> None:
        self.panel.set_prepared_channels(
            self.project_state.channels,
            self.preparation.provenance if self.preparation.is_current() else {},
        )

    def choose_path(self, channel_order, file_format: str, pixel_type: str) -> bool:
        source = self.project_state.original_dataset
        if source is None:
            self._status("Load and prepare an image before exporting.")
            return False
        stem = source.source_filename
        for suffix in (".ome.tiff", ".ome.tif", ".tiff", ".tif"):
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        suffix = ".ome.tif" if file_format == "ome" else ".tif"
        suggested = source.source_path.parent / f"{stem}_prepared_{pixel_type}{suffix}"
        file_filter = (
            "OME-TIFF (*.ome.tif *.ome.tiff)"
            if file_format == "ome"
            else "ImageJ TIFF (*.tif *.tiff)"
        )
        path, _ = QFileDialog.getSaveFileName(
            self.panel,
            "Export prepared multichannel TIFF",
            str(suggested),
            file_filter,
        )
        if not path:
            return False
        return self.export(path, channel_order, file_format, pixel_type)

    def export(self, path, channel_order, file_format="ome", pixel_type="uint16") -> bool:
        if self.busy:
            self._status("A TIFF export is already running.")
            return False
        if file_format not in {"ome", "imagej"} or pixel_type not in {"uint16", "float32"}:
            self._status("Select a supported TIFF format and pixel type.")
            return False
        if not self.preparation.is_current():
            self._status("Prepared channels are missing or outdated. Generate previews again.")
            return False
        order = [int(index) for index in channel_order]
        if not order or len(order) != len(set(order)):
            self._status("Select one or more unique prepared channels.")
            return False
        if any(index not in self.preparation.prepared_channels for index in order):
            self._status("The selected channel order contains unavailable prepared data.")
            return False
        source = self.project_state.original_dataset
        if source is None:
            self._status("The source project is no longer available.")
            return False
        by_index = {channel.index: channel for channel in self.project_state.channels}
        arrays = [self.preparation.prepared_channels[index] for index in order]
        names = [by_index[index].name for index in order]
        requested_at = datetime.now(timezone.utc).isoformat()
        provenance = []
        for position, index in enumerate(order):
            record = deepcopy(self.preparation.provenance[index])
            record.update(
                export_channel_position=position,
                export_requested_at_utc=requested_at,
                export_pixel_type=pixel_type,
                export_format=file_format,
            )
            provenance.append(record)
        protected = [source.source_path]
        worker = ImageExportWorker(
            Path(path),
            arrays,
            names,
            tuple(self.project_state.voxel_spacing_zyx_um),
            provenance,
            protected,
            file_format,
            pixel_type,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._succeeded)
        worker.failed.connect(self._failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._worker = worker
        self._thread = thread
        self._pending = {
            "order": order,
            "names": names,
            "format": file_format,
            "pixel_type": pixel_type,
        }
        self.panel.set_busy(True)
        self.preprocessing_page.setEnabled(False)
        self._status(
            f"Writing {len(order)} prepared channel(s) as {pixel_type} "
            f"{'OME-TIFF' if file_format == 'ome' else 'ImageJ TIFF'}…"
        )
        thread.start()
        return True

    def _succeeded(self, path) -> None:
        details = self._pending or {}
        names = details.get("names", [])
        self.panel.set_results(
            "\n".join(
                (
                    f"Saved: {path}",
                    f"Format: {details.get('format')}",
                    f"Pixel type: {details.get('pixel_type')}",
                    f"Channel order: {', '.join(names)}",
                    f"Provenance: {path}.json",
                    "Display settings applied: no",
                )
            )
        )
        self._status(f"TIFF export completed: {path}")

    def _failed(self, message: str) -> None:
        self._status(f"TIFF export failed: {message}")

    def _finished(self) -> None:
        completion_status = self.panel.status.text()
        self._thread = None
        self._worker = None
        self._pending = None
        self.panel.set_busy(False)
        self.preprocessing_page.setEnabled(True)
        self.refresh_channels()
        self.panel.set_status(completion_status)

    def clear_project(self) -> None:
        self.panel.reset()

    def _status(self, message: str) -> None:
        self.panel.set_status(message)
        self.status_changed.emit(str(message))

    def dispose(self) -> None:
        self.panel.export_requested.disconnect(self.choose_path)
        self.preparation.prepared_changed.disconnect(self.refresh_channels)
        self.project_state.channels_changed.disconnect(self.refresh_channels)
