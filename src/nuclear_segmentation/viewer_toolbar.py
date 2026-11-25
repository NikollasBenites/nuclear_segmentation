"""Explicit embedded-viewer controls that avoid napari window actions."""
from __future__ import annotations

from qtpy.QtCore import QSize, Signal
from qtpy.QtWidgets import QButtonGroup, QToolButton, QGridLayout, QWidget

from .matching_pair import MATCHING_LAYER
from .ui_icons import icon


class ViewerToolbar(QWidget):
    """Compact toolbar operating directly on the selected public layer API."""

    status_changed = Signal(str)
    tool_changed = Signal(str)
    display_about_to_change = Signal(int)

    def __init__(self, viewer, parent: QWidget | None = None):
        super().__init__(parent)
        self.viewer = viewer
        self.setObjectName("viewerToolbar")
        layout = QGridLayout(self)
        layout.setContentsMargins(5, 8, 5, 8)
        layout.setSpacing(5)
        self.buttons: dict[str, QToolButton] = {}
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        definitions = (
            ("2d", "view2d", "Switch to 2D slice view", False),
            ("3d", "view3d", "Switch to 3D volume view", False),
            ("fit", "fit", "Fit image to view", False),
            ("pan_zoom", "pan", "Pan and zoom", True),
            ("review", "select", "Review/select without editing", True),
            ("paint", "paint", "Paint the active Labels layer", True),
            ("erase", "erase", "Erase from the active Labels layer", True),
            ("pick", "pick", "Pick a value from the active Labels layer", True),
        )
        for key, icon_name, tooltip, is_tool in definitions:
            button = QToolButton()
            button.setObjectName(f"viewerTool_{key}")
            button.setIcon(icon(icon_name))
            button.setIconSize(QSize(25, 25))
            button.setToolTip(tooltip)
            button.setFixedSize(32, 32)
            button.setAccessibleName(tooltip)
            if is_tool:
                button.setCheckable(True)
                self.tool_group.addButton(button)
                button.clicked.connect(
                    lambda checked=False, mode=key: self.activate_tool(mode)
                )
            elif key == "2d":
                button.clicked.connect(lambda: self.set_ndisplay(2))
            elif key == "fit":
                button.clicked.connect(self.fit_to_view)
            else:
                button.clicked.connect(lambda: self.set_ndisplay(3))
            self.buttons[key] = button
            position = len(self.buttons) - 1
            layout.addWidget(button, position // 4, position % 4)
        self._observed_layer = None
        viewer.layers.selection.events.active.connect(self._active_layer_changed)
        self.buttons["pan_zoom"].setChecked(True)
        self._active_layer_changed()

    def active_layer(self):
        return self.viewer.layers.selection.active

    def _is_labels(self, layer) -> bool:
        return (layer is not None and type(layer).__name__.lower() == "labels"
                and not layer.metadata.get(MATCHING_LAYER))

    def _active_layer_changed(self, event=None) -> None:
        self._disconnect_observed_layer()
        layer = self.active_layer()
        if self._is_labels(layer):
            self._observed_layer = layer
            layer.events.mode.connect(self._sync_layer_mode)
            layer.events.visible.connect(self._refresh_tools)
            layer.events.editable.connect(self._refresh_tools)
        self._refresh_tools()
        self._sync_layer_mode()
        self.status_changed.emit(
            f"Active layer: {layer.name}" if layer is not None else "No active layer"
        )

    def _disconnect_observed_layer(self):
        if self._observed_layer is not None:
            self._observed_layer.events.mode.disconnect(self._sync_layer_mode)
            self._observed_layer.events.visible.disconnect(self._refresh_tools)
            self._observed_layer.events.editable.disconnect(self._refresh_tools)
            self._observed_layer = None

    def _sync_layer_mode(self, event=None):
        layer = self._observed_layer
        mode = str(layer.mode) if layer is not None else "pan_zoom"
        if mode == "pan_zoom" and self.buttons["review"].isChecked():
            return
        if mode in ("paint", "erase", "pan_zoom", "pick") and not self.buttons[mode].isChecked():
            self.buttons[mode].setChecked(True)
            self.tool_changed.emit(mode)

    def _refresh_tools(self, event=None):
        layer = self.active_layer()
        labels = self._is_labels(layer)
        for key in ("review", "pick"):
            self.buttons[key].setEnabled(labels)
        for key in ("paint", "erase"):
            editing = labels and (layer.metadata.get("nucytoseg_edit_preview") or layer.metadata.get("nucytoseg_demo"))
            self.buttons[key].setVisible(bool(editing))
            self.buttons[key].setEnabled(labels and layer.editable and layer.visible)

    def set_ndisplay(self, dimensions: int) -> bool:
        if dimensions not in (2, 3):
            return False
        self.display_about_to_change.emit(dimensions)
        self.viewer.dims.ndisplay = dimensions
        self.status_changed.emit(f"{dimensions}D display active")
        return True

    def fit_to_view(self) -> None:
        """Reset the napari camera without changing layer data or calibration."""
        self.viewer.reset_view()
        self.status_changed.emit("Image fitted to view")

    def activate_tool(self, mode: str) -> bool:
        layer = self.active_layer()
        requested = "pan_zoom" if mode == "review" else mode
        if mode in {"review", "paint", "erase", "pick"} and not self._is_labels(layer):
            self.status_changed.emit(f"{mode.title()} requires an active Labels layer")
            return False
        if layer is None:
            self.status_changed.emit("Select a layer before choosing a viewer tool")
            return False
        if mode in {"paint", "erase"} and (not layer.editable or not layer.visible):
            self.status_changed.emit("Choose a visible, editable Labels layer.")
            return False
        try:
            if layer is self._observed_layer:
                with layer.events.mode.blocker(self._sync_layer_mode):
                    layer.mode = requested
            else:
                layer.mode = requested
        except (AttributeError, ValueError) as exc:
            self.status_changed.emit(f"Viewer tool unavailable: {exc}")
            return False
        self.buttons[mode].setChecked(True)
        self.tool_changed.emit(mode)
        self.status_changed.emit(f"{mode.replace('_', ' ').title()} tool active")
        return True

    def dispose(self) -> None:
        self._disconnect_observed_layer()
        self.viewer.layers.selection.events.active.disconnect(self._active_layer_changed)
