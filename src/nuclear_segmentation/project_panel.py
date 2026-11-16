"""Functional Phase 2A project/import controls."""
from __future__ import annotations

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .channel_panel import ChannelConfigurationPanel


class ProjectPanel(QWidget):
    open_requested = Signal()
    load_pending_requested = Signal()
    analysis_mode_changed = Signal(str)
    calibration_requested = Signal(object)

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self._pending = False
        self._metadata_spacing_available = False
        self._spacing_manually_edited = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        provenance, provenance_form = self._section("Image Provenance")
        self.provenance_values = {}
        for key, title in (
            ("filename", "File"),
            ("path", "Path"),
            ("format", "Format"),
            ("series", "Series"),
            ("original", "Original"),
            ("standardized", "CZYX"),
            ("dtype", "Data type"),
            ("channels", "Channels"),
            ("planes", "Z planes"),
        ):
            value = QLabel("Not loaded")
            value.setWordWrap(True)
            value.setTextInteractionFlags(value.textInteractionFlags())
            self.provenance_values[key] = value
            provenance_form.addRow(title, value)
        layout.addWidget(provenance)

        calibration, calibration_form = self._section("Calibration and Axes")
        self.axes_override = QLineEdit()
        self.axes_override.setPlaceholderText("Auto from TIFF metadata")
        calibration_form.addRow("Axes override", self.axes_override)
        self.time_index = QSpinBox()
        self.time_index.setRange(0, 1_000_000)
        calibration_form.addRow("Time index", self.time_index)
        self.spacing_fields = []
        for axis in "ZYX":
            field = QLineEdit()
            field.setPlaceholderText("Required if metadata is missing")
            self.spacing_fields.append(field)
            field.textEdited.connect(self._mark_spacing_edited)
            calibration_form.addRow(f"{axis} spacing (µm)", field)
        self.calibration_source = QLabel("Not loaded")
        self.calibration_source.setWordWrap(True)
        calibration_form.addRow("Source", self.calibration_source)
        self.apply_calibration_button = QPushButton("Apply Manual Calibration")
        self.apply_calibration_button.clicked.connect(self._request_calibration)
        calibration_form.addRow(self.apply_calibration_button)
        layout.addWidget(calibration)

        channels, channels_layout = self._section("Channel Configuration")
        self.channel_panel = ChannelConfigurationPanel(project_state)
        channels_layout.addRow(self.channel_panel)
        layout.addWidget(channels)

        mode_section, mode_form = self._section("Analysis Mode")
        self.analysis_mode = QComboBox()
        self.analysis_mode.addItem("New Segmentation", "segment")
        self.analysis_mode.addItem("Resume Analysis", "resume")
        self.analysis_mode.addItem("Density Only", "density_only")
        self.analysis_mode.currentIndexChanged.connect(
            lambda: self.analysis_mode_changed.emit(self.analysis_mode.currentData())
        )
        mode_form.addRow(self.analysis_mode)
        layout.addWidget(mode_section)

        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open Image…")
        self.open_button.clicked.connect(self.open_requested)
        self.load_button = QPushButton("Load Selected File")
        self.load_button.setEnabled(False)
        self.load_button.clicked.connect(self.load_pending_requested)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.load_button)
        layout.addLayout(buttons)
        self.status_label = QLabel("No image loaded")
        self.status_label.setObjectName("importStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        project_state.changed.connect(self.set_dataset)
        project_state.import_status_changed.connect(self.status_label.setText)

    @staticmethod
    def _section(title: str):
        frame = QFrame()
        frame.setObjectName("projectSection")
        root = QVBoxLayout(frame)
        root.setContentsMargins(10, 8, 10, 10)
        heading = QLabel(title)
        heading.setObjectName("contextSectionTitle")
        root.addWidget(heading)
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        root.addLayout(form)
        return frame, form

    def import_options(self) -> dict:
        text_values = [field.text().strip() for field in self.spacing_fields]
        if any(text_values) and not all(text_values):
            raise ValueError("Enter all three Z, Y, and X spacing values.")
        spacing = None
        if all(text_values) and (
            not self._metadata_spacing_available or self._spacing_manually_edited
        ):
            try:
                spacing = tuple(float(value) for value in text_values)
            except ValueError as error:
                raise ValueError("Spacing values must be numbers in micrometers.") from error
        return {
            "axes_override": self.axes_override.text().strip() or None,
            "spacing_override": spacing,
            "time_index": self.time_index.value(),
        }

    def _request_calibration(self) -> None:
        text_values = [field.text().strip() for field in self.spacing_fields]
        if not all(text_values):
            self.calibration_requested.emit(None)
            return
        try:
            spacing = tuple(float(value) for value in text_values)
        except ValueError:
            self.calibration_requested.emit(None)
            return
        self.calibration_requested.emit(spacing)

    def set_pending(self, enabled: bool) -> None:
        self._pending = bool(enabled)
        self.load_button.setEnabled(self._pending)

    def set_loading(self, loading: bool) -> None:
        self.open_button.setEnabled(not loading)
        self.load_button.setEnabled(not loading and self._pending)

    def set_inspection(self, inspection) -> None:
        self.provenance_values["filename"].setText(inspection.source_path.name)
        self.provenance_values["path"].setText(str(inspection.source_path))
        self.provenance_values["format"].setText(inspection.source_format)
        self.provenance_values["original"].setText(
            f"{inspection.reported_axes} {inspection.reported_shape}"
        )
        self._metadata_spacing_available = inspection.voxel_spacing_zyx_um is not None
        self._spacing_manually_edited = False
        if self._metadata_spacing_available:
            for field, value in zip(self.spacing_fields, inspection.voxel_spacing_zyx_um):
                field.setText(f"{value:g}")
            self.calibration_source.setText(inspection.calibration_source)
        else:
            for field in self.spacing_fields:
                field.clear()
            self.calibration_source.setText("Manual calibration required")

    def set_lif_series(self, source_path, series) -> None:
        values = self.provenance_values
        values["filename"].setText(source_path.name)
        values["path"].setText(str(source_path))
        values["format"].setText("Leica LIF")
        values["series"].setText(f"{series.index}: {series.name}")
        values["original"].setText(f"{series.dimensions} {series.shape}")
        values["channels"].setText(str(series.channel_count))
        values["planes"].setText(str(series.z_planes))
        self.time_index.setMaximum(max(series.time_points - 1, 0))
        self._metadata_spacing_available = series.voxel_spacing_zyx_um is not None
        self._spacing_manually_edited = False
        if self._metadata_spacing_available:
            for field, value in zip(
                self.spacing_fields, series.voxel_spacing_zyx_um
            ):
                field.setText(f"{value:g}")
            self.calibration_source.setText("Leica metadata")
        else:
            for field in self.spacing_fields:
                field.clear()
            self.calibration_source.setText("Manual calibration required")

    def _mark_spacing_edited(self, text: str) -> None:
        self._spacing_manually_edited = True

    def set_dataset(self, dataset) -> None:
        values = self.provenance_values
        values["filename"].setText(dataset.source_filename)
        values["path"].setText(str(dataset.source_path))
        values["format"].setText(dataset.source_format)
        values["series"].setText(f"{dataset.series_index}: {dataset.series_name}")
        values["original"].setText(
            f"{dataset.reported_axes} {dataset.reported_shape}"
        )
        values["standardized"].setText(str(dataset.standardized_shape))
        values["dtype"].setText(dataset.dtype)
        values["channels"].setText(str(len(dataset.channels)))
        values["planes"].setText(str(dataset.standardized_shape[1]))
        for field, value in zip(self.spacing_fields, dataset.voxel_spacing_zyx_um):
            field.setText(f"{value:g}")
        self.calibration_source.setText(dataset.calibration_source)
        self.set_pending(False)
