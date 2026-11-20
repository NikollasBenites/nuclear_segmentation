"""Application-owned Cellpose configuration panel; no inference is executed here."""
from __future__ import annotations

from copy import deepcopy
import math

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from .collapsible_section import CollapsibleSection
from .config import defaults, validate_config


class SegmentationPanel(QWidget):
    """Edit the existing scientific configuration without loading Cellpose."""

    run_requested = Signal()
    configuration_changed = Signal()
    mask_review_requested = Signal()

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self._defaults = defaults()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)

        section = CollapsibleSection("segmentation_input", "Segmentation Input")
        self.unsupported_role = QLabel("Cytoplasmic segmentation execution is unavailable.")
        self.unsupported_role.setWordWrap(True)
        self.unsupported_role.hide()
        root.addWidget(self.unsupported_role)
        self.input_section = section
        form = QFormLayout()
        form.setContentsMargins(10, 8, 10, 10)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.input_values = {}
        for key, title in (
            ("source", "Source image"),
            ("channel", "Segmentation channel"),
            ("shape", "Working dimensions (Z, Y, X)"),
            ("spacing", "Voxel spacing (Z, Y, X)"),
            ("anisotropy", "Calibrated anisotropy"),
            ("crop", "Crop / polygon ROI"),
            ("z_correction", "Z-intensity correction"),
            ("normalization", "Cellpose normalization"),
        ):
            value = QLabel("—")
            value.setObjectName(f"segmentation_{key}")
            value.setWordWrap(True)
            value.setMinimumWidth(0)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            self.input_values[key] = value
            form.addRow(title, value)
        section.set_content_layout(form)
        root.addWidget(section)

        section = CollapsibleSection("cellpose_model", "Cellpose Model")
        self.model_section = section
        form = QFormLayout()
        form.setContentsMargins(10, 8, 10, 10)
        self.model_name = QLineEdit(str(self._defaults["MODEL_NAME"]))
        self.model_name.setPlaceholderText("Model name or local model path")
        self.device = QComboBox()
        self.device.addItem("Automatic (CUDA → Apple MPS → CPU)", "auto")
        self.device.addItem("CPU", "cpu")
        self.mode = QComboBox()
        self.mode.addItem("3D segmentation", True)
        self.mode.addItem("2D + stitching", False)
        form.addRow("Model name or path", self.model_name)
        form.addRow("Processing device", self.device)
        form.addRow("Segmentation mode", self.mode)
        section.set_content_layout(form)
        root.addWidget(section)

        section = CollapsibleSection("cellpose_parameters", "Segmentation Parameters")
        self.parameters_section = section
        form = QFormLayout()
        form.setContentsMargins(10, 8, 10, 10)
        self.cellprob = self._double(-1000, 1000, self._defaults["CELLPROB_THRESHOLD"])
        self.min_size = QSpinBox()
        self.min_size.setRange(0, 2_147_483_647)
        self.min_size.setValue(int(self._defaults["CELLPOSE_MIN_SIZE_VOXELS"]))
        self.stitch = self._double(0, 1, self._defaults["CELLPOSE_STITCH_THRESHOLD"], 4)
        form.addRow("Cell probability threshold", self.cellprob)
        form.addRow("Minimum size (voxels)", self.min_size)
        form.addRow("2D stitch threshold", self.stitch)
        section.set_content_layout(form)
        root.addWidget(section)

        section = CollapsibleSection("cellpose_advanced", "Advanced Parameters", expanded=False)
        self.advanced_section = section
        form = QFormLayout()
        form.setContentsMargins(10, 8, 10, 10)
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, 2_147_483_647)
        self.batch_size.setValue(int(self._defaults["BATCH_SIZE"]))
        flow_widget = QWidget()
        flow_layout = QHBoxLayout(flow_widget)
        flow_layout.setContentsMargins(0, 0, 0, 0)
        self.flow_smooth = []
        for axis, value in zip("ZYX", self._defaults["FLOW3D_SMOOTH"]):
            field = self._double(0, 1000, value)
            field.setPrefix(f"{axis}: ")
            self.flow_smooth.append(field)
            flow_layout.addWidget(field)
        self.resample = QCheckBox("Enable Cellpose resampling")
        self.resample.setChecked(bool(self._defaults["CELLPOSE_RESAMPLE"]))
        self.rescale_enabled = QCheckBox("Use explicit rescale")
        self.rescale = self._double(0.000001, 1000, 1, 6)
        self.rescale.setEnabled(False)
        self.diameter_enabled = QCheckBox("Use explicit diameter")
        self.diameter = self._double(0.000001, 1_000_000, 30)
        self.diameter.setEnabled(False)
        form.addRow("Batch size", self.batch_size)
        form.addRow("3D flow smoothing (Z, Y, X)", flow_widget)
        form.addRow(self.resample)
        form.addRow(self.rescale_enabled, self.rescale)
        form.addRow(self.diameter_enabled, self.diameter)
        section.set_content_layout(form)
        root.addWidget(section)

        section = CollapsibleSection("segmentation_readiness", "Execution Readiness")
        self.execution_section = section
        content = QVBoxLayout()
        content.setContentsMargins(10, 8, 10, 10)
        self.readiness = QLabel()
        self.readiness.setObjectName("segmentationReadiness")
        self.readiness.setWordWrap(True)
        content.addWidget(self.readiness)
        self.execution_summary = QLabel("No segmentation has run.")
        self.execution_summary.setWordWrap(True)
        content.addWidget(self.execution_summary)
        self.execution_progress = QProgressBar()
        self.execution_progress.setRange(0, 0)
        self.execution_progress.setTextVisible(False)
        self.execution_progress.hide()
        content.addWidget(self.execution_progress)
        self.execution_log = QPlainTextEdit()
        self.execution_log.setReadOnly(True)
        self.execution_log.setMaximumHeight(130)
        self.execution_log.hide()
        content.addWidget(self.execution_log)
        self.execute_button = QPushButton("Run Segmentation")
        self.execute_button.setEnabled(False)
        self.execute_button.setToolTip("Run the existing Cellpose pipeline on the current working image.")
        content.addWidget(self.execute_button)
        self.return_to_results_button = QPushButton("Return to Completed Results")
        self.return_to_results_button.hide()
        content.addWidget(self.return_to_results_button)
        section.set_content_layout(content)
        root.addWidget(section)

        self.results_section = CollapsibleSection(
            "segmentation_results", "Segmentation Results"
        )
        results = QVBoxLayout()
        results.setContentsMargins(10, 8, 10, 10)
        self.result_summary = QLabel()
        self.result_summary.setWordWrap(True)
        self.result_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        results.addWidget(self.result_summary)
        self.view_parameters_button = QPushButton("View Parameters")
        self.view_parameters_button.setCheckable(True)
        results.addWidget(self.view_parameters_button)
        self.executed_parameters = QLabel()
        self.executed_parameters.setWordWrap(True)
        self.executed_parameters.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.executed_parameters.hide()
        results.addWidget(self.executed_parameters)
        self.run_new_button = QPushButton("Run New Segmentation")
        self.go_review_button = QPushButton("Go to Mask Review")
        results.addWidget(self.run_new_button)
        results.addWidget(self.go_review_button)
        self.results_section.set_content_layout(results)
        self.results_section.hide()
        root.addWidget(self.results_section)
        root.addStretch(1)

        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.rescale_enabled.toggled.connect(self.rescale.setEnabled)
        self.diameter_enabled.toggled.connect(self.diameter.setEnabled)
        for signal in (
            self.model_name.textChanged, self.device.currentIndexChanged,
            self.mode.currentIndexChanged, self.cellprob.valueChanged,
            self.min_size.valueChanged, self.stitch.valueChanged,
            self.batch_size.valueChanged, self.resample.toggled,
            self.rescale_enabled.toggled, self.rescale.valueChanged,
            self.diameter_enabled.toggled, self.diameter.valueChanged,
            *(field.valueChanged for field in self.flow_smooth),
        ):
            signal.connect(self._configuration_edited)
        project_state.changed.connect(self.refresh)
        project_state.channels_changed.connect(self.refresh)
        project_state.calibration_changed.connect(self.refresh)
        project_state.normalization_changed.connect(self.refresh)
        project_state.z_correction_changed.connect(self.refresh)
        project_state.segmentation_result_changed.connect(self._result_changed)
        self.execute_button.clicked.connect(self.run_requested)
        self.view_parameters_button.toggled.connect(
            self.executed_parameters.setVisible
        )
        self.run_new_button.clicked.connect(self._show_configuration)
        self.return_to_results_button.clicked.connect(self._show_completed_result)
        self.go_review_button.clicked.connect(self.mask_review_requested)
        self._busy = False
        self._external_busy = False
        self._mode_changed()

    @property
    def _configuration_sections(self):
        return (
            self.input_section, self.model_section, self.parameters_section,
            self.advanced_section, self.execution_section,
        )

    def _show_configuration(self) -> None:
        for section in self._configuration_sections:
            section.show()
        self.results_section.hide()
        has_result = bool(
            self.project_state.segmentation_result is not None
            and self.project_state.segmentation_is_current
        )
        self.return_to_results_button.setVisible(has_result)
        if has_result and not self._busy:
            self.execution_summary.setText(
                "These settings apply to the next execution. Existing masks remain available."
            )

    def _show_completed_result(self) -> None:
        result = self.project_state.segmentation_result
        if result is None or not self.project_state.segmentation_is_current:
            self._show_configuration()
            return
        for section in self._configuration_sections:
            section.hide()
        self.return_to_results_button.hide()
        self.result_summary.setText(self._format_result_summary(result))
        self.executed_parameters.setText(self._format_executed_parameters(result))
        self.view_parameters_button.setChecked(False)
        self.results_section.show()

    @staticmethod
    def _executed_config(result):
        return result.get("scientific_config") or result.get("parameters") or {}

    def _format_result_summary(self, result) -> str:
        config = self._executed_config(result)
        do_3d = bool(config.get("CELLPOSE_DO_3D", True))
        z_applied = result.get("z_corrections") or {}
        channel = result.get("segmentation_channel")
        correction = z_applied.get(channel, {}) if channel is not None else {}
        processing = (
            "Z-corrected" if correction.get("segmentation")
            else "Original working image"
        )
        normalize = result.get("cellpose_normalize", config.get("CELLPOSE_NORMALIZE", True))
        normalization = "Cellpose Default" if normalize is True else (
            "Disabled" if normalize is False else "Custom Cellpose normalization"
        )
        return "\n".join((
            "Status: Completed",
            "Segmentation type: Nuclear",
            f"Model: {result.get('model_name', config.get('MODEL_NAME', '—'))}",
            f"Device: {result.get('device', config.get('PROCESSING_DEVICE', '—'))}",
            f"Mode: {'3D' if do_3d else '2D + stitching'}",
            f"Objects detected: {int(result.get('object_count', 0)):,}",
            f"Input channel: {result.get('segmentation_channel', '—')}",
            f"Input processing: {processing}",
            f"Normalization: {normalization}",
        ))

    def _format_executed_parameters(self, result) -> str:
        config = self._executed_config(result)
        fields = (
            ("MODEL_NAME", "Model"),
            ("PROCESSING_DEVICE", "Requested device"),
            ("CELLPOSE_DO_3D", "3D mode"),
            ("CELLPROB_THRESHOLD", "Cell probability threshold"),
            ("CELLPOSE_MIN_SIZE_VOXELS", "Minimum size (voxels)"),
            ("CELLPOSE_STITCH_THRESHOLD", "Stitch threshold"),
            ("BATCH_SIZE", "Batch size"),
            ("FLOW3D_SMOOTH", "3D flow smoothing"),
            ("CELLPOSE_RESAMPLE", "Resample"),
            ("CELLPOSE_RESCALE", "Rescale"),
            ("DIAMETER_PIXELS", "Diameter (pixels)"),
            ("CELLPOSE_NORMALIZE", "Cellpose normalization"),
        )
        return "\n".join(
            f"{label}: {config.get(key, '—')}" for key, label in fields
        )

    def _configuration_edited(self, *args):
        self.configuration_changed.emit()
        self.refresh()

    def _set_input_text(self, key: str, text: str) -> None:
        value = str(text)
        self.input_values[key].setText(value)
        self.input_values[key].setToolTip(value)

    @staticmethod
    def _double(minimum, maximum, value, decimals=3):
        field = QDoubleSpinBox()
        field.setRange(float(minimum), float(maximum))
        field.setDecimals(int(decimals))
        field.setValue(float(value))
        field.setKeyboardTracking(False)
        return field

    def _mode_changed(self, *args):
        is_3d = bool(self.mode.currentData())
        self.stitch.setEnabled(not is_3d)
        for field in self.flow_smooth:
            field.setEnabled(is_3d)
        self.refresh()

    def pending_config(self):
        state = self.project_state
        config = deepcopy(self._defaults)
        config.update(
            RUN_MODE="segment",
            MODEL_NAME=self.model_name.text().strip(),
            PROCESSING_DEVICE=str(self.device.currentData()),
            CELLPROB_THRESHOLD=float(self.cellprob.value()),
            CELLPOSE_MIN_SIZE_VOXELS=int(self.min_size.value()),
            BATCH_SIZE=int(self.batch_size.value()),
            FLOW3D_SMOOTH=[float(field.value()) for field in self.flow_smooth],
            CELLPOSE_DO_3D=bool(self.mode.currentData()),
            CELLPOSE_STITCH_THRESHOLD=float(self.stitch.value()),
            CELLPOSE_RESAMPLE=bool(self.resample.isChecked()),
            CELLPOSE_RESCALE=float(self.rescale.value()) if self.rescale_enabled.isChecked() else None,
            DIAMETER_PIXELS=float(self.diameter.value()) if self.diameter_enabled.isChecked() else None,
            CHANNELS=state.analysis_channel_records(),
            SEGMENTATION_CHANNEL=state.segmentation_channel,
            VOXEL_SPACING_OVERRIDE_UM=None if state.voxel_spacing_zyx_um is None else list(state.voxel_spacing_zyx_um),
            Z_CORRECTIONS=state.legacy_z_corrections(),
            Z_CORRECTION_SOURCE=None,
            PERINUCLEAR_MARKERS=state.legacy_perinuclear_markers(),
        )
        normalization = state.normalization_configuration or {}
        config["CELLPOSE_NORMALIZE"] = deepcopy(normalization.get("cellpose_normalize", True))
        config["CELLPOSE_ROI_NORMALIZATION"] = None
        return config

    def scientific_config(self):
        config = self.pending_config()
        if not config["MODEL_NAME"]:
            raise ValueError("A Cellpose model name or local model path is required.")
        if not math.isfinite(config["CELLPROB_THRESHOLD"]):
            raise ValueError("Cell probability threshold must be finite.")
        return validate_config(config)

    def restore_settings(self, config):
        self.model_name.setText(config["MODEL_NAME"])
        self.device.setCurrentIndex(self.device.findData(config["PROCESSING_DEVICE"]))
        self.mode.setCurrentIndex(self.mode.findData(config["CELLPOSE_DO_3D"]))
        for field, key in (
            (self.cellprob, "CELLPROB_THRESHOLD"), (self.min_size, "CELLPOSE_MIN_SIZE_VOXELS"),
            (self.stitch, "CELLPOSE_STITCH_THRESHOLD"), (self.batch_size, "BATCH_SIZE"),
        ):
            field.setValue(config[key])
        for field, value in zip(self.flow_smooth, config["FLOW3D_SMOOTH"], strict=True):
            field.setValue(value)
        self.resample.setChecked(config["CELLPOSE_RESAMPLE"])
        for enabled, field, key in (
            (self.rescale_enabled, self.rescale, "CELLPOSE_RESCALE"),
            (self.diameter_enabled, self.diameter, "DIAMETER_PIXELS"),
        ):
            value = config[key]
            enabled.setChecked(value is not None)
            if value is not None:
                field.setValue(value)

    def prepare_scientific_input(self):
        """Validate and route current in-memory data without running Cellpose."""
        from .scientific_input import prepare_scientific_input

        return prepare_scientific_input(self.project_state, self.scientific_config())

    def refresh(self, *args):
        state = self.project_state
        dataset = state.working_dataset
        channel = next((item for item in state.channels if item.index == state.active_channel_index), None)
        if dataset is None:
            for key in self.input_values:
                self._set_input_text(key, "—")
        else:
            shape = dataset.data_czyx.shape[1:]
            spacing = tuple(float(value) for value in state.voxel_spacing_zyx_um)
            anisotropy = spacing[0] / ((spacing[1] + spacing[2]) / 2)
            crop = state.crop_provenance
            crop_text = "Not cropped"
            if crop is not None:
                roi = "polygon ROI retained" if crop.selection_type == "polygon" else "rectangular crop"
                crop_text = f"Applied — {roi}"
            self._set_input_text("source", dataset.source_filename)
            self._set_input_text("channel", "Not designated" if channel is None else f"Index {channel.index}: {channel.name}")
            self._set_input_text("shape", " × ".join(str(int(v)) for v in shape))
            self._set_input_text("spacing", " × ".join(f"{v:.6g}" for v in spacing) + " µm")
            self._set_input_text("anisotropy", f"{anisotropy:.6g} (Z / mean XY)")
            self._set_input_text("crop", crop_text)
            if channel is not None and channel.index in state.z_correction_applied:
                z_text = "Applied for selected channel"
            elif channel is not None and channel.index in state.z_correction_reports:
                z_text = "Preview calculated; not applied"
            else:
                z_text = "Not applied"
            self._set_input_text("z_correction", z_text)
            normalization = state.normalization_configuration or {}
            mode = normalization.get("mode", "cellpose_default")
            text = mode.replace("_", " ").title()
            if mode not in {"cellpose_default", "disabled"}:
                text += " — applied" if state.normalization_is_current else " — outdated"
            self._set_input_text("normalization", text)

        missing = []
        if dataset is None:
            missing.append("load a dataset")
        spacing = state.voxel_spacing_zyx_um
        if spacing is None or len(spacing) != 3 or not all(math.isfinite(float(v)) and float(v) > 0 for v in (spacing or ())):
            missing.append("provide valid Z/Y/X calibration")
        if channel is None:
            missing.append("designate a segmentation channel")
        normalization = state.normalization_configuration or {}
        if normalization.get("mode") not in {None, "cellpose_default", "disabled"} and not state.normalization_is_current:
            missing.append("apply current Cellpose normalization settings")
        try:
            config = self.scientific_config()
            if dataset is not None:
                from .scientific_input import validate_scientific_input

                validate_scientific_input(state, config)
        except (TypeError, ValueError) as error:
            missing.append(str(error))
        supported = state.active_session.scientific_role == "nuclear"
        if not supported:
            self.readiness.setText("Cytoplasmic segmentation execution is unavailable.")
        elif self._busy:
            self.readiness.setText("Cellpose segmentation is running. Project inputs are locked.")
        elif self._external_busy:
            self.readiness.setText("Wait for the active project operation to finish.")
        elif missing:
            self.readiness.setText("Not ready: " + "; ".join(dict.fromkeys(missing)) + ".")
        else:
            self.readiness.setText("Scientific input is valid and ready for Cellpose execution.")
        self.execute_button.setEnabled(
            supported and not self._busy and not self._external_busy and not missing
        )

    def set_external_busy(self, busy: bool) -> None:
        busy = bool(busy)
        if busy == self._external_busy:
            return
        self._external_busy = busy
        self.refresh()

    def set_execution_running(self, running: bool) -> None:
        self._busy = bool(running)
        for widget in (
            self.model_name, self.device, self.mode, self.cellprob, self.min_size,
            self.stitch, self.batch_size, self.resample, self.rescale_enabled,
            self.rescale, self.diameter_enabled, self.diameter, *self.flow_smooth,
        ):
            widget.setEnabled(not running)
        self.execution_progress.setVisible(running)
        self.execution_log.setVisible(running or bool(self.execution_log.toPlainText()))
        self.refresh()
        if (
            not running
            and self.project_state.segmentation_result is not None
            and self.project_state.segmentation_is_current
        ):
            self._show_completed_result()

    def set_execution_status(self, message: str, *, append: bool = True) -> None:
        self.execution_summary.setText(str(message))
        if append:
            self.execution_log.appendPlainText(str(message))

    def set_elapsed(self, seconds: float) -> None:
        self.execution_progress.setFormat(f"Elapsed: {seconds:.1f} s")
        self.execution_progress.setTextVisible(True)

    def _result_changed(self, result, current: bool) -> None:
        if result is None:
            self._show_configuration()
            self.execution_summary.setText("No segmentation has run.")
        elif current:
            self.execution_summary.setText(
                f"Completed — {result['object_count']:,} objects; "
                f"mask shape {tuple(result['mask_shape'])}; device {result['device']}."
            )
            if not self._busy:
                self._show_completed_result()
        else:
            self.execution_summary.setText(
                "Previous segmentation remains available but is outdated for the current settings."
            )
            self._show_configuration()
        supported = self.project_state.active_session.scientific_role == "nuclear"
        self.unsupported_role.setVisible(not supported)
        self.run_new_button.setVisible(supported)
        if not supported:
            for section in self._configuration_sections:
                section.hide()
            self.return_to_results_button.hide()
        self.refresh()

    def dispose(self):
        state = self.project_state
        state.changed.disconnect(self.refresh)
        state.channels_changed.disconnect(self.refresh)
        state.calibration_changed.disconnect(self.refresh)
        state.normalization_changed.disconnect(self.refresh)
        state.z_correction_changed.disconnect(self.refresh)
        state.segmentation_result_changed.disconnect(self._result_changed)
