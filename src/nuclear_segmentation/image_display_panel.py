"""Application-owned, non-destructive controls for napari Image display properties."""
from __future__ import annotations

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .collapsible_section import CollapsibleSection


class ImageDisplayPanel(QWidget):
    """Edit only public Image-layer display properties for the active layer."""

    _SLIDER_STEPS = 1000

    def __init__(self, viewer, project_state, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.project_state = project_state
        self.layer = None
        self._callbacks = []
        self._syncing = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.section = CollapsibleSection("image_display", "Image Display")
        form = QFormLayout()
        form.setContentsMargins(10, 8, 10, 10)
        self.layer_name = QLabel("Select an Image layer")
        self.layer_name.setWordWrap(True)
        form.addRow("Selected layer", self.layer_name)

        self.minimum = self._value_editor()
        self.maximum = self._value_editor()
        self.minimum_slider = self._slider()
        self.maximum_slider = self._slider()
        form.addRow("Minimum", self.minimum)
        form.addRow("", self.minimum_slider)
        form.addRow("Maximum", self.maximum)
        form.addRow("", self.maximum_slider)

        self.gamma = QDoubleSpinBox()
        self.gamma.setRange(0.1, 5.0)
        self.gamma.setDecimals(3)
        self.gamma.setSingleStep(0.05)
        form.addRow("Gamma", self.gamma)
        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(0.0, 1.0)
        self.opacity.setDecimals(3)
        self.opacity.setSingleStep(0.05)
        self.opacity_slider = self._slider()
        form.addRow("Opacity", self.opacity)
        form.addRow("", self.opacity_slider)

        self.auto_button = QPushButton("Auto Contrast (1–99%)")
        self.reset_button = QPushButton("Reset Contrast")
        form.addRow(self.auto_button)
        form.addRow(self.reset_button)
        self.scope = QLabel("Display settings do not modify image pixels.")
        self.scope.setWordWrap(True)
        form.addRow(self.scope)
        self.section.set_content_layout(form)
        outer.addWidget(self.section)

        self.minimum.valueChanged.connect(self._contrast_from_values)
        self.maximum.valueChanged.connect(self._contrast_from_values)
        self.minimum_slider.valueChanged.connect(self._contrast_from_sliders)
        self.maximum_slider.valueChanged.connect(self._contrast_from_sliders)
        self.gamma.valueChanged.connect(self._gamma_changed)
        self.opacity.valueChanged.connect(self._opacity_changed)
        self.opacity_slider.valueChanged.connect(self._opacity_slider_changed)
        self.auto_button.clicked.connect(self.auto_contrast)
        self.reset_button.clicked.connect(self.reset_contrast)
        viewer.layers.selection.events.active.connect(self._active_changed)
        viewer.layers.events.removed.connect(self._active_changed)
        self._active_changed()

    @staticmethod
    def _value_editor() -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(-1.0e30, 1.0e30)
        field.setDecimals(6)
        field.setKeyboardTracking(False)
        return field

    def _slider(self) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, self._SLIDER_STEPS)
        return slider

    @staticmethod
    def _is_image(layer) -> bool:
        return layer is not None and type(layer).__name__.lower() == "image"

    def _disconnect_layer(self) -> None:
        if self.layer is not None:
            for emitter, callback in self._callbacks:
                emitter.disconnect(callback)
        self._callbacks.clear()

    def _active_changed(self, event=None) -> None:
        active = self.viewer.layers.selection.active
        if active is self.layer and active in self.viewer.layers:
            return
        self._disconnect_layer()
        self.layer = active if self._is_image(active) and active in self.viewer.layers else None
        if self.layer is not None:
            for name in ("contrast_limits", "gamma", "opacity", "name"):
                emitter = getattr(self.layer.events, name, None)
                if emitter is not None:
                    callback = lambda event=None: self.sync_from_layer()
                    emitter.connect(callback)
                    self._callbacks.append((emitter, callback))
        self.sync_from_layer()

    def _display_range(self) -> tuple[float, float]:
        if self.layer is None:
            return 0.0, 1.0
        values = getattr(self.layer, "contrast_limits_range", None)
        if values is None:
            values = self.layer.contrast_limits
        low, high = (float(values[0]), float(values[1]))
        if not np.isfinite(low) or not np.isfinite(high) or high <= low:
            low, high = (float(self.layer.contrast_limits[0]), float(self.layer.contrast_limits[1]))
        if high <= low:
            high = low + 1.0
        return low, high

    def sync_from_layer(self) -> None:
        self._syncing = True
        enabled = self.layer is not None
        self.section.content.setEnabled(enabled)
        if not enabled:
            self.layer_name.setText("Select an Image layer")
            self.scope.setText("Display settings do not modify image pixels.")
            self._syncing = False
            return
        self.layer_name.setText(self.layer.name)
        low, high = (float(value) for value in self.layer.contrast_limits)
        range_low, range_high = self._display_range()
        for field, value in ((self.minimum, low), (self.maximum, high)):
            field.setValue(value)
        self.minimum_slider.setValue(self._to_slider(low, range_low, range_high))
        self.maximum_slider.setValue(self._to_slider(high, range_low, range_high))
        self.gamma.setValue(float(self.layer.gamma))
        self.opacity.setValue(float(self.layer.opacity))
        self.opacity_slider.setValue(round(float(self.layer.opacity) * self._SLIDER_STEPS))
        self._syncing = False

    def _to_slider(self, value: float, low: float, high: float) -> int:
        return round(np.clip((value - low) / (high - low), 0.0, 1.0) * self._SLIDER_STEPS)

    def _from_slider(self, value: int, low: float, high: float) -> float:
        return low + (high - low) * value / self._SLIDER_STEPS

    def _set_contrast(self, low: float, high: float) -> None:
        if self._syncing or self.layer is None or not np.isfinite([low, high]).all():
            return
        if high <= low:
            self.scope.setText("Minimum contrast must be lower than maximum contrast.")
            return
        self.layer.contrast_limits = (float(low), float(high))
        self.scope.setText("Display-only contrast updated; image pixels are unchanged.")

    def _contrast_from_values(self, *args) -> None:
        self._set_contrast(self.minimum.value(), self.maximum.value())

    def _contrast_from_sliders(self, *args) -> None:
        if self._syncing or self.layer is None:
            return
        low, high = self._display_range()
        self._set_contrast(
            self._from_slider(self.minimum_slider.value(), low, high),
            self._from_slider(self.maximum_slider.value(), low, high),
        )

    def _gamma_changed(self, value: float) -> None:
        if not self._syncing and self.layer is not None:
            self.layer.gamma = float(value)

    def _opacity_changed(self, value: float) -> None:
        if self._syncing or self.layer is None:
            return
        self.layer.opacity = float(value)
        self._syncing = True
        self.opacity_slider.setValue(round(float(value) * self._SLIDER_STEPS))
        self._syncing = False

    def _opacity_slider_changed(self, value: int) -> None:
        if not self._syncing:
            self.opacity.setValue(float(value) / self._SLIDER_STEPS)

    def _valid_values(self) -> tuple[np.ndarray, str]:
        data = np.asarray(self.layer.data)
        mask = self.project_state.roi_mask_yx
        metadata = getattr(self.layer, "metadata", {}) or {}
        can_use_mask = (
            mask is not None
            and data.ndim >= 2
            and tuple(data.shape[-2:]) == tuple(mask.shape)
            and any(
                metadata.get(key)
                for key in (
                    "nucytoseg_imported",
                    "nucytoseg_crop_preview",
                    "nucytoseg_normalization_preview",
                    "nucytoseg_z_correction_preview",
                )
            )
        )
        values = data[..., mask] if can_use_mask else data.reshape(-1)
        values = values[np.isfinite(values)]
        return values, "valid polygon ROI" if can_use_mask else "finite active-layer pixels"

    def auto_contrast(self) -> bool:
        if self.layer is None:
            return False
        values, scope = self._valid_values()
        if values.size == 0:
            self.scope.setText("Auto Contrast found no finite pixels.")
            return False
        low, high = np.percentile(values, (1.0, 99.0))
        if not np.isfinite(low) or not np.isfinite(high):
            self.scope.setText("Auto Contrast could not determine a finite range.")
            return False
        if high <= low:
            low = float(np.min(values))
            high = float(np.max(values))
        if high <= low:
            high = low + max(abs(low) * 1e-6, 1e-6)
        self.layer.contrast_limits = (float(low), float(high))
        self.scope.setText(f"Auto Contrast: 1–99% of {scope}; pixels are unchanged.")
        return True

    def reset_contrast(self) -> bool:
        if self.layer is None:
            return False
        self.layer.reset_contrast_limits()
        self.scope.setText("Contrast reset from the active layer data; pixels are unchanged.")
        return True

    def dispose(self) -> None:
        self._disconnect_layer()
        self.viewer.layers.selection.events.active.disconnect(self._active_changed)
        self.viewer.layers.events.removed.disconnect(self._active_changed)
