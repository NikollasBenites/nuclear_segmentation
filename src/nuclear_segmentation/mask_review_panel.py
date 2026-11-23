"""Application-owned view of existing mask selection and segmentation state."""
from __future__ import annotations

import numpy as np
from qtpy.QtCore import QSignalBlocker, Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from .collapsible_section import CollapsibleSection
from .ui_layout import add_stacked_combo_row


class SyncedNumericControl(QWidget):
    """High-DPI slider plus exact floating-point editor."""

    valueChanged = Signal(float)
    _STEPS = 1000

    def __init__(self, minimum=0.0, maximum=1.0, decimals=4, parent=None):
        super().__init__(parent)
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._syncing = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, self._STEPS)
        self.editor = QDoubleSpinBox()
        self.editor.setDecimals(int(decimals))
        self.editor.setKeyboardTracking(False)
        self.editor.setMinimumWidth(88)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.editor)
        self.setRange(minimum, maximum)
        self.slider.valueChanged.connect(self._from_slider)
        self.editor.valueChanged.connect(self._from_editor)

    def setRange(self, minimum, maximum):
        minimum, maximum = float(minimum), float(maximum)
        if not np.isfinite([minimum, maximum]).all() or maximum <= minimum:
            maximum = minimum + 1.0
        value = np.clip(self.value(), minimum, maximum)
        self._minimum, self._maximum = minimum, maximum
        self.editor.setRange(minimum, maximum)
        self.editor.setSingleStep(max((maximum - minimum) / self._STEPS, 10 ** -self.editor.decimals()))
        self.setValue(value)

    def setValue(self, value):
        value = float(np.clip(float(value), self._minimum, self._maximum))
        self._syncing = True
        self.editor.setValue(value)
        position = round((value - self._minimum) / (self._maximum - self._minimum) * self._STEPS)
        self.slider.setValue(position)
        self._syncing = False

    def value(self):
        return float(self.editor.value()) if hasattr(self, "editor") else self._minimum

    def _from_slider(self, position):
        if self._syncing:
            return
        value = self._minimum + (self._maximum - self._minimum) * position / self._STEPS
        self._syncing = True
        self.editor.setValue(value)
        self._syncing = False
        self.valueChanged.emit(self.value())

    def _from_editor(self, value):
        if self._syncing:
            return
        self._syncing = True
        position = round((float(value) - self._minimum) / (self._maximum - self._minimum) * self._STEPS)
        self.slider.setValue(position)
        self._syncing = False
        self.valueChanged.emit(float(value))


class PerinuclearMarkerRow(QGroupBox):
    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, channels, record=None, parent=None):
        super().__init__(parent)
        record = dict(record or {})
        form = QFormLayout(self)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.channel = QComboBox()
        for item in channels:
            self.channel.addItem(f"{item.name} (channel {item.index + 1})", int(item.index))
        selected = self.channel.findData(record.get("channel_index"))
        if selected >= 0:
            self.channel.setCurrentIndex(selected)
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(bool(record.get("enabled", True)))
        self.inner = QDoubleSpinBox(); self.inner.setRange(0, 1e6); self.inner.setDecimals(3)
        self.outer = QDoubleSpinBox(); self.outer.setRange(0.001, 1e6); self.outer.setDecimals(3)
        self.inner.setValue(float(record.get("ring_inner_um", .5)))
        self.outer.setValue(float(record.get("ring_outer_um", 3.0)))
        self.manual_threshold = QCheckBox("Manual pixel threshold")
        threshold = record.get("pixel_threshold")
        self.manual_threshold.setChecked(threshold is not None)
        self.threshold = QDoubleSpinBox(); self.threshold.setRange(-1e30, 1e30); self.threshold.setDecimals(6)
        self.threshold.setValue(0.0 if threshold is None else float(threshold)); self.threshold.setEnabled(threshold is not None)
        self.fraction = SyncedNumericControl(0, 1, 6)
        self.fraction.setValue(float(record.get("initial_min_positive_fraction", 0.1)))
        self.remove_button = QPushButton("Remove Marker")
        add_stacked_combo_row(form, "Source channel", self.channel)
        form.addRow(self.enabled)
        form.addRow("Inner shell distance (µm)", self.inner); form.addRow("Outer shell distance (µm)", self.outer)
        form.addRow(self.manual_threshold, self.threshold); form.addRow("Minimum positive fraction", self.fraction)
        form.addRow(self.remove_button)
        self.setToolTip("Shell distances are physical micrometres. Automatic threshold uses the existing resolver.")
        self.manual_threshold.toggled.connect(self.threshold.setEnabled)
        for signal in (self.channel.currentIndexChanged, self.enabled.toggled,
                       self.inner.valueChanged, self.outer.valueChanged,
                       self.manual_threshold.toggled, self.threshold.valueChanged,
                       self.fraction.valueChanged):
            signal.connect(self.changed)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))

    def record(self):
        return {
            "channel_index": int(self.channel.currentData()),
            "enabled": self.enabled.isChecked(),
            "ring_inner_um": self.inner.value(), "ring_outer_um": self.outer.value(),
            "pixel_threshold": self.threshold.value() if self.manual_threshold.isChecked() else None,
            "initial_min_positive_fraction": self.fraction.value(),
        }


class MaskReviewPanel(QWidget):
    """Present review state without owning selection or scientific decisions."""

    show_raw_requested = Signal()
    show_all_requested = Signal()
    show_selected_requested = Signal()
    clear_selection_requested = Signal()
    calculate_measurements_requested = Signal()
    filters_changed = Signal()
    filters_edited = Signal()
    accept_roi_requested = Signal()
    marker_configuration_changed = Signal(object)

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self._result = None
        self._result_current = False
        self._selected_id = None
        self._selected_identity = None
        self._selected_is_raw = False
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(120)
        self._filter_timer.timeout.connect(self.filters_changed)
        self.marker_rows = []
        self.pages = {key: QWidget() for key in ("filtering", "roi_counting", "mask_editing")}
        filtering, counting, editing = (
            QVBoxLayout(page) for page in self.pages.values()
        )
        for page_layout in (filtering, counting, editing):
            page_layout.setContentsMargins(0, 8, 0, 0)
            page_layout.setSpacing(8)

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
        editing.addWidget(section)

        section = CollapsibleSection("scientific_filters", "Scientific Filtering")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.calculate_button = QPushButton("Calculate Measurements")
        self.calculate_button.clicked.connect(self.calculate_measurements_requested)
        self.measurement_status = self._value("Measurements have not been calculated.")
        layout.addWidget(self.calculate_button)
        layout.addWidget(self.measurement_status)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.minimum_volume = SyncedNumericControl(0, 1, 6)
        self.minimum_sphericity = SyncedNumericControl(0, 1, 6); self.minimum_sphericity.setValue(.45)
        self.first_tissue_z = QSpinBox(); self.last_tissue_z = QSpinBox()
        self.z_guard_um = SyncedNumericControl(0, 1, 6)
        self.z_guard_mode = QComboBox(); self.z_guard_mode.addItems(["none", "first", "last", "both"]); self.z_guard_mode.setCurrentText("first")
        form.addRow("Minimum volume (µm³)", self.minimum_volume)
        form.addRow("Minimum sphericity", self.minimum_sphericity)
        layout.addLayout(form)
        self.intensity_combination = QComboBox(); self.intensity_combination.addItems(["all", "any"])
        self.intensity_master = QCheckBox("Enable Intensity Filtering")
        self.intensity_label = self._value("Channel intensity filters (enabled channels combined by)")
        layout.addWidget(self.intensity_master)
        layout.addWidget(self.intensity_label)
        layout.addWidget(self.intensity_combination)
        self.intensity_layout = QVBoxLayout(); layout.addLayout(self.intensity_layout)
        self.intensity_controls = {}
        self.perinuclear_combination = QComboBox(); self.perinuclear_combination.addItems(["all", "any"])
        self.perinuclear_label = self._value("Perinuclear marker filters (combined by)")
        layout.addWidget(self.perinuclear_label)
        layout.addWidget(self.perinuclear_combination)
        self.perinuclear_layout = QVBoxLayout(); layout.addLayout(self.perinuclear_layout)
        self.perinuclear_controls = {}
        self.filter_summary = self._value("Live filtering is unavailable.")
        layout.addWidget(self.filter_summary)
        section.set_content_layout(layout)
        filtering.addWidget(section)

        section = CollapsibleSection(
            "perinuclear_marker_configuration",
            "Perinuclear Marker Configuration",
        )
        self.marker_configuration_layout = QVBoxLayout()
        self.marker_configuration_layout.setContentsMargins(10, 8, 10, 10)
        self.marker_configuration_note = self._value(
            "No perinuclear markers configured. Add a marker and select any source channel."
        )
        self.add_marker_button = QPushButton("Add Perinuclear Marker")
        self.add_marker_button.clicked.connect(self.add_marker)
        self.marker_configuration_layout.addWidget(self.marker_configuration_note)
        self.marker_configuration_layout.addWidget(self.add_marker_button)
        self.marker_section = section
        section.set_content_layout(self.marker_configuration_layout)
        filtering.addWidget(section)

        section = CollapsibleSection("scientific_roi", "Scientific MNTB ROI")
        layout = QVBoxLayout(); layout.setContentsMargins(10, 8, 10, 10)
        layout.addWidget(self._value("Show the MNTB ROI in Layers to review it in the viewer."))
        self.roi_status = self._value("Provisional ROI unavailable.")
        self.accept_roi_button = QPushButton("Accept Reviewed MNTB ROI")
        self.accept_roi_button.setEnabled(False)
        self.accept_roi_button.clicked.connect(self.accept_roi_requested)
        layout.addWidget(self.roi_status); layout.addWidget(self.accept_roi_button)
        section.set_content_layout(layout)
        counting.addWidget(section)

        section = CollapsibleSection("roi_counting", "Counting Settings")
        form = QFormLayout()
        form.setContentsMargins(10, 8, 10, 10)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("First tissue Z", self.first_tissue_z)
        form.addRow("Last tissue Z", self.last_tissue_z)
        form.addRow("Z guard (µm)", self.z_guard_um)
        form.addRow("Z guard mode", self.z_guard_mode)
        self.counting_error = self._value("")
        self.counting_error.hide()
        form.addRow(self.counting_error)
        section.set_content_layout(form)
        counting.addWidget(section)
        for widget in (self.minimum_volume, self.minimum_sphericity, self.first_tissue_z,
                       self.last_tissue_z, self.z_guard_um):
            widget.valueChanged.connect(self._schedule_filters)
        for widget in (self.z_guard_mode, self.intensity_combination, self.perinuclear_combination):
            widget.currentTextChanged.connect(self._schedule_filters)
        self.intensity_master.toggled.connect(self._intensity_master_changed)

        section = CollapsibleSection(
            "selected_mask_information", "Selected Mask Information"
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.information_section = section
        self.mask_type = self._value("Mask type: —")
        self.measurements = self._value(
            "Scientific measurements are not available in this milestone."
        )
        layout.addWidget(self.mask_type)
        layout.addWidget(self.measurements)
        section.set_content_layout(layout)
        editing.addWidget(section)

        section = CollapsibleSection("review_visualization", "Visualization")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.show_raw_button = QPushButton("Show Raw Masks")
        self.show_all_button = QPushButton("Show All Masks")
        self.show_selected_button = QPushButton("Show Selected Mask Only")
        self.clear_button = QPushButton("Clear mask selection")
        self.show_raw_button.clicked.connect(self.show_raw_requested)
        self.show_all_button.clicked.connect(self.show_all_requested)
        self.show_selected_button.clicked.connect(self.show_selected_requested)
        self.clear_button.clicked.connect(self.clear_selection_requested)
        for button in (
            self.show_raw_button, self.show_all_button, self.show_selected_button, self.clear_button
        ):
            layout.addWidget(button)
        self.visualization_status = self._value(
            "Select masks with the existing Review / Select tool."
        )
        layout.addWidget(self.visualization_status)
        section.set_content_layout(layout)
        editing.addWidget(section)

        section = CollapsibleSection("manual_review_status", "Review Status")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.review_status = self._value("No segmentation is available for review.")
        layout.addWidget(self.review_status)
        section.set_content_layout(layout)
        editing.addWidget(section)
        for page_layout in (filtering, counting, editing):
            page_layout.addStretch(1)

        project_state.segmentation_result_changed.connect(self.set_result)
        project_state.channels_changed.connect(self._channels_changed)
        project_state.perinuclear_markers_changed.connect(self.set_marker_configuration)
        self.set_marker_configuration(project_state.perinuclear_markers)
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
        self._selected_is_raw = self.project_state.owns_mask_layer(layer) and bool(
            metadata.get("nucytoseg_raw_segmentation")
            or metadata.get("nucytoseg_edited_segmentation")
            or metadata.get("nucytoseg_filtered_segmentation")
        )
        self._selected_identity = (
            self.project_state.mask_identity(self._selected_id)
            if self._selected_is_raw and self._selected_id is not None else None
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

    def show_context_properties(self, layer, label):
        state = self.project_state
        voxels = sum(np.count_nonzero(plane == label) for plane in layer.data)
        text = f"Mask {label} · {voxels:,} voxels in {layer.name}"
        result = state.segmentation_result
        raw_with_edits = layer.metadata.get("nucytoseg_raw_segmentation") and result.get("edited_masks") is not None
        if (self._selected_identity is not None and state.filtering_is_current and not raw_with_edits
                and label in state.filtering_result["properties"].index):
            text += "\n" + self.measurements.text()
        self.measurements.setText(text)
        self.information_section.set_expanded(True)
        scroll = self.information_section.parentWidget()
        while scroll is not None and not isinstance(scroll, QScrollArea):
            scroll = scroll.parentWidget()
        if scroll is not None:
            scroll.ensureWidgetVisible(self.measurements)

    def configure_filtering(self, result, current=True):
        role = self.project_state.active_session.scientific_role
        nuclear = role == "nuclear"
        self.marker_section.setVisible(nuclear)
        self.pages["roi_counting"].setEnabled(nuclear)
        self.calculate_button.setText(f"Calculate {role.title()} Measurements")
        if result is None:
            self.measurement_status.setText("Measurements have not been calculated.")
            self.accept_roi_button.setEnabled(False)
            self.counting_error.hide()
            self.filter_summary.setText("Live filtering is unavailable.")
            self.roi_status.setText("Provisional ROI unavailable.")
            for control, value in (
                (self.minimum_volume, 0), (self.minimum_sphericity, .45),
                (self.first_tissue_z, 0), (self.last_tissue_z, 0), (self.z_guard_um, 0),
            ):
                with QSignalBlocker(control):
                    control.setValue(value)
            for control, value in (
                (self.z_guard_mode, "first"), (self.intensity_combination, "all"),
                (self.perinuclear_combination, "all"),
            ):
                with QSignalBlocker(control):
                    control.setCurrentText(value)
            self._rebuild_filter_controls({})
            return
        table = result["properties"]
        self.measurement_status.setText(f"Measurements available for {len(table):,} {role} objects." + ("" if current else " Results are outdated."))
        nz = int(result["raw_mask_shape"][0])
        for control in (self.first_tissue_z, self.last_tissue_z):
            control.blockSignals(True); control.setRange(0, nz - 1); control.blockSignals(False)
        settings = result.get("settings") or {}
        volume_high = max(1.0, settings.get("minimum_volume_um3", 0.0), float(np.ceil(table["volume_um3"].max())))
        self.minimum_volume.setRange(0.0, volume_high)
        z_high = max(1.0, settings.get("z_guard_um", 0.0),
                     (nz - 1) * float(self.project_state.voxel_spacing_zyx_um[0]))
        self.z_guard_um.setRange(0.0, z_high)
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
        if nuclear:
            self.roi_status.setText("Reviewed and accepted." if result.get("roi_reviewed") else "Provisional 3D ROI — inspect and accept before validated counting eligibility.")
        else:
            self.roi_status.setText("Counting ROI is available for nuclear sessions.")
        self.accept_roi_button.setEnabled(nuclear and self._result_current)
        self.update_filter_summary(result, current)

    def _rebuild_filter_controls(self, result):
        for group, *_ in self.intensity_controls.values(): group.deleteLater()
        for group, *_ in self.perinuclear_controls.values(): group.deleteLater()
        self.intensity_controls = {}; self.perinuclear_controls = {}
        definitions = result.get("perinuclear_definitions", [])
        intensity_definitions = result.get("intensity_definitions", [])
        result_settings = result.get("settings") or {}
        active_names = set(result_settings.get("intensity_filters", {}))
        self.intensity_master.blockSignals(True)
        self.intensity_master.setChecked(
            bool(result_settings.get("intensity_filtering_enabled", active_names))
        )
        self.intensity_master.blockSignals(False)
        for item in intensity_definitions:
            group = QGroupBox(f"{item['name']} (channel {item['index'] + 1})")
            form = QFormLayout(group); enabled = QCheckBox("Enable"); enabled.setChecked(item["name"] in active_names)
            low = SyncedNumericControl(item["minimum"], item["maximum"], 9)
            high = SyncedNumericControl(item["minimum"], item["maximum"], 9)
            limits = result_settings.get("intensity_filters", {}).get(item["name"], item)
            low.setValue(limits["minimum"]); high.setValue(limits["maximum"])
            form.addRow(enabled); form.addRow("Minimum", low); form.addRow("Maximum", high)
            enabled.toggled.connect(self._intensity_channel_changed); low.valueChanged.connect(self._schedule_filters); high.valueChanged.connect(self._schedule_filters)
            self.intensity_layout.addWidget(group); self.intensity_controls[item["index"]] = (group, enabled, low, high, item)
        for item in definitions:
            group = QGroupBox(item["name"]); form = QFormLayout(group)
            limits = result_settings.get("perinuclear_filters", {}).get(item["name"], item)
            minimum = SyncedNumericControl(0, 1, 6); minimum.setValue(limits["minimum_positive_fraction"])
            form.addRow("Minimum positive fraction", minimum); minimum.valueChanged.connect(self._schedule_filters)
            self.perinuclear_layout.addWidget(group); self.perinuclear_controls[item["name"]] = (group, minimum, item)
        self._update_optional_filter_visibility()

    def filter_settings(self):
        intensity = {}
        if self.intensity_master.isChecked():
            for _, enabled, low, high, item in self.intensity_controls.values():
                if enabled.isChecked(): intensity[item["name"]] = {"column": item["column"], "minimum": low.value(), "maximum": high.value()}
        settings = {
            "minimum_volume_um3": self.minimum_volume.value(),
            "minimum_sphericity": self.minimum_sphericity.value(),
            "intensity_combination": self.intensity_combination.currentText(),
            "intensity_filtering_enabled": self.intensity_master.isChecked(),
            "intensity_filters": intensity,
        }
        if self.project_state.active_session.scientific_role == "nuclear":
            settings.update(
                first_tissue_z=self.first_tissue_z.value(), last_tissue_z=self.last_tissue_z.value(),
                z_guard_um=self.z_guard_um.value(), z_guard_mode=self.z_guard_mode.currentText(),
                z_counting_rule="directional_entry_v1",
                perinuclear_combination=self.perinuclear_combination.currentText(),
                perinuclear_filters={
                    name: {"column": item["column"], "minimum_positive_fraction": control.value()}
                    for name, (_, control, item) in self.perinuclear_controls.items()
                },
            )
        return settings

    def intensity_control_settings(self):
        return {
            index: (enabled.isChecked(), low.value(), high.value())
            for index, (_, enabled, low, high, _) in self.intensity_controls.items()
        }

    def restore_intensity_controls(self, settings):
        for index, values in settings.items():
            _, enabled, low, high, _ = self.intensity_controls[index]
            for widget, method, value in zip(
                (enabled, low, high), (enabled.setChecked, low.setValue, high.setValue), values, strict=True
            ):
                blocked = widget.blockSignals(True)
                method(value)
                widget.blockSignals(blocked)
        self._update_optional_filter_visibility()

    def update_filter_summary(self, result, current=True):
        self.counting_error.hide()
        qualifier = "validated" if result.get("roi_reviewed") else "preliminary — ROI review required"
        if self.project_state.active_session.scientific_role == "cytoplasmic":
            qualifier = "cytoplasmic objects"
        if not current: qualifier = "outdated"
        retained = len(result.get("retained_ids", ()))
        excluded = len(result.get("properties", ())) - retained
        self.filter_summary.setText(f"Retained masks: {retained:,} · Excluded masks: {excluded:,} ({qualifier})")

    def show_filter_error(self, message):
        self.filter_summary.setText(message)
        self.counting_error.setText(message)
        self.counting_error.show()

    def update_selected_properties(self, mask_id):
        result = self.project_state.filtering_result
        if (
            result is None or mask_id is None
            or self._selected_identity != self.project_state.mask_identity(mask_id)
            or int(mask_id) not in result["properties"].index
        ):
            self.measurements.setText("Scientific measurements are not available for this selection."); return
        row = result["properties"].loc[int(mask_id)]
        lines = [f"Volume: {row['volume_um3']:.4g} µm³", f"Sphericity: {row['sphericity']:.4g}",
                 "Automatic status: " + ("retained" if bool(row.get("automatic_keep")) else "excluded")]
        if row.get("automatic_failure_reasons", ""): lines.append("Exclusion reasons: " + str(row["automatic_failure_reasons"]))
        if "excluded_by_z_guard" in row:
            lines.append("Z guard excluded: " + ("yes" if bool(row.get("excluded_by_z_guard")) else "no"))
        for key, value in row.items():
            if ("intensity" in key or "positive_fraction" in key) and hasattr(value, "__float__"): lines.append(f"{key}: {float(value):.4g}")
        self.measurements.setText("\n".join(lines))

    def _schedule_filters(self, *args):
        self.counting_error.hide()
        self._filter_timer.start()
        self.filters_edited.emit()

    def _intensity_master_changed(self, *args):
        self._update_optional_filter_visibility()
        self._schedule_filters()

    def _intensity_channel_changed(self, *args):
        self._update_optional_filter_visibility()
        self._schedule_filters()

    def _update_optional_filter_visibility(self):
        intensity_enabled = self.intensity_master.isChecked()
        active_intensity = sum(
            enabled.isChecked()
            for _, enabled, _, _, _ in self.intensity_controls.values()
        ) if intensity_enabled else 0
        self.intensity_label.setVisible(intensity_enabled)
        self.intensity_combination.setVisible(intensity_enabled and active_intensity >= 2)
        for group, *_ in self.intensity_controls.values():
            group.setVisible(intensity_enabled)

        marker_count = len(self.perinuclear_controls)
        self.perinuclear_label.setVisible(marker_count >= 2)
        self.perinuclear_combination.setVisible(marker_count >= 2)
        for group, *_ in self.perinuclear_controls.values():
            group.setVisible(marker_count > 0)

    def add_marker(self, record=None):
        if not self.project_state.channels:
            self.marker_configuration_note.setText("Load a project before configuring markers.")
            return
        if record is None:
            used = {item["channel_index"] for item in self.marker_configuration()}
            available = next(
                (item.index for item in self.project_state.channels if item.index not in used),
                None,
            )
            if available is None:
                self.measurement_status.setText(
                    "Every image channel already has a perinuclear marker configuration."
                )
                return
            record = {"channel_index": available}
            new_record = True
        else:
            new_record = False
        row = PerinuclearMarkerRow(self.project_state.channels, record, self)
        row.changed.connect(self._marker_configuration_edited)
        row.remove_requested.connect(self.remove_marker)
        self.marker_configuration_layout.insertWidget(
            self.marker_configuration_layout.count() - 1, row
        )
        self.marker_rows.append(row)
        self.marker_configuration_note.setVisible(False)
        if new_record:
            self._marker_configuration_edited()

    def remove_marker(self, row):
        if row not in self.marker_rows:
            return
        self.marker_rows.remove(row)
        row.deleteLater()
        self.marker_configuration_note.setVisible(not self.marker_rows)
        self._marker_configuration_edited()

    def marker_configuration(self):
        return [row.record() for row in self.marker_rows]

    def _marker_configuration_edited(self, *args):
        records = self.marker_configuration()
        indexes = [item["channel_index"] for item in records]
        if len(indexes) != len(set(indexes)):
            self.measurement_status.setText(
                "Each source channel may be configured as a perinuclear marker only once."
            )
            return
        for item in records:
            if not 0 <= item["ring_inner_um"] < item["ring_outer_um"]:
                self.measurement_status.setText(
                    "Perinuclear distances must satisfy 0 ≤ inner < outer."
                )
                return
        self.marker_configuration_changed.emit(records)

    def set_marker_configuration(self, records):
        records = [dict(item) for item in records]
        if self.marker_rows and self.marker_configuration() == records:
            return
        for row in self.marker_rows:
            row.deleteLater()
        self.marker_rows = []
        for record in records:
            self.add_marker(record)
        self.marker_configuration_note.setVisible(not records)

    def _channels_changed(self, channels):
        records = self.marker_configuration()
        self.set_marker_configuration(records)

    def set_perinuclear_filter_defaults(self, records):
        by_index = {int(item["channel_index"]): item for item in records}
        changed = False
        for _, control, definition in self.perinuclear_controls.values():
            record = by_index.get(int(definition["channel_index"]))
            if record is None:
                continue
            value = float(record["initial_min_positive_fraction"])
            if control.value() != value:
                control.setValue(value)
                changed = True
        if changed:
            self._schedule_filters()

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
                f"Detected masks: {int(result.get('edited_object_count', result['object_count'])):,}"
            )
            self.review_status.setText(
                ("Committed edits are ready for visual review. " if result.get("edit_history") else
                 "Raw masks are ready for visual review. ") + "Use selection and visibility "
                "controls to inspect individual masks."
            )
        else:
            self.segmentation_status.setText("Segmentation: outdated")
            self.mask_count.setText(
                f"Detected masks: {int(result.get('edited_object_count', result['object_count'])):,}"
            )
            self.review_status.setText(
                "Previous masks remain visible for inspection but are outdated. "
                "Rerun segmentation before further scientific review."
            )
        self._refresh_actions()
        self.calculate_button.setEnabled(result is not None and current)
        self.accept_roi_button.setEnabled(
            result is not None
            and current
            and self.project_state.filtering_result is not None
        )

    def set_visualization_status(self, message: str) -> None:
        self.visualization_status.setText(str(message))

    def _refresh_actions(self) -> None:
        available = self._result is not None
        selected = available and self._selected_is_raw and self._selected_id is not None
        self.show_raw_button.setEnabled(available)
        self.show_all_button.setEnabled(available)
        self.show_selected_button.setEnabled(selected)
        self.clear_button.setEnabled(self._selected_id is not None)

    def dispose(self) -> None:
        self._filter_timer.stop()
        self.project_state.segmentation_result_changed.disconnect(self.set_result)
        self.project_state.channels_changed.disconnect(self._channels_changed)
        self.project_state.perinuclear_markers_changed.disconnect(
            self.set_marker_configuration
        )
