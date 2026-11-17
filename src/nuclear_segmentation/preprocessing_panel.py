"""Phase 2B.1 projection and non-destructive XY crop controls."""
from __future__ import annotations

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .collapsible_section import CollapsibleSection


class PreprocessingPanel(QWidget):
    normalization_calculate_requested = Signal(object)
    normalization_apply_requested = Signal()
    normalization_reset_requested = Signal()
    normalization_settings_changed = Signal()
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

        normalization = CollapsibleSection("normalization", "Normalization")
        normalization_form = QFormLayout()
        normalization_form.setContentsMargins(10, 8, 10, 10)
        self.normalization_channel_combo = QComboBox()
        self.normalization_mode_combo = QComboBox()
        self.normalization_mode_combo.addItem(
            "Cellpose default normalization", "cellpose_default"
        )
        self.normalization_mode_combo.addItem(
            "Percentile-based normalization", "percentile"
        )
        self.normalization_mode_combo.addItem(
            "ROI-derived fixed limits", "roi_fixed"
        )
        self.normalization_mode_combo.addItem("Disabled normalization", "disabled")
        self.normalization_lower = QDoubleSpinBox()
        self.normalization_upper = QDoubleSpinBox()
        for widget, value in (
            (self.normalization_lower, 1.0),
            (self.normalization_upper, 99.0),
        ):
            widget.setRange(0.0, 100.0)
            widget.setDecimals(3)
            widget.setValue(value)
        self.normalization_norm3d = QCheckBox("Normalize across Z (norm3D)")
        self.normalization_norm3d.setChecked(True)
        normalization_form.addRow(
            "Source channel", self.normalization_channel_combo
        )
        normalization_form.addRow("Mode", self.normalization_mode_combo)
        normalization_form.addRow("Lower percentile", self.normalization_lower)
        normalization_form.addRow("Upper percentile", self.normalization_upper)
        normalization_form.addRow("norm3D", self.normalization_norm3d)
        norm3d_note = QLabel(
            "norm3D controls whether Cellpose computes normalization statistics "
            "across the image volume. It does not run Cellpose in this preview."
        )
        norm3d_note.setWordWrap(True)
        normalization_form.addRow(norm3d_note)
        self.normalization_calculate_button = QPushButton("Calculate and Preview")
        self.normalization_apply_button = QPushButton("Apply Normalization")
        self.normalization_reset_button = QPushButton("Reset Normalization")
        self.normalization_apply_button.setEnabled(False)
        normalization_form.addRow(self.normalization_calculate_button)
        normalization_form.addRow(self.normalization_apply_button)
        normalization_form.addRow(self.normalization_reset_button)
        self.normalization_status = QLabel(
            "Load a project image to configure normalization."
        )
        self.normalization_status.setWordWrap(True)
        normalization_form.addRow(self.normalization_status)
        normalization.set_content_layout(normalization_form)
        layout.addWidget(normalization)

        self.normalization_calculate_button.clicked.connect(
            self._request_normalization_calculation
        )
        self.normalization_apply_button.clicked.connect(
            self.normalization_apply_requested
        )
        self.normalization_reset_button.clicked.connect(
            self.normalization_reset_requested
        )
        for signal in (
            self.normalization_channel_combo.currentIndexChanged,
            self.normalization_mode_combo.currentIndexChanged,
            self.normalization_lower.valueChanged,
            self.normalization_upper.valueChanged,
            self.normalization_norm3d.toggled,
        ):
            signal.connect(self._normalization_setting_changed)

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
        normalization_selected = self.normalization_channel_combo.currentData()
        self.channel_combo.blockSignals(True)
        self.normalization_channel_combo.blockSignals(True)
        self.channel_combo.clear()
        self.normalization_channel_combo.clear()
        for channel in channels:
            self.channel_combo.addItem(f"{channel.index}: {channel.name}", channel.index)
            self.normalization_channel_combo.addItem(channel.name, channel.index)
        if selected is not None:
            index = self.channel_combo.findData(selected)
            if index >= 0:
                self.channel_combo.setCurrentIndex(index)
        if normalization_selected is None:
            normalization_selected = self.project_state.segmentation_channel_index
        index = self.normalization_channel_combo.findData(normalization_selected)
        if index >= 0:
            self.normalization_channel_combo.setCurrentIndex(index)
        self.channel_combo.blockSignals(False)
        self.normalization_channel_combo.blockSignals(False)
        self.generate_button.setEnabled(bool(channels))
        self.normalization_calculate_button.setEnabled(bool(channels))
        if not channels:
            self.normalization_apply_button.setEnabled(False)
            self.normalization_status.setText(
                "Load a project image to configure normalization."
            )

    def normalization_options(self) -> dict:
        lower = float(self.normalization_lower.value())
        upper = float(self.normalization_upper.value())
        if not 0 <= lower < upper <= 100:
            raise ValueError("Percentiles must satisfy 0 <= lower < upper <= 100.")
        channel_index = self.normalization_channel_combo.currentData()
        if channel_index is None:
            raise ValueError("Select a normalization source channel.")
        return {
            "channel_index": int(channel_index),
            "mode": str(self.normalization_mode_combo.currentData()),
            "lower": lower,
            "upper": upper,
            "norm3D": bool(self.normalization_norm3d.isChecked()),
        }

    def _request_normalization_calculation(self) -> None:
        try:
            options = self.normalization_options()
        except ValueError as error:
            self.set_normalization_status(str(error), preview_ready=False)
            return
        self.normalization_calculate_requested.emit(options)

    def _normalization_setting_changed(self, *args) -> None:
        self.normalization_apply_button.setEnabled(False)
        if self.project_state.dataset_ready:
            self.normalization_status.setText(
                "Normalization settings changed. Recalculate the preview before applying."
            )
        self.normalization_settings_changed.emit()

    def set_normalization_status(self, message: str, *, preview_ready=False) -> None:
        self.normalization_status.setText(str(message))
        self.normalization_apply_button.setEnabled(bool(preview_ready))

    def reset_normalization_controls(self) -> None:
        self.normalization_mode_combo.setCurrentIndex(
            self.normalization_mode_combo.findData("cellpose_default")
        )
        self.normalization_lower.setValue(1.0)
        self.normalization_upper.setValue(99.0)
        self.normalization_norm3d.setChecked(True)
        self.set_channels(self.project_state.channels)
        self.set_normalization_status(
            "Cellpose default normalization selected. Calculate a preview to inspect intensities.",
            preview_ready=False,
        )

    def _request_projection(self) -> None:
        if self.channel_combo.currentData() is not None:
            self.generate_requested.emit(
                int(self.channel_combo.currentData()), self.method_combo.currentData()
            )

    def set_status(self, message: str) -> None:
        self.status_label.setText(str(message))

    def dispose(self) -> None:
        self.project_state.channels_changed.disconnect(self.set_channels)
