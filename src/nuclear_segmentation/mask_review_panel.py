"""Application-owned view of existing mask selection and segmentation state."""
from __future__ import annotations

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .collapsible_section import CollapsibleSection


class MaskReviewPanel(QWidget):
    """Present review state without owning selection or scientific decisions."""

    show_all_requested = Signal()
    show_selected_requested = Signal()
    clear_selection_requested = Signal()
    calculate_measurements_requested = Signal()
    filters_changed = Signal()
    accept_roi_requested = Signal()

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self._result = None
        self._result_current = False
        self._selected_id = None
        self._selected_is_raw = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        section = CollapsibleSection("review_selection", "Selection")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.mask_id = self._value("Selected Mask ID: —")
        self.active_layer = self._value("Active segmentation layer: —")
        self.segmentation_status = self._value("Segmentation: missing")
        self.mask_count = self._value("Detected masks: —")
        for widget in (
            self.mask_id, self.active_layer, self.segmentation_status,
            self.mask_count,
        ):
            layout.addWidget(widget)
        section.set_content_layout(layout)
        root.addWidget(section)

        section = CollapsibleSection("scientific_filters", "Scientific Filtering")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.calculate_button = QPushButton("Calculate Measurements")
        self.calculate_button.clicked.connect(self.calculate_measurements_requested)
        self.measurement_status = self._value("Measurements have not been calculated.")
        layout.addWidget(self.calculate_button)
        layout.addWidget(self.measurement_status)
        form = QFormLayout()
        self.minimum_volume = QDoubleSpinBox(); self.minimum_volume.setRange(0, 1e12)
        self.minimum_sphericity = QDoubleSpinBox(); self.minimum_sphericity.setRange(0, 1); self.minimum_sphericity.setValue(.45)
        self.first_tissue_z = QSpinBox(); self.last_tissue_z = QSpinBox()
        self.z_guard_um = QDoubleSpinBox(); self.z_guard_um.setRange(0, 1e9)
        self.z_guard_mode = QComboBox(); self.z_guard_mode.addItems(["none", "first", "last", "both"]); self.z_guard_mode.setCurrentText("first")
        form.addRow("Minimum volume (µm³)", self.minimum_volume)
        form.addRow("Minimum sphericity", self.minimum_sphericity)
        form.addRow("First tissue Z", self.first_tissue_z)
        form.addRow("Last tissue Z", self.last_tissue_z)
        form.addRow("Z guard (µm)", self.z_guard_um)
        form.addRow("Z guard mode", self.z_guard_mode)
        layout.addLayout(form)
        self.intensity_combination = QComboBox(); self.intensity_combination.addItems(["all", "any"])
        layout.addWidget(QLabel("Channel intensity filters (enabled channels combined by)"))
        layout.addWidget(self.intensity_combination)
        self.intensity_layout = QVBoxLayout(); layout.addLayout(self.intensity_layout)
        self.intensity_controls = {}
        self.perinuclear_combination = QComboBox(); self.perinuclear_combination.addItems(["all", "any"])
        layout.addWidget(QLabel("Perinuclear marker filters (combined by)"))
        layout.addWidget(self.perinuclear_combination)
        self.perinuclear_layout = QVBoxLayout(); layout.addLayout(self.perinuclear_layout)
        self.perinuclear_controls = {}
        self.filter_summary = self._value("Live filtering is unavailable.")
        layout.addWidget(self.filter_summary)
        section.set_content_layout(layout)
        root.addWidget(section)

        section = CollapsibleSection("scientific_roi", "Scientific MNTB ROI")
        layout = QVBoxLayout(); layout.setContentsMargins(10, 8, 10, 10)
        self.roi_status = self._value("Provisional ROI unavailable.")
        self.accept_roi_button = QPushButton("Accept Reviewed MNTB ROI")
        self.accept_roi_button.setEnabled(False)
        self.accept_roi_button.clicked.connect(self.accept_roi_requested)
        layout.addWidget(self.roi_status); layout.addWidget(self.accept_roi_button)
        section.set_content_layout(layout); root.addWidget(section)
        for widget in (self.minimum_volume, self.minimum_sphericity, self.first_tissue_z,
                       self.last_tissue_z, self.z_guard_um):
            widget.valueChanged.connect(self.filters_changed)
        for widget in (self.z_guard_mode, self.intensity_combination, self.perinuclear_combination):
            widget.currentTextChanged.connect(self.filters_changed)

        section = CollapsibleSection(
            "selected_mask_information", "Selected Mask Information"
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.mask_type = self._value("Mask type: —")
        self.measurements = self._value(
            "Scientific measurements are not available in this milestone."
        )
        layout.addWidget(self.mask_type)
        layout.addWidget(self.measurements)
        section.set_content_layout(layout)
        root.addWidget(section)

        section = CollapsibleSection("review_visualization", "Visualization")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.show_all_button = QPushButton("Show all masks")
        self.show_selected_button = QPushButton("Show selected mask only")
        self.clear_button = QPushButton("Clear mask selection")
        self.show_all_button.clicked.connect(self.show_all_requested)
        self.show_selected_button.clicked.connect(self.show_selected_requested)
        self.clear_button.clicked.connect(self.clear_selection_requested)
        for button in (
            self.show_all_button, self.show_selected_button, self.clear_button
        ):
            layout.addWidget(button)
        self.visualization_status = self._value(
            "Select masks with the existing Review / Select tool."
        )
        layout.addWidget(self.visualization_status)
        section.set_content_layout(layout)
        root.addWidget(section)

        section = CollapsibleSection("manual_review_status", "Review Status")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.review_status = self._value("No segmentation is available for review.")
        layout.addWidget(self.review_status)
        self.keep_button = QPushButton("Keep selected mask")
        self.exclude_button = QPushButton("Exclude selected mask")
        self.automatic_button = QPushButton("Use automatic filters")
        self.undo_button = QPushButton("Undo last review decision")
        for button in (
            self.keep_button, self.exclude_button, self.automatic_button,
            self.undo_button,
        ):
            button.setEnabled(False)
            button.setToolTip(
                "Requires integrated measurements, automatic filters, and a "
                "reviewed scientific ROI."
            )
            layout.addWidget(button)
        self.review_safeguard = self._value(
            "Manual keep/exclude is disabled until filtering, eligibility, guard zones, "
            "and reviewed ROI state can be supplied to the existing ManualReview logic."
        )
        layout.addWidget(self.review_safeguard)
        section.set_content_layout(layout)
        root.addWidget(section)
        root.addStretch(1)

        project_state.segmentation_result_changed.connect(self.set_result)
        self.set_result(
            project_state.segmentation_result,
            project_state.segmentation_is_current,
        )

    @staticmethod
    def _value(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        return label

    def set_selection(self, layer, mask_id) -> None:
        metadata = {} if layer is None else (getattr(layer, "metadata", {}) or {})
        self._selected_id = None if mask_id is None else int(mask_id)
        self._selected_is_raw = bool(
            metadata.get("nucytoseg_raw_segmentation")
            or metadata.get("nucytoseg_filtered_segmentation")
        )
        self.mask_id.setText(
            "Selected Mask ID: —"
            if self._selected_id is None
            else f"Selected Mask ID: {self._selected_id}"
        )
        self.active_layer.setText(
            "Active segmentation layer: —"
            if layer is None
            else f"Active segmentation layer: {layer.name}"
        )
        role = str(metadata.get("mask_type", "")).strip()
        self.mask_type.setText(f"Mask type: {role or 'unclassified'}")
        self.update_selected_properties(self._selected_id)
        self._refresh_actions()

    def configure_filtering(self, result, current=True):
        if result is None:
            self.measurement_status.setText("Measurements have not been calculated.")
            self.accept_roi_button.setEnabled(False)
            return
        table = result["properties"]
        self.measurement_status.setText(f"Measurements available for {len(table):,} masks." + ("" if current else " Results are outdated."))
        nz = int(result["raw_mask_shape"][0])
        for control in (self.first_tissue_z, self.last_tissue_z):
            control.blockSignals(True); control.setRange(0, nz - 1); control.blockSignals(False)
        settings = result.get("settings") or {}
        values = (
            (self.minimum_volume, settings.get("minimum_volume_um3", 0.0)),
            (self.minimum_sphericity, settings.get("minimum_sphericity", .45)),
            (self.first_tissue_z, settings.get("first_tissue_z", 0)),
            (self.last_tissue_z, settings.get("last_tissue_z", nz - 1)),
            (self.z_guard_um, settings.get("z_guard_um", 0.0)),
        )
        for control, value in values:
            control.blockSignals(True); control.setValue(value); control.blockSignals(False)
        for control, value in (
            (self.z_guard_mode, settings.get("z_guard_mode", "first")),
            (self.intensity_combination, settings.get("intensity_combination", "all")),
            (self.perinuclear_combination, settings.get("perinuclear_combination", "all")),
        ):
            control.blockSignals(True); control.setCurrentText(value); control.blockSignals(False)
        self._rebuild_filter_controls(result)
        self.roi_status.setText("Reviewed and accepted." if result.get("roi_reviewed") else "Provisional 3D ROI — inspect and accept before validated counting eligibility.")
        self.accept_roi_button.setEnabled(bool(current))
        self.update_filter_summary(result, current)

    def _rebuild_filter_controls(self, result):
        for group, *_ in self.intensity_controls.values(): group.deleteLater()
        for group, *_ in self.perinuclear_controls.values(): group.deleteLater()
        self.intensity_controls = {}; self.perinuclear_controls = {}
        for item in result.get("intensity_definitions", []):
            group = QGroupBox(f"{item['name']} (channel {item['index'] + 1})")
            form = QFormLayout(group); enabled = QCheckBox("Enable"); enabled.setChecked(item.get("enabled", True))
            low = QDoubleSpinBox(); high = QDoubleSpinBox()
            for control in (low, high): control.setRange(-1e15, 1e15); control.setDecimals(4)
            low.setValue(item["minimum"]); high.setValue(item["maximum"])
            form.addRow(enabled); form.addRow("Minimum", low); form.addRow("Maximum", high)
            enabled.toggled.connect(self.filters_changed); low.valueChanged.connect(self.filters_changed); high.valueChanged.connect(self.filters_changed)
            self.intensity_layout.addWidget(group); self.intensity_controls[item["index"]] = (group, enabled, low, high, item)
        for item in result.get("perinuclear_definitions", []):
            group = QGroupBox(item["name"]); form = QFormLayout(group)
            minimum = QDoubleSpinBox(); minimum.setRange(0, 1); minimum.setSingleStep(.01); minimum.setValue(item["minimum_positive_fraction"])
            form.addRow("Minimum positive fraction", minimum); minimum.valueChanged.connect(self.filters_changed)
            self.perinuclear_layout.addWidget(group); self.perinuclear_controls[item["name"]] = (group, minimum, item)

    def filter_settings(self):
        intensity = {}
        for _, enabled, low, high, item in self.intensity_controls.values():
            if enabled.isChecked(): intensity[item["name"]] = {"column": item["column"], "minimum": low.value(), "maximum": high.value()}
        perinuclear = {name: {"column": item["column"], "minimum_positive_fraction": control.value()} for name, (_, control, item) in self.perinuclear_controls.items()}
        return {"minimum_volume_um3": self.minimum_volume.value(), "minimum_sphericity": self.minimum_sphericity.value(),
                "first_tissue_z": self.first_tissue_z.value(), "last_tissue_z": self.last_tissue_z.value(),
                "z_guard_um": self.z_guard_um.value(), "z_guard_mode": self.z_guard_mode.currentText(), "z_counting_rule": "directional_entry_v1",
                "perinuclear_combination": self.perinuclear_combination.currentText(), "perinuclear_filters": perinuclear,
                "intensity_combination": self.intensity_combination.currentText(), "intensity_filters": intensity}

    def update_filter_summary(self, result, current=True):
        qualifier = "validated" if result.get("roi_reviewed") else "preliminary — ROI review required"
        if not current: qualifier = "outdated"
        self.filter_summary.setText(f"Retained masks: {len(result.get('retained_ids', ())):,} / {len(result.get('properties', ())):,} ({qualifier})")

    def update_selected_properties(self, mask_id):
        result = self.project_state.filtering_result
        if result is None or mask_id is None or int(mask_id) not in result["properties"].index:
            self.measurements.setText("Scientific measurements are not available for this selection."); return
        row = result["properties"].loc[int(mask_id)]
        lines = [f"Volume: {row['volume_um3']:.4g} µm³", f"Sphericity: {row['sphericity']:.4g}",
                 "Automatic status: " + ("retained" if bool(row.get("automatic_keep")) else "excluded")]
        if row.get("automatic_failure_reasons", ""): lines.append("Exclusion reasons: " + str(row["automatic_failure_reasons"]))
        lines.append("Z guard excluded: " + ("yes" if bool(row.get("excluded_by_z_guard")) else "no"))
        for key, value in row.items():
            if ("intensity" in key or "positive_fraction" in key) and hasattr(value, "__float__"): lines.append(f"{key}: {float(value):.4g}")
        self.measurements.setText("\n".join(lines))

    def set_result(self, result, current: bool) -> None:
        self._result = result
        self._result_current = bool(current)
        if result is None:
            self.segmentation_status.setText("Segmentation: missing")
            self.mask_count.setText("Detected masks: —")
            self.review_status.setText("No segmentation is available for review.")
        elif current:
            self.segmentation_status.setText("Segmentation: available and current")
            self.mask_count.setText(
                f"Detected masks: {int(result['object_count']):,}"
            )
            self.review_status.setText(
                "Raw masks are ready for visual review. Scientific keep/exclude "
                "decisions remain disabled until their prerequisites are integrated."
            )
        else:
            self.segmentation_status.setText("Segmentation: outdated")
            self.mask_count.setText(
                f"Detected masks: {int(result['object_count']):,}"
            )
            self.review_status.setText(
                "Previous masks remain visible for inspection but are outdated. "
                "Rerun segmentation before further scientific review."
            )
        self._refresh_actions()
        self.calculate_button.setEnabled(result is not None and current)

    def set_visualization_status(self, message: str) -> None:
        self.visualization_status.setText(str(message))

    def _refresh_actions(self) -> None:
        available = self._result is not None
        selected = available and self._selected_is_raw and self._selected_id is not None
        self.show_all_button.setEnabled(available)
        self.show_selected_button.setEnabled(selected)
        self.clear_button.setEnabled(self._selected_id is not None)

    def dispose(self) -> None:
        self.project_state.segmentation_result_changed.disconnect(self.set_result)
