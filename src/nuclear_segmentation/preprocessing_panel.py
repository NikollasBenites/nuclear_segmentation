"""Phase 2B.1 projection and non-destructive XY crop controls."""
from __future__ import annotations

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .collapsible_section import CollapsibleSection


class PreprocessingPanel(QWidget):
    generate_requested = Signal(int, str)
    draw_requested = Signal(str)
    edit_requested = Signal()
    clear_requested = Signal()
    preview_requested = Signal()
    confirm_requested = Signal()
    cancel_requested = Signal()
    reset_requested = Signal()

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        projection = CollapsibleSection("projection", "XY Projection")
        projection_form = QFormLayout()
        projection_form.setContentsMargins(10, 8, 10, 10)
        self.channel_combo = QComboBox()
        self.method_combo = QComboBox()
        self.method_combo.addItem("Maximum intensity", "maximum")
        self.method_combo.addItem("Mean intensity", "mean")
        self.generate_button = QPushButton("Generate Projection")
        self.generate_button.clicked.connect(self._request_projection)
        projection_form.addRow("Source channel", self.channel_combo)
        projection_form.addRow("Method", self.method_combo)
        projection_form.addRow(self.generate_button)
        projection.set_content_layout(projection_form)
        layout.addWidget(projection)

        selection = CollapsibleSection("crop_selection", "XY Crop Selection")
        selection_grid = QGridLayout()
        selection_grid.setContentsMargins(10, 8, 10, 10)
        self.polygon_button = QPushButton("Draw Polygon")
        self.rectangle_button = QPushButton("Draw Rectangle")
        self.edit_button = QPushButton("Edit Selection")
        self.clear_button = QPushButton("Clear Selection")
        self.polygon_button.clicked.connect(lambda: self.draw_requested.emit("polygon"))
        self.rectangle_button.clicked.connect(lambda: self.draw_requested.emit("rectangle"))
        self.edit_button.clicked.connect(self.edit_requested)
        self.clear_button.clicked.connect(self.clear_requested)
        for index, button in enumerate(
            (self.polygon_button, self.rectangle_button, self.edit_button, self.clear_button)
        ):
            selection_grid.addWidget(button, index // 2, index % 2)
        selection.set_content_layout(selection_grid)
        layout.addWidget(selection)

        crop = CollapsibleSection("crop_preview", "Crop Preview")
        crop_layout = QVBoxLayout()
        crop_layout.setContentsMargins(10, 8, 10, 10)
        self.preview_button = QPushButton("Preview Crop")
        self.confirm_button = QPushButton("Apply Crop")
        self.cancel_button = QPushButton("Cancel Preview")
        self.reset_button = QPushButton("Reset Crop")
        self.preview_button.clicked.connect(self.preview_requested)
        self.confirm_button.clicked.connect(self.confirm_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.reset_button.clicked.connect(self.reset_requested)
        for button in (
            self.preview_button,
            self.confirm_button,
            self.cancel_button,
            self.reset_button,
        ):
            crop_layout.addWidget(button)
        crop.set_content_layout(crop_layout)
        layout.addWidget(crop)

        self.status_label = QLabel("Load a project image to generate an XY projection.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        project_state.channels_changed.connect(self.set_channels)
        self.set_channels(project_state.channels)

    def set_channels(self, channels) -> None:
        selected = self.channel_combo.currentData()
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        for channel in channels:
            self.channel_combo.addItem(f"{channel.index}: {channel.name}", channel.index)
        if selected is not None:
            index = self.channel_combo.findData(selected)
            if index >= 0:
                self.channel_combo.setCurrentIndex(index)
        self.channel_combo.blockSignals(False)
        self.generate_button.setEnabled(bool(channels))

    def _request_projection(self) -> None:
        if self.channel_combo.currentData() is not None:
            self.generate_requested.emit(
                int(self.channel_combo.currentData()), self.method_combo.currentData()
            )

    def set_status(self, message: str) -> None:
        self.status_label.setText(str(message))

    def dispose(self) -> None:
        self.project_state.channels_changed.disconnect(self.set_channels)
