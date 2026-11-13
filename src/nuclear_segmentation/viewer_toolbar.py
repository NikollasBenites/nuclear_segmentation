"""Explicit embedded-viewer controls that avoid napari window actions."""
from __future__ import annotations

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QButtonGroup, QToolButton, QVBoxLayout, QWidget


class ViewerToolbar(QWidget):
    """Compact toolbar operating directly on the selected public layer API."""

    status_changed = Signal(str)

    def __init__(self, viewer, parent: QWidget | None = None):
        super().__init__(parent)
        self.viewer = viewer
        self.setObjectName("viewerToolbar")
        self.setFixedWidth(48)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 8, 5, 8)
        layout.setSpacing(5)
        self.buttons: dict[str, QToolButton] = {}
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        definitions = (
            ("2d", "2D", "Switch to 2D slice view", False),
            ("3d", "3D", "Switch to 3D volume view", False),
            ("pan_zoom", "↔", "Pan and zoom", True),
            ("review", "◎", "Review/select the active layer without editing", True),
            ("paint", "✎", "Paint the active Labels layer", True),
            ("erase", "⌫", "Erase from the active Labels layer", True),
            ("pick", "⌖", "Pick a value from the active Labels layer", True),
        )
        for key, text, tooltip, is_tool in definitions:
            button = QToolButton()
            button.setObjectName(f"viewerTool_{key}")
            button.setText(text)
            button.setToolTip(tooltip)
            button.setFixedSize(38, 38)
            if is_tool:
                button.setCheckable(True)
                self.tool_group.addButton(button)
                button.clicked.connect(
                    lambda checked=False, mode=key: self.activate_tool(mode)
                )
            elif key == "2d":
                button.clicked.connect(lambda: self.set_ndisplay(2))
            else:
                button.clicked.connect(lambda: self.set_ndisplay(3))
            self.buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        viewer.layers.selection.events.active.connect(self._active_layer_changed)
        self.buttons["pan_zoom"].setChecked(True)
        self._active_layer_changed()

    def active_layer(self):
        return self.viewer.layers.selection.active

    def _is_labels(self, layer) -> bool:
        return layer is not None and type(layer).__name__.lower() == "labels"

    def _active_layer_changed(self, event=None) -> None:
        editable = self._is_labels(self.active_layer())
        for key in ("paint", "erase", "pick"):
            self.buttons[key].setEnabled(editable)
        layer = self.active_layer()
        self.status_changed.emit(
            f"Active layer: {layer.name}" if layer is not None else "No active layer"
        )

    def set_ndisplay(self, dimensions: int) -> bool:
        if dimensions not in (2, 3):
            return False
        self.viewer.dims.ndisplay = dimensions
        self.status_changed.emit(f"{dimensions}D display active")
        return True

    def activate_tool(self, mode: str) -> bool:
        layer = self.active_layer()
        requested = "pan_zoom" if mode == "review" else mode
        if mode in {"paint", "erase", "pick"} and not self._is_labels(layer):
            self.status_changed.emit(f"{mode.title()} requires an active Labels layer")
            return False
        if layer is None:
            self.status_changed.emit("Select a layer before choosing a viewer tool")
            return False
        try:
            layer.mode = requested
        except (AttributeError, ValueError) as exc:
            self.status_changed.emit(f"Viewer tool unavailable: {exc}")
            return False
        self.buttons[mode].setChecked(True)
        self.status_changed.emit(f"{mode.replace('_', ' ').title()} tool active")
        return True

    def dispose(self) -> None:
        self.viewer.layers.selection.events.active.disconnect(self._active_layer_changed)
