"""Application-owned Cellpose configuration panel; no inference is executed here."""
from __future__ import annotations

from copy import deepcopy
import math

from qtpy.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .collapsible_section import CollapsibleSection
from .config import defaults, validate_config


class SegmentationPanel(QWidget):
    """Edit the existing scientific configuration without loading Cellpose."""

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self._defaults = defaults()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)

        section = CollapsibleSection("segmentation_input", "Segmentation Input")
        form = QFormLayout()
        form.setContentsMargins(10, 8, 10, 10)
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
            self.input_values[key] = value
            form.addRow(title, value)
        section.set_content_layout(form)
        root.addWidget(section)

        section = CollapsibleSection("cellpose_model", "Cellpose Model")
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
        content = QVBoxLayout()
        content.setContentsMargins(10, 8, 10, 10)
        self.readiness = QLabel()
        self.readiness.setObjectName("segmentationReadiness")
        self.readiness.setWordWrap(True)
        content.addWidget(self.readiness)
        self.execute_button = QPushButton("Run Segmentation")
        self.execute_button.setEnabled(False)
        self.execute_button.setToolTip("Cellpose execution will be integrated in Phase 2C.2.")
        content.addWidget(self.execute_button)
        section.set_content_layout(content)
        root.addWidget(section)
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
            signal.connect(self.refresh)
        project_state.changed.connect(self.refresh)
        project_state.channels_changed.connect(self.refresh)
        project_state.calibration_changed.connect(self.refresh)
        project_state.normalization_changed.connect(self.refresh)
        project_state.z_correction_changed.connect(self.refresh)
        self._mode_changed()

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

    def scientific_config(self):
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
        )
        normalization = state.normalization_configuration or {}
        config["CELLPOSE_NORMALIZE"] = deepcopy(normalization.get("cellpose_normalize", True))
        config["CELLPOSE_ROI_NORMALIZATION"] = None
        if not config["MODEL_NAME"]:
            raise ValueError("A Cellpose model name or local model path is required.")
        if not math.isfinite(config["CELLPROB_THRESHOLD"]):
            raise ValueError("Cell probability threshold must be finite.")
        return validate_config(config)

    def refresh(self, *args):
        state = self.project_state
        dataset = state.working_dataset
        channel = next((item for item in state.channels if item.index == state.segmentation_channel_index), None)
        if dataset is None:
            for label in self.input_values.values():
                label.setText("—")
        else:
            shape = dataset.data_czyx.shape[1:]
            spacing = tuple(float(value) for value in state.voxel_spacing_zyx_um)
            anisotropy = spacing[0] / ((spacing[1] + spacing[2]) / 2)
            crop = state.crop_provenance
            crop_text = "Not cropped"
            if crop is not None:
                roi = "polygon ROI retained" if crop.selection_type == "polygon" else "rectangular crop"
                crop_text = f"Applied — {roi}"
            self.input_values["source"].setText(dataset.source_filename)
            self.input_values["channel"].setText("Not designated" if channel is None else f"Index {channel.index}: {channel.name}")
            self.input_values["shape"].setText(" × ".join(str(int(v)) for v in shape))
            self.input_values["spacing"].setText(" × ".join(f"{v:.6g}" for v in spacing) + " µm")
            self.input_values["anisotropy"].setText(f"{anisotropy:.6g} (Z / mean XY)")
            self.input_values["crop"].setText(crop_text)
            if channel is not None and channel.index in state.z_correction_applied:
                z_text = "Applied for selected channel"
            elif channel is not None and channel.index in state.z_correction_reports:
                z_text = "Preview calculated; not applied"
            else:
                z_text = "Not applied"
            self.input_values["z_correction"].setText(z_text)
            normalization = state.normalization_configuration or {}
            mode = normalization.get("mode", "cellpose_default")
            text = mode.replace("_", " ").title()
            if mode not in {"cellpose_default", "disabled"}:
                text += " — applied" if state.normalization_is_current else " — outdated"
            self.input_values["normalization"].setText(text)

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
            self.scientific_config()
        except (TypeError, ValueError) as error:
            missing.append(str(error))
        if missing:
            self.readiness.setText("Not ready: " + "; ".join(dict.fromkeys(missing)) + ".")
        else:
            self.readiness.setText("Configuration ready for Phase 2C.2 integration. Cellpose has not run.")
        self.execute_button.setEnabled(False)

    def dispose(self):
        state = self.project_state
        state.changed.disconnect(self.refresh)
        state.channels_changed.disconnect(self.refresh)
        state.calibration_changed.disconnect(self.refresh)
        state.normalization_changed.disconnect(self.refresh)
        state.z_correction_changed.disconnect(self.refresh)
