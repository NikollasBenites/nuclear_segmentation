"""Non-destructive 2D/3D mask selection for the NuCytoSeg Review tool."""
from __future__ import annotations

import numpy as np
from qtpy.QtCore import QObject, Signal

from .label_geometry import selected_bounds
from .matching_pair import MATCHING_LAYER


INTERNAL_HIGHLIGHT_KEY = "nucytoseg_selection_highlight"


def bounded_highlight(layer, selected, bounds=None):
    """Return a padded binary crop and its local-to-world transform."""
    data = layer.data[0] if layer.multiscale else layer.data
    if bounds is None:
        bounds = selected_bounds(data, selected)
    if bounds is None:
        return
    minimum, maximum = bounds
    slices = tuple(slice(int(low), int(high)) for low, high in zip(minimum, maximum))
    crop = np.pad((np.asarray(data[slices]) == selected).astype(np.uint8), 1)
    start = minimum.astype(float) - 1
    origin = np.asarray(layer.data_to_world(start), dtype=float)
    ndim = data.ndim
    identity = np.eye(ndim)
    linear = np.column_stack(
        [
            np.asarray(layer.data_to_world(start + identity[axis]), dtype=float)
            - origin
            for axis in range(ndim)
        ]
    )
    affine = np.eye(ndim + 1)
    affine[:ndim, :ndim] = linear
    affine[:ndim, -1] = origin
    return crop, affine


class MaskSelectionController(QObject):
    """Own object selection separately from active-layer and editing state."""

    selection_changed = Signal(object, object)
    selection_cleared = Signal(object)
    status_changed = Signal(str)

    def __init__(self, viewer, parent: QObject | None = None):
        super().__init__(parent)
        self.viewer = viewer
        self._tool = "pan_zoom"
        self._review_layer = None
        self._highlight_layer = None
        self._selected_by_layer: dict[object, int] = {}
        self._updating_overlay = False
        self._disposed = False
        viewer.layers.selection.events.active.connect(self._on_active_layer_changed)
        viewer.layers.events.removed.connect(self._layer_removed)
        viewer.dims.events.ndisplay.connect(self._on_ndisplay_changed)

    @staticmethod
    def _is_labels(layer) -> bool:
        metadata = getattr(layer, "metadata", {}) or {}
        return (
            layer is not None
            and type(layer).__name__.lower() == "labels"
            and not (metadata.get(INTERNAL_HIGHLIGHT_KEY) or metadata.get(MATCHING_LAYER))
        )

    @property
    def selected_layer(self):
        layer = self.viewer.layers.selection.active
        if not self._is_labels(layer) or not layer.visible:
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
            self._activate_review_layer(self.viewer.layers.selection.active)
        self._emit_selection()

    def _on_active_layer_changed(self, event=None) -> None:
        if self._disposed or self._updating_overlay:
            return
        self._deactivate_review_layer()
        if self._tool == "review":
            self._activate_review_layer(self.viewer.layers.selection.active)
        self._emit_selection()

    def _activate_review_layer(self, layer) -> None:
        if not self._is_labels(layer) or layer not in self.viewer.layers:
            return
        self._review_layer = layer
        if self._on_mouse_press not in layer.mouse_drag_callbacks:
            layer.mouse_drag_callbacks.append(self._on_mouse_press)
        layer.events.visible.connect(self._on_review_visibility_changed)
        layer.events.name.connect(self._on_review_name_changed)
        selected = self._selected_by_layer.get(layer)
        if selected is not None and layer.visible:
            self._update_highlight(layer, selected)

    def _deactivate_review_layer(self) -> None:
        layer = self._review_layer
        if layer is not None:
            if self._on_mouse_press in layer.mouse_drag_callbacks:
                layer.mouse_drag_callbacks.remove(self._on_mouse_press)
            layer.events.visible.disconnect(self._on_review_visibility_changed)
            layer.events.name.disconnect(self._on_review_name_changed)
        self._remove_highlight()
        self._review_layer = None

    def _on_review_visibility_changed(self, event=None) -> None:
        layer = self._review_layer
        self._remove_highlight()
        if layer is not None and layer.visible:
            selected = self._selected_by_layer.get(layer)
            if selected is not None:
                self._update_highlight(layer, selected)
        self._emit_selection()

    def _on_review_name_changed(self, event=None) -> None:
        self._emit_selection()

    def _on_ndisplay_changed(self, event=None) -> None:
        if self._highlight_layer is not None:
            self._highlight_layer.contour = 2 if self.viewer.dims.ndisplay == 2 else 0

    def prepare_display_change(self, dimensions: int) -> None:
        """Disable 2D contours before napari constructs a 3D Labels view."""
        if dimensions == 3 and self._highlight_layer is not None:
            self._highlight_layer.contour = 0

    def _on_mouse_press(self, layer, event) -> None:
        if (
            self._disposed
            or getattr(event, "button", 1) != 1
            or self._tool != "review"
            or layer is not self._review_layer
            or not layer.visible
        ):
            return
        if self.viewer.dims.ndisplay == 3 and event.view_direction is None:
            self.status_changed.emit("3D selection unavailable for this camera event")
            return
        value = layer.get_value(
            event.position,
            view_direction=event.view_direction,
            dims_displayed=list(event.dims_displayed),
            world=True,
        )
        self.select_mask(layer, self._normalize_value(value))

    @staticmethod
    def _normalize_value(value):
        if isinstance(value, tuple) and len(value) == 2:
            return value[1]
        return value

    def pick_from_ray(
        self,
        layer,
        position,
        view_direction,
        dims_displayed,
        *,
        world: bool = True,
    ) -> int | None:
        """Pick the camera-nearest non-background label along a 3D ray."""
        if (
            self._tool != "review"
            or layer is not self.selected_layer
            or not layer.visible
            or len(dims_displayed) != 3
        ):
            return None
        value = layer.get_value(
            position,
            view_direction=view_direction,
            dims_displayed=list(dims_displayed),
            world=world,
        )
        return self.select_mask(layer, self._normalize_value(value))

    def select_at_data_position(self, layer, position) -> int | None:
        """Select at a data coordinate; exposed for deterministic 2D tests."""
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
        self._update_highlight(layer, selected)
        self._emit_selection()
        self.status_changed.emit(f"Selected Mask ID: {selected}")
        return selected

    def _update_highlight(self, layer, selected: int) -> None:
        self._remove_highlight()
        geometry = bounded_highlight(layer, selected)
        if geometry is None:
            return
        crop, affine = geometry
        active = self.viewer.layers.selection.active
        self._updating_overlay = True
        try:
            overlay = self.viewer.add_labels(
                crop,
                name="Selected mask highlight",
                colormap={None: "transparent", 0: "transparent", 1: "#63d9d0"},
                opacity=0.8,
                blending="translucent",
                affine=affine,
                metadata={INTERNAL_HIGHLIGHT_KEY: True},
            )
            overlay.editable = False
            overlay.contour = 2 if self.viewer.dims.ndisplay == 2 else 0
            self._highlight_layer = overlay
            if active in self.viewer.layers:
                self.viewer.layers.selection.active = active
        finally:
            self._updating_overlay = False

    def _remove_highlight(self) -> None:
        overlay = self._highlight_layer
        self._highlight_layer = None
        if overlay is None or overlay not in self.viewer.layers:
            return
        active = self.viewer.layers.selection.active
        self._updating_overlay = True
        try:
            self.viewer.layers.remove(overlay)
            if active in self.viewer.layers:
                self.viewer.layers.selection.active = active
        finally:
            self._updating_overlay = False

    def _layer_removed(self, event):
        self._selected_by_layer.pop(event.value, None)
        if event.value is self._highlight_layer:
            self._highlight_layer = None
        if event.value is self._review_layer:
            self._deactivate_review_layer()

    def label_for_layer(self, layer):
        return self._selected_by_layer.get(layer)

    def restore_selection(self, layer, label):
        """Reconnect a saved selection to its newly created Labels layer."""
        self._selected_by_layer[layer] = label
        if not layer.visible:
            return
        if self.viewer.layers.selection.active is not layer:
            self.viewer.layers.selection.active = layer
        elif self._tool == "review" and layer.visible:
            self._update_highlight(layer, label)
        self._emit_selection()

    def clear_selection(self, layer=None) -> None:
        layer = layer or self.selected_layer
        if layer is None:
            return
        previous = self._selected_by_layer.pop(layer, None)
        if layer is self._review_layer:
            self._remove_highlight()
        self._emit_selection()
        if previous is not None:
            self.selection_cleared.emit(layer)
        self.status_changed.emit("Mask selection cleared")

    def clear_all(self) -> None:
        self._selected_by_layer.clear()
        self._remove_highlight()
        self._emit_selection()

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
        self.viewer.layers.events.removed.disconnect(self._layer_removed)
        self.viewer.dims.events.ndisplay.disconnect(self._on_ndisplay_changed)
        self._selected_by_layer.clear()
