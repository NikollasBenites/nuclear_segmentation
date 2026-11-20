"""Independent multichannel Z-intensity correction controls."""
from __future__ import annotations

from copy import deepcopy

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
from .results_panel import ScientificResultsSection
from .ui_layout import add_stacked_combo_row
from .z_correction import DEFAULTS, validate_options


class ZCorrectionPanel(QWidget):
    calculate_requested = Signal(object, int, bool)
    apply_requested = Signal(object)
    reset_selected_requested = Signal(object)
    reset_all_requested = Signal()
    profiles_requested = Signal(int, bool)
    settings_changed = Signal(int)
    inspected_channel_changed = Signal(int)
    results_channel_changed = Signal(int)

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self._settings: dict[int, dict] = {}
        self._states: dict[int, str] = {}
        self._channel_names: dict[int, str] = {}
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        section = CollapsibleSection("z_correction", "Z-Intensity Correction")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        note = QLabel(
            "Channels are corrected independently from the uncorrected working "
            "volume. Gaussian sigma is measured in Z planes."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.channel_list = QListWidget()
        self.channel_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.channel_list.setFixedHeight(88)
        layout.addWidget(self.channel_list)
        selection_buttons = QGridLayout()
        self.select_all_button = QPushButton("Select All")
        self.clear_selection_button = QPushButton("Clear Selection")
        selection_buttons.addWidget(self.select_all_button, 0, 0)
        selection_buttons.addWidget(self.clear_selection_button, 0, 1)
        layout.addLayout(selection_buttons)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.inspect_combo = QComboBox()
        add_stacked_combo_row(form, "Inspect/settings channel", self.inspect_combo)
        self.fields = {}
        for key, label, minimum, maximum in (
            ("percentile", "Reference percentile", 0.001, 100.0),
            ("sigma_planes", "Gaussian sigma (Z planes)", 0.0, 1000.0),
            ("min_gain", "Minimum gain", 0.001, 100.0),
            ("max_gain", "Maximum gain", 0.001, 100.0),
        ):
            field = QDoubleSpinBox()
            field.setRange(minimum, maximum)
            field.setDecimals(3)
            field.setValue(DEFAULTS[key])
            field.valueChanged.connect(self._save_current_settings)
            self.fields[key] = field
            form.addRow(label, field)
        self.segmentation = QCheckBox(
            "Apply to segmentation input"
        )
        self.segmentation.setToolTip("Use correction if this is the designated segmentation channel.")
        self.measurement = QCheckBox(
            "Apply to channel measurements"
        )
        self.measurement.setToolTip("Use corrected intensities for measurements on this channel.")
        self.segmentation.setChecked(DEFAULTS["segmentation"])
        self.measurement.setChecked(DEFAULTS["measurement"])
        self.segmentation.toggled.connect(self._save_current_settings)
        self.measurement.toggled.connect(self._save_current_settings)
        form.addRow(self.segmentation)
        form.addRow(self.measurement)
        layout.addLayout(form)
        self.include_zeros = QCheckBox(
            "Include zeros in profiles"
        )
        self.include_zeros.setChecked(True)
        self.include_zeros.setToolTip(
            "Artificial polygon-crop fill is always excluded. This option changes "
            "only diagnostic P99 and mean profiles, not the correction algorithm."
        )
        self.include_zeros.toggled.connect(self._profiles_setting_changed)
        layout.addWidget(self.include_zeros)
        actions = QGridLayout()
        self.calculate_button = QPushButton("Calculate and Preview")
        self.apply_button = QPushButton("Apply Z Correction")
        self.reset_selected_button = QPushButton("Reset Selected")
        self.reset_selected_button.setToolTip("Reset the selected channels’ Z corrections.")
        self.reset_all_button = QPushButton("Reset All")
        self.reset_all_button.setToolTip("Reset Z corrections for every channel.")
        self.profiles_button = QPushButton("Show Z Profiles")
        actions.addWidget(self.calculate_button, 0, 0, 1, 2)
        actions.addWidget(self.apply_button, 1, 0, 1, 2)
        actions.addWidget(self.reset_selected_button, 2, 0)
        actions.addWidget(self.reset_all_button, 2, 1)
        actions.addWidget(self.profiles_button, 3, 0, 1, 2)
        layout.addLayout(actions)
        self.status = QLabel("Load a project image to configure Z correction.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        section.set_content_layout(layout)
        outer.addWidget(section)

        self.results = ScientificResultsSection(
            "z_correction_results", "Z-Correction Results"
        )
        results_selector = QWidget()
        results_selector_layout = QFormLayout(results_selector)
        results_selector_layout.setContentsMargins(0, 0, 0, 0)
        results_selector_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.results_channel_combo = QComboBox()
        add_stacked_combo_row(results_selector_layout, "Results channel", self.results_channel_combo)
        self.results.insert_widget(0, results_selector)

        from matplotlib.backends.backend_qtagg import (
            FigureCanvasQTAgg,
            NavigationToolbar2QT,
        )
        from matplotlib.figure import Figure

        self.profile_figure = Figure(figsize=(5.0, 7.5))
        self.profile_canvas = FigureCanvasQTAgg(self.profile_figure)
        self.profile_canvas.setMinimumHeight(620)
        self.profile_toolbar = NavigationToolbar2QT(self.profile_canvas, self.results)
        self.profile_toolbar.setMinimumWidth(0)
        self.profile_note = QLabel(
            "The horizontal axis uses calibrated Z positions. Artificial "
            "polygon-crop fill is excluded."
        )
        self.profile_note.setWordWrap(True)
        self.results.insert_widget(2, self.profile_toolbar)
        self.results.insert_widget(3, self.profile_canvas)
        self.results.insert_widget(4, self.profile_note)
        self.clear_profiles()
        outer.addWidget(self.results)
        outer.addStretch(1)

        self.select_all_button.clicked.connect(self.channel_list.selectAll)
        self.clear_selection_button.clicked.connect(self.channel_list.clearSelection)
        self.inspect_combo.currentIndexChanged.connect(self._load_current_settings)
        self.inspect_combo.currentIndexChanged.connect(self._emit_inspected)
        self.calculate_button.clicked.connect(self._request_calculate)
        self.apply_button.clicked.connect(
            lambda: self.apply_requested.emit(self.selected_indexes())
        )
        self.reset_selected_button.clicked.connect(
            lambda: self.reset_selected_requested.emit(self.selected_indexes())
        )
        self.reset_all_button.clicked.connect(self.reset_all_requested)
        self.profiles_button.clicked.connect(self._request_profiles)
        self.results_channel_combo.currentIndexChanged.connect(
            self._results_channel_changed
        )
        project_state.channels_changed.connect(self.set_channels)
        self.set_channels(project_state.channels)

    def set_channels(self, channels) -> None:
        selected = set(self.selected_indexes())
        inspected = self.inspect_combo.currentData()
        results_index = self.results_channel_combo.currentData()
        self._loading = True
        self.channel_list.clear()
        self.inspect_combo.clear()
        self.results_channel_combo.clear()
        indexes = set()
        for channel in channels:
            indexes.add(channel.index)
            self._channel_names[channel.index] = channel.name
            self._states.setdefault(channel.index, "No correction")
            item = QListWidgetItem(
                f"{channel.index}: {channel.name} — {self._states[channel.index]}"
            )
            item.setData(Qt.ItemDataRole.UserRole, channel.index)
            self.channel_list.addItem(item)
            self.inspect_combo.addItem(channel.name, channel.index)
            self.results_channel_combo.addItem(channel.name, channel.index)
            self._settings.setdefault(channel.index, deepcopy(DEFAULTS))
            if channel.index in selected:
                item.setSelected(True)
        self._settings = {
            index: options for index, options in self._settings.items()
            if index in indexes
        }
        self._states = {
            index: state for index, state in self._states.items() if index in indexes
        }
        self._channel_names = {
            index: name for index, name in self._channel_names.items() if index in indexes
        }
        default = self.project_state.segmentation_channel_index
        for combo, wanted in (
            (self.inspect_combo, inspected if inspected is not None else default),
            (self.results_channel_combo, results_index if results_index is not None else default),
        ):
            position = combo.findData(wanted)
            if position >= 0:
                combo.setCurrentIndex(position)
        self._loading = False
        self._load_current_settings()
        enabled = bool(channels)
        for button in (
            self.calculate_button,
            self.apply_button,
            self.reset_selected_button,
            self.reset_all_button,
            self.profiles_button,
        ):
            button.setEnabled(enabled)
        if not enabled:
            self.status.setText("Load a project image to configure Z correction.")
            self.results.set_results("No results calculated.")

    def selected_indexes(self) -> list[int]:
        return [
            int(item.data(Qt.ItemDataRole.UserRole))
            for item in self.channel_list.selectedItems()
        ]

    def current_index(self) -> int | None:
        value = self.inspect_combo.currentData()
        return None if value is None else int(value)

    def options_for(self, index: int) -> dict:
        return validate_options(self._settings.get(int(index), {}))

    def _save_current_settings(self, *args) -> None:
        if self._loading:
            return
        index = self.current_index()
        if index is None:
            return
        options = {
            **{key: field.value() for key, field in self.fields.items()},
            "segmentation": self.segmentation.isChecked(),
            "measurement": self.measurement.isChecked(),
        }
        try:
            self._settings[index] = validate_options(options)
        except ValueError as error:
            self.status.setText(str(error))
        self.settings_changed.emit(index)

    def _load_current_settings(self, *args) -> None:
        index = self.current_index()
        if index is None:
            return
        options = self._settings.setdefault(index, deepcopy(DEFAULTS))
        self._loading = True
        for key, field in self.fields.items():
            field.setValue(options[key])
        self.segmentation.setChecked(options["segmentation"])
        self.measurement.setChecked(options["measurement"])
        self._loading = False

    def _emit_inspected(self, *args) -> None:
        if self._loading:
            return
        index = self.current_index()
        if index is not None:
            self.inspected_channel_changed.emit(index)

    def _request_calculate(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            self.status.setText("Select one or more channels for Z correction.")
            return
        options = {index: self.options_for(index) for index in indexes}
        inspected = self.current_index()
        if inspected not in indexes:
            inspected = indexes[0]
            self.inspect_combo.setCurrentIndex(self.inspect_combo.findData(inspected))
        self.calculate_requested.emit(options, inspected, self.include_zeros.isChecked())

    def _request_profiles(self) -> None:
        index = self.current_index()
        if index is not None:
            self.profiles_requested.emit(index, self.include_zeros.isChecked())

    def _profiles_setting_changed(self, checked: bool) -> None:
        if self.project_state.dataset_ready:
            self.status.setText(
                "Diagnostic sampling changed. Recalculate to refresh Z profiles; "
                "applied correction settings are unchanged."
            )

    def _results_channel_changed(self, *args) -> None:
        if self._loading:
            return
        value = self.results_channel_combo.currentData()
        if value is not None:
            self.results_channel_changed.emit(int(value))

    def set_status(self, message: str) -> None:
        self.status.setText(str(message).splitlines()[0])

    def set_channel_state(self, channel_index: int, state: str) -> None:
        index = int(channel_index)
        self._states[index] = str(state)
        for row in range(self.channel_list.count()):
            item = self.channel_list.item(row)
            if int(item.data(Qt.ItemDataRole.UserRole)) == index:
                name = self._channel_names.get(index, str(index))
                item.setText(f"{index}: {name} — {state}")
                break

    def set_results(self, channel_index: int, text: str) -> None:
        position = self.results_channel_combo.findData(int(channel_index))
        if position >= 0:
            self.results_channel_combo.setCurrentIndex(position)
        self.results.set_results(text)

    def set_profiles(self, channel_name: str, report: dict, spacing_z: float) -> None:
        """Render current diagnostic plots inside Z-Correction Results."""
        import numpy as np

        self.profile_figure.clear()
        axes = self.profile_figure.subplots(4, 1, sharex=True)
        z = np.arange(len(report["gain"]), dtype=float) * float(spacing_z)
        profiles = report["intensity_profiles"]
        axes[0].plot(z, profiles["original"]["p99"], label="Original")
        axes[0].plot(z, profiles["corrected"]["p99"], label="Corrected")
        axes[0].set_ylabel("P99")
        axes[1].plot(z, profiles["original"]["mean"], label="Original")
        axes[1].plot(z, profiles["corrected"]["mean"], label="Corrected")
        axes[1].set_ylabel("Mean")
        axes[2].plot(z, report["observed_profile"], label="Observed reference")
        axes[2].plot(z, report["smoothed_profile"], label="Smoothed reference")
        axes[2].set_ylabel(f"P{report['options']['percentile']:g}")
        axes[3].plot(z, report["gain"], label="Multiplicative gain")
        axes[3].set_ylabel("Gain")
        axes[3].set_xlabel("Z position (µm)")
        for axis in axes:
            axis.grid(alpha=0.25)
            axis.legend(fontsize="small")
        self.profile_figure.suptitle(
            f"{channel_name}\n{report['diagnostic_scope']}; P99 diagnostics "
            "are distinct from the correction reference percentile",
            fontsize="medium",
        )
        self.profile_figure.tight_layout()
        for widget in (self.profile_toolbar, self.profile_canvas, self.profile_note):
            widget.show()
        self.profile_canvas.draw_idle()

    def clear_profiles(self) -> None:
        self.profile_figure.clear()
        for widget in (self.profile_toolbar, self.profile_canvas, self.profile_note):
            widget.hide()

    def reset_controls(self) -> None:
        self._settings = {
            channel.index: deepcopy(DEFAULTS) for channel in self.project_state.channels
        }
        self._states = {
            channel.index: "No correction" for channel in self.project_state.channels
        }
        for channel in self.project_state.channels:
            self.set_channel_state(channel.index, "No correction")
        self._load_current_settings()
        self.status.setText("No Z correction applied.")
        self.results.set_results("No results calculated.")
        self.clear_profiles()

    def reset_channel_controls(self, channel_indexes) -> None:
        for index in channel_indexes:
            self._settings[int(index)] = deepcopy(DEFAULTS)
            self.set_channel_state(int(index), "No correction")
        self._load_current_settings()

    def dispose(self) -> None:
        self.project_state.channels_changed.disconnect(self.set_channels)
