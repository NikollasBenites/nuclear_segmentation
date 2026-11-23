"""Functional Phase 2A project/import controls."""
from __future__ import annotations

from qtpy.QtCore import QSignalBlocker, Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QInputDialog,
    QMessageBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .channel_panel import ChannelConfigurationPanel
from .collapsible_section import CollapsibleSection
from .ui_icons import icon


class ProjectPanel(QWidget):
    open_requested = Signal()
    load_pending_requested = Signal()
    calibration_requested = Signal(object)
    section_state_changed = Signal(str, bool)
    close_requested = Signal()
    availability_changed = Signal(bool)
    session_selected = Signal(str)
    session_creation_requested = Signal(str, str, int)

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self._pending = False
        self._metadata_spacing_available = False
        self._spacing_manually_edited = False
        self.sections = {}
        layout = QVBoxLayout(self)
        self.project_actions = QVBoxLayout()
        self.project_description = QLabel("No native project directory")
        self.project_description.setWordWrap(True)
        layout.addLayout(self.project_actions)
        layout.addWidget(self.project_description)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        session_section, session_form = self._section("sessions", "Segmentation Sessions")
        self.session_combo = QComboBox()
        self.session_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.session_combo.currentIndexChanged.connect(self._select_session)
        session_form.addRow(self.session_combo)
        self.session_details = QLabel()
        self.session_details.setTextFormat(Qt.TextFormat.PlainText)
        self.session_details.setWordWrap(True)
        session_form.addRow(self.session_details)
        self.new_session_button = QPushButton("New Session…")
        self.new_session_button.clicked.connect(self._create_session)
        self.new_session_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        session_form.addRow(self.new_session_button)
        self.rename_session_button = QPushButton("Rename Session…")
        self.rename_session_button.clicked.connect(self._rename_session)
        self.rename_session_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        session_form.addRow(self.rename_session_button)
        layout.addWidget(session_section)
        self.session_section = session_section

        provenance, provenance_form = self._provenance_section()
        self.provenance_values = {}
        for key, title in (
            ("filename", "File"),
            ("path", "Path"),
            ("format", "Format"),
            ("series", "Series"),
            ("original_axes", "Original axes"),
            ("original_dimensions", "Original dimensions"),
            ("standardized", "CZYX dimensions"),
            ("dtype", "Data type"),
            ("channels", "Channels"),
            ("planes", "Z planes"),
        ):
            value = QLabel("Not loaded")
            value.setWordWrap(True)
            value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            value.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            value.setMinimumWidth(0)
            self.provenance_values[key] = value
            row = provenance_form.rowCount()
            label = QLabel(title)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            provenance_form.addWidget(label, row, 0)
            provenance_form.addWidget(value, row, 1)
        layout.addWidget(provenance)

        calibration, calibration_form = self._section(
            "calibration", "Calibration and Axes"
        )
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

        channels, channels_layout = self._section(
            "channels", "Channel Configuration"
        )
        self.channel_panel = ChannelConfigurationPanel(project_state)
        channels_layout.addRow(self.channel_panel)
        layout.addWidget(channels)

        self.open_button = QPushButton("Open Image…")
        self.open_button.clicked.connect(self.open_requested)
        self.load_button = QPushButton("Load Selected File")
        self.load_button.setEnabled(False)
        self.load_button.clicked.connect(self.load_pending_requested)
        self.close_button = QPushButton("Close Project")
        self.close_button.setIcon(icon("close"))
        self.close_button.setToolTip(
            "Close the current project and return to an empty workspace."
        )
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.close_requested)
        layout.addWidget(self.open_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.load_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.status_label = QLabel("No image loaded")
        self.status_label.setObjectName("importStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        project_state.changed.connect(self.set_dataset)
        project_state.import_status_changed.connect(self.status_label.setText)
        project_state.sessions_changed.connect(self.refresh_sessions)
        project_state.active_session_changed.connect(self.refresh_sessions)
        project_state.channels_changed.connect(self.refresh_sessions)
        project_state.segmentation_result_changed.connect(self.refresh_sessions)
        self.refresh_sessions()

    def refresh_sessions(self, *args) -> None:
        state = self.project_state
        self.session_section.setVisible(state.dataset_ready)
        with QSignalBlocker(self.session_combo):
            self.session_combo.clear()
            for session in state.sessions.values():
                self.session_combo.addItem(session.display_name, session.session_id)
            self.session_combo.setCurrentIndex(self.session_combo.findData(state.active_session_id))
        session = state.active_session
        index = session.source_channel_index
        if index is None:
            index = state.active_channel_index
        channel = next((item.name for item in state.channels if item.index == index), "Unassigned")
        status = "Empty"
        if session.segmentation_result is not None:
            status = "Current masks" if session.segmentation_is_current else "Outdated masks"
        self.session_details.setText(f"{session.scientific_role.title()} · {channel} · {status}")

    def _select_session(self) -> None:
        session_id = self.session_combo.currentData()
        if session_id is not None:
            self.session_selected.emit(session_id)

    def _create_session(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("New Segmentation Session")
        form = QFormLayout(dialog)
        name = QLineEdit()
        name.setMaxLength(80)
        role = QComboBox()
        role.addItem("Nuclear", "nuclear")
        role.addItem("Cytoplasmic", "cytoplasmic")
        source = QComboBox()
        for channel in self.project_state.channels:
            source.addItem(channel.name, channel.index)
        source.setCurrentIndex(source.findData(self.project_state.segmentation_channel_index))
        form.addRow("Name", name)
        form.addRow("Scientific role", role)
        form.addRow("Source channel", source)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        confirm.setEnabled(False)
        name.textChanged.connect(lambda text: confirm.setEnabled(bool(text.strip())))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.session_creation_requested.emit(name.text(), role.currentData(), source.currentData())

    def _rename_session(self) -> None:
        session = self.project_state.active_session
        name, accepted = QInputDialog.getText(
            self, "Rename Segmentation Session", "Display name", text=session.display_name
        )
        if accepted:
            try:
                self.project_state.rename_session(session.session_id, name)
            except ValueError as error:
                QMessageBox.warning(self, "Rename Segmentation Session", str(error))


    def _register_section(self, section: CollapsibleSection) -> None:
        self.sections[section.key] = section
        section.expanded_changed.connect(self.section_state_changed)

    def _section(self, key: str, title: str):
        frame = CollapsibleSection(key, title)
        self._register_section(frame)
        form = QFormLayout()
        form.setContentsMargins(10, 8, 10, 10)
        frame.set_content_layout(form)
        return frame, form

    def _provenance_section(self):
        frame = CollapsibleSection("provenance", "Image Provenance")
        self._register_section(frame)
        grid = QGridLayout()
        grid.setContentsMargins(10, 8, 10, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        frame.set_content_layout(grid)
        return frame, grid

    def section_states(self) -> dict[str, bool]:
        return {key: section.expanded for key, section in self.sections.items()}

    def set_section_states(self, states) -> None:
        if not isinstance(states, dict):
            states = dict(states or ())
        for key, expanded in states.items():
            section = self.sections.get(str(key))
            if section is not None and isinstance(expanded, bool):
                section.set_expanded(expanded, emit=False)

    def expand_section(self, key: str) -> None:
        section = self.sections.get(key)
        if section is not None:
            section.set_expanded(True)

    def import_options(self) -> dict:
        text_values = [field.text().strip() for field in self.spacing_fields]
        if any(text_values) and not all(text_values):
            self.expand_section("calibration")
            raise ValueError("Enter all three Z, Y, and X spacing values.")
        spacing = None
        if all(text_values) and (
            not self._metadata_spacing_available or self._spacing_manually_edited
        ):
            try:
                spacing = tuple(float(value) for value in text_values)
            except ValueError as error:
                self.expand_section("calibration")
                raise ValueError("Spacing values must be numbers in micrometers.") from error
        return {
            "axes_override": self.axes_override.text().strip() or None,
            "spacing_override": spacing,
            "time_index": self.time_index.value(),
        }

    def _request_calibration(self) -> None:
        text_values = [field.text().strip() for field in self.spacing_fields]
        if not all(text_values):
            self.expand_section("calibration")
            self.calibration_requested.emit(None)
            return
        try:
            spacing = tuple(float(value) for value in text_values)
        except ValueError:
            self.expand_section("calibration")
            self.calibration_requested.emit(None)
            return
        self.calibration_requested.emit(spacing)

    def set_pending(self, enabled: bool) -> None:
        self._pending = bool(enabled)
        self.load_button.setEnabled(self._pending)
        self.close_button.setEnabled(self._pending or self.project_state.dataset_ready)
        self.availability_changed.emit(self.close_button.isEnabled())

    def set_loading(self, loading: bool) -> None:
        self.open_button.setEnabled(not loading)
        self.load_button.setEnabled(not loading and self._pending)

    def set_inspection(self, inspection) -> None:
        self.provenance_values["filename"].setText(inspection.source_path.name)
        self.provenance_values["path"].setText(str(inspection.source_path))
        self.provenance_values["path"].setToolTip(str(inspection.source_path))
        self.provenance_values["format"].setText(inspection.source_format)
        self.provenance_values["original_axes"].setText(inspection.reported_axes)
        self.provenance_values["original_dimensions"].setText(
            str(inspection.reported_shape)
        )
        self._metadata_spacing_available = inspection.voxel_spacing_zyx_um is not None
        self._spacing_manually_edited = False
        if self._metadata_spacing_available:
            for field, value in zip(self.spacing_fields, inspection.voxel_spacing_zyx_um):
                field.setText(f"{value:g}")
            self.calibration_source.setText(inspection.calibration_source)
        else:
            self.expand_section("calibration")
            for field in self.spacing_fields:
                field.clear()
            self.calibration_source.setText("Manual calibration required")

    def set_lif_series(self, source_path, series) -> None:
        values = self.provenance_values
        values["filename"].setText(source_path.name)
        values["path"].setText(str(source_path))
        values["path"].setToolTip(str(source_path))
        values["format"].setText("Leica LIF")
        values["series"].setText(f"{series.index}: {series.name}")
        values["original_axes"].setText(series.dimensions)
        values["original_dimensions"].setText(str(series.shape))
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
            self.expand_section("calibration")
            for field in self.spacing_fields:
                field.clear()
            self.calibration_source.setText("Manual calibration required")

    def _mark_spacing_edited(self, text: str) -> None:
        self._spacing_manually_edited = True

    def set_dataset(self, dataset) -> None:
        if dataset is None:
            self.clear_project()
            return
        values = self.provenance_values
        values["filename"].setText(dataset.source_filename)
        values["path"].setText(str(dataset.source_path))
        values["path"].setToolTip(str(dataset.source_path))
        values["format"].setText(dataset.source_format)
        values["series"].setText(f"{dataset.series_index}: {dataset.series_name}")
        values["original_axes"].setText(dataset.reported_axes)
        values["original_dimensions"].setText(str(dataset.reported_shape))
        values["standardized"].setText(str(dataset.standardized_shape))
        values["dtype"].setText(dataset.dtype)
        values["channels"].setText(str(len(dataset.channels)))
        values["planes"].setText(str(dataset.standardized_shape[1]))
        for field, value in zip(self.spacing_fields, dataset.voxel_spacing_zyx_um):
            field.setText(f"{value:g}")
        self.calibration_source.setText(dataset.calibration_source)
        self.set_pending(False)
        self.close_button.setEnabled(True)
        self.availability_changed.emit(True)

    def clear_project(self) -> None:
        for value in self.provenance_values.values():
            value.setText("Not loaded")
            value.setToolTip("")
        self.axes_override.clear()
        self.time_index.setValue(0)
        for field in self.spacing_fields:
            field.clear()
        self.calibration_source.setText("Not loaded")
        self._metadata_spacing_available = False
        self._spacing_manually_edited = False
        self._pending = False
        self.load_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.status_label.setText("No image loaded")
        self.availability_changed.emit(False)
