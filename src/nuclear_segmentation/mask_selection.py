"""Non-destructive 2D mask selection for the NuCytoSeg Review tool."""
from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtCore import QObject, Signal


@dataclass(frozen=True)
class _DisplayState:
    selected_label: int
    show_selected_label: bool
    contour: int


class MaskSelectionController(QObject):
    """Own mask selection separately from active-layer and editing state."""

    selection_changed = Signal(object, object)
    status_changed = Signal(str)

    def __init__(self, viewer, parent: QObject | None = None):
        super().__init__(parent)
        self.viewer = viewer
        self._tool = "pan_zoom"
        self._review_layer = None
        self._display_state: _DisplayState | None = None
        self._selected_by_layer: dict[object, int] = {}
        self._disposed = False
        viewer.layers.selection.events.active.connect(self._on_active_layer_changed)

    @staticmethod
    def _is_labels(layer) -> bool:
        return layer is not None and type(layer).__name__.lower() == "labels"

    @property
    def selected_layer(self):
        layer = self.viewer.layers.selection.active
        if not self._is_labels(layer):
            return None
        return layer

    @property
    def selected_mask_id(self) -> int | None:
        layer = self.selected_layer
        return None if layer is None else self._selected_by_layer.get(layer)

    def set_tool(self, tool: str) -> None:
        if tool == self._tool:
            return
        self._deactivate_review_layer()
        self._tool = tool
        if tool == "review":
            self._activate_review_layer(self.selected_layer)
        self._emit_selection()

    def _on_active_layer_changed(self, event=None) -> None:
        if self._disposed:
            return
        self._deactivate_review_layer()
        if self._tool == "review":
            self._activate_review_layer(self.selected_layer)
        self._emit_selection()

    def _activate_review_layer(self, layer) -> None:
        if not self._is_labels(layer) or layer not in self.viewer.layers:
            return
        self._review_layer = layer
        self._display_state = _DisplayState(
            selected_label=int(layer.selected_label),
            show_selected_label=bool(layer.show_selected_label),
            contour=int(layer.contour),
        )
        if self._on_mouse_press not in layer.mouse_drag_callbacks:
            layer.mouse_drag_callbacks.append(self._on_mouse_press)
        selected = self._selected_by_layer.get(layer)
        if selected is not None:
            self._apply_highlight(layer, selected)

    def _deactivate_review_layer(self) -> None:
        layer = self._review_layer
        state = self._display_state
        if layer is not None:
            if self._on_mouse_press in layer.mouse_drag_callbacks:
                layer.mouse_drag_callbacks.remove(self._on_mouse_press)
            if state is not None and layer in self.viewer.layers:
                layer.show_selected_label = state.show_selected_label
                layer.contour = state.contour
                layer.selected_label = state.selected_label
        self._review_layer = None
        self._display_state = None

    def _on_mouse_press(self, layer, event) -> None:
        if (
            self._disposed
            or self._tool != "review"
            or layer is not self._review_layer
            or self.viewer.dims.ndisplay != 2
        ):
            return
        value = layer.get_value(
            event.position,
            view_direction=event.view_direction,
            dims_displayed=event.dims_displayed,
            world=True,
        )
        self.select_mask(layer, value)

    def select_at_data_position(self, layer, position) -> int | None:
        """Select at a data coordinate; exposed for deterministic tests."""
        if (
            self._tool != "review"
            or layer is not self.selected_layer
            or self.viewer.dims.ndisplay != 2
        ):
            return None
        data = layer.data[0] if layer.multiscale else layer.data
        coordinates = tuple(int(round(float(value))) for value in position)
        if len(coordinates) != data.ndim or any(
            value < 0 or value >= data.shape[index]
            for index, value in enumerate(coordinates)
        ):
            value = None
        else:
            value = data[coordinates]
        return self.select_mask(layer, value)

    def select_mask(self, layer, value) -> int | None:
        """Select one ID on the active Labels layer without changing its data."""
        if (
            self._tool != "review"
            or layer is not self.selected_layer
            or layer is not self._review_layer
        ):
            return None
        background = int(layer.colormap.background_value)
        selected = background if value is None else int(value)
        if selected == background:
            self.clear_selection(layer)
            return None
        self._selected_by_layer[layer] = selected
        self._apply_highlight(layer, selected)
        self._emit_selection()
        self.status_changed.emit(f"Selected Mask ID: {selected}")
        return selected

    def _apply_highlight(self, layer, selected: int) -> None:
        layer.selected_label = int(selected)
        layer.contour = max(2, int(layer.contour))
        layer.show_selected_label = True

    def clear_selection(self, layer=None) -> None:
        layer = layer or self.selected_layer
        if layer is None:
            return
        self._selected_by_layer.pop(layer, None)
        if layer is self._review_layer and self._display_state is not None:
            state = self._display_state
            layer.show_selected_label = state.show_selected_label
            layer.contour = state.contour
            layer.selected_label = state.selected_label
        self._emit_selection()
        self.status_changed.emit("Mask selection cleared")

    def _emit_selection(self) -> None:
        layer = self.selected_layer
        selected = None if layer is None else self._selected_by_layer.get(layer)
        self.selection_changed.emit(layer, selected)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._deactivate_review_layer()
        self.viewer.layers.selection.events.active.disconnect(
            self._on_active_layer_changed
        )
        self._selected_by_layer.clear()
