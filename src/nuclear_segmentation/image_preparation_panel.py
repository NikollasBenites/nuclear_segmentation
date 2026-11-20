"""Controls for independent multichannel visualization/export preparation."""
from __future__ import annotations

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .collapsible_section import CollapsibleSection
from .image_preparation import ROUTE_LABELS, validate_preparation_options
from .results_panel import ScientificResultsSection
from .ui_layout import add_stacked_combo_row


class ImagePreparationPanel(QWidget):
    prepare_requested = Signal(object, object)
    clear_requested = Signal()
    settings_changed = Signal()

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self._routes: dict[int, str] = {}
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        section = CollapsibleSection(
            "multichannel_preparation", "Step 1 — Prepare Channels"
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        note = QLabel(
            "Prepare independent channel previews for later TIFF export. This is "
            "separate from Cellpose normalization and display contrast."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.channel_list = QListWidget()
        self.channel_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.channel_list.setFixedHeight(88)
        layout.addWidget(self.channel_list)
        selection = QGridLayout()
        self.select_all_button = QPushButton("Select All")
        self.clear_selection_button = QPushButton("Clear Selection")
        selection.addWidget(self.select_all_button, 0, 0)
        selection.addWidget(self.clear_selection_button, 0, 1)
        layout.addLayout(selection)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.inspect_combo = QComboBox()
        self.route_combo = QComboBox()
        self.lower = QDoubleSpinBox()
        self.upper = QDoubleSpinBox()
        for field, value in ((self.lower, 1.0), (self.upper, 99.0)):
            field.setRange(0.0, 100.0)
            field.setDecimals(3)
            field.setValue(value)
        self.scope_combo = QComboBox()
        self.scope_combo.addItem(
            "Valid working region (recommended)", "valid_working"
        )
        self.scope_combo.addItem(
            "Working bounding box (includes exterior fill)", "working_bbox"
        )
        self.clip = QCheckBox("Clip normalized output to 0–1")
        self.clip.setChecked(True)
        add_stacked_combo_row(form, "Configure channel", self.inspect_combo)
        add_stacked_combo_row(form, "Processing route", self.route_combo)
        form.addRow("Lower percentile", self.lower)
        form.addRow("Upper percentile", self.upper)
        add_stacked_combo_row(form, "Sampling scope", self.scope_combo)
        form.addRow(self.clip)
        layout.addLayout(form)
        actions = QGridLayout()
        self.preview_button = QPushButton("Generate Prepared Previews")
        self.clear_button = QPushButton("Clear Prepared Previews")
        self.clear_button.setEnabled(False)
        actions.addWidget(self.preview_button, 0, 0)
        actions.addWidget(self.clear_button, 1, 0)
        layout.addLayout(actions)
        self.status = QLabel("Load a project image to prepare channels.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        section.set_content_layout(layout)
        outer.addWidget(section)
        self.results = ScientificResultsSection(
            "multichannel_preparation_results", "Image Preparation Results"
        )
        outer.addWidget(self.results)

        self.select_all_button.clicked.connect(self.channel_list.selectAll)
        self.clear_selection_button.clicked.connect(self.channel_list.clearSelection)
        self.inspect_combo.currentIndexChanged.connect(self._inspected_changed)
        self.route_combo.currentIndexChanged.connect(self._route_changed)
        for signal in (
            self.lower.valueChanged,
            self.upper.valueChanged,
            self.scope_combo.currentIndexChanged,
            self.clip.toggled,
        ):
            signal.connect(self._settings_changed)
        self.preview_button.clicked.connect(self._request_prepare)
        self.clear_button.clicked.connect(self.clear_requested)
        project_state.channels_changed.connect(self.set_channels)
        project_state.z_correction_changed.connect(self.refresh_routes)
        self.set_channels(project_state.channels)

    def set_channels(self, channels) -> None:
        selected = {
            int(item.data(Qt.ItemDataRole.UserRole))
            for item in self.channel_list.selectedItems()
        }
        inspected = self.inspect_combo.currentData()
        self._loading = True
        self.channel_list.clear()
        self.inspect_combo.clear()
        indexes = set()
        for channel in channels:
            indexes.add(channel.index)
            item = QListWidgetItem(f"{channel.index}: {channel.name}")
            item.setData(Qt.ItemDataRole.UserRole, channel.index)
            self.channel_list.addItem(item)
            self.inspect_combo.addItem(channel.name, channel.index)
            self._routes.setdefault(channel.index, "original")
            if channel.index in selected:
                item.setSelected(True)
        self._routes = {
            index: route for index, route in self._routes.items() if index in indexes
        }
        wanted = inspected
        if wanted is None:
            wanted = self.project_state.segmentation_channel_index
        position = self.inspect_combo.findData(wanted)
        if position >= 0:
            self.inspect_combo.setCurrentIndex(position)
        self._loading = False
        self.refresh_routes()
        enabled = bool(channels)
        self.preview_button.setEnabled(enabled)
        if not enabled:
            self.clear_button.setEnabled(False)
            self.status.setText("Load a project image to prepare channels.")

    def refresh_routes(self, *args) -> None:
        index = self.inspect_combo.currentData()
        self._loading = True
        self.route_combo.clear()
        if index is not None:
            index = int(index)
            available = ["original", "normalized"]
            if index in self.project_state.z_correction_reports:
                available[1:1] = ["z_corrected"]
                available.append("z_corrected_normalized")
            current = self._routes.get(index, "original")
            if current not in available:
                current = "original"
                self._routes[index] = current
            for route in available:
                self.route_combo.addItem(ROUTE_LABELS[route], route)
            position = self.route_combo.findData(current)
            if position >= 0:
                self.route_combo.setCurrentIndex(position)
        self._loading = False

    def _inspected_changed(self, *args) -> None:
        if not self._loading:
            self.refresh_routes()

    def _route_changed(self, *args) -> None:
        if self._loading:
            return
        index = self.inspect_combo.currentData()
        route = self.route_combo.currentData()
        if index is not None and route is not None:
            self._routes[int(index)] = str(route)
            self.settings_changed.emit()

    def _settings_changed(self, *args) -> None:
        if not self._loading:
            self.settings_changed.emit()

    def options(self) -> dict:
        return validate_preparation_options(
            {
                "lower": self.lower.value(),
                "upper": self.upper.value(),
                "scope": self.scope_combo.currentData(),
                "clip": self.clip.isChecked(),
            }
        )

    def selected_routes(self) -> dict[int, str]:
        return {
            int(item.data(Qt.ItemDataRole.UserRole)): self._routes.get(
                int(item.data(Qt.ItemDataRole.UserRole)), "original"
            )
            for item in self.channel_list.selectedItems()
        }

    def _request_prepare(self) -> None:
        routes = self.selected_routes()
        if not routes:
            self.status.setText("Select one or more channels to prepare.")
            return
        try:
            options = self.options()
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.prepare_requested.emit(routes, options)

    def set_status(self, message: str) -> None:
        self.status.setText(str(message).splitlines()[0])

    def set_results(self, text: str) -> None:
        self.results.set_results(text)

    def set_preview_available(self, available: bool) -> None:
        self.clear_button.setEnabled(bool(available))

    def reset_controls(self) -> None:
        self._routes = {channel.index: "original" for channel in self.project_state.channels}
        self.lower.setValue(1.0)
        self.upper.setValue(99.0)
        self.scope_combo.setCurrentIndex(0)
        self.clip.setChecked(True)
        self.set_channels(self.project_state.channels)
        self.set_preview_available(False)
        self.set_results("No prepared channels.")

    def dispose(self) -> None:
        self.project_state.channels_changed.disconnect(self.set_channels)
        self.project_state.z_correction_changed.disconnect(self.refresh_routes)
