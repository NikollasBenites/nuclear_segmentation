"""Right-click actions for the active session's visible napari Labels layer."""
from dataclasses import dataclass

import numpy as np
from qtpy.QtGui import QCursor
from qtpy.QtWidgets import QApplication, QMenu

from .geometry_edit import effective_masks


@dataclass(frozen=True)
class MaskHit:
    layer: object
    label: int
    revision: object
    transaction: object
    position: tuple
    axes: tuple
    direction: tuple | None


class MaskContextMenu:
    actions = (
        ("select", "Select Mask"), ("isolate", "Show Selected Only"),
        ("add_merge", "Add to Merge Selection"), ("split", "Split"),
        ("remove", "Remove"), ("start", "Start Editing"), ("properties", "Show Mask Properties"),
    )

    def __init__(self, window):
        self.window = window
        self.panel = window.geometry_panel
        self.viewer = self.panel.viewer
        self.state = window.project_state
        self.layer = self.menu = None
        self.viewer.layers.selection.events.active.connect(self._bind)
        self.viewer.dims.events.ndisplay.connect(self._dismiss)
        self.state.active_session_about_to_change.connect(self._detach)
        self.state.segmentation_result_changed.connect(self._bind)
        self.window.viewer_toolbar.tool_changed.connect(self._dismiss)
        self._bind()

    def _dismiss(self, *args):
        if self.menu is not None:
            self.menu.hide()

    def _detach(self):
        self._dismiss()
        if self.layer is not None:
            self.layer.mouse_drag_callbacks.remove(self._gesture)
            self.layer = None

    def _bind(self, *args):
        self._detach()
        layer = self.viewer.layers.selection.active
        if layer is not None and self.state.owns_mask_layer(layer) and any(
            layer.metadata.get(key) for key in ("nucytoseg_edit_preview", "nucytoseg_raw_segmentation",
                                                "nucytoseg_edited_segmentation", "nucytoseg_filtered_segmentation")
        ):
            self.layer = layer
            layer.mouse_drag_callbacks.append(self._gesture)

    def _can_pick(self, layer):
        if (layer is not self.layer or layer is not self.viewer.layers.selection.active
                or not layer.visible or layer.opacity <= 0 or str(layer.mode) != "pan_zoom"
                or not self.state.owns_mask_layer(layer) or self.window._import_thread is not None):
            return False
        if self.panel.busy:
            return layer is self.panel.layer and self.panel._owns_preview()
        layers = self.window.mask_display.layers
        return any(layer is layers.get(role) for role in ("raw", "edited", "filtered"))

    def _label_at(self, layer, position, axes, direction):
        if not self._can_pick(layer) or tuple(self.viewer.dims.displayed) != axes:
            return None
        if len(axes) == 3 and (direction is None or not np.all(np.isfinite(direction))
                               or not np.any(direction) or layer.show_selected_label or str(layer.depiction) != "volume"):
            return None
        value = layer.get_value(position, dims_displayed=list(axes), view_direction=direction, world=True)
        if value is None or int(value) == 0 or (layer.show_selected_label and value != layer.selected_label):
            return None
        return int(value)

    def _gesture(self, layer, event):
        # VisPy reports secondary mouse presses as button 2. Leave all native
        # button/drag dispatch intact, including camera zoom with the same button.
        if (getattr(event, "button", None) != 2 or event.modifiers or not self._can_pick(layer)):
            return
        start = np.asarray(event.pos, dtype=float).copy()
        position, axes = tuple(event.position), tuple(event.dims_displayed)
        direction = None if event.view_direction is None else tuple(event.view_direction)
        label = self._label_at(layer, position, axes, direction)
        if label is None:
            return
        hit = MaskHit(layer, label, self.state.session_revision(), self.panel.edit, position, axes, direction)
        yield
        while event.type == "mouse_move":
            if event.modifiers or np.abs(np.asarray(event.pos) - start).sum() >= QApplication.startDragDistance():
                return
            yield
        if (event.type == "mouse_release" and not event.modifiers
                and np.abs(np.asarray(event.pos) - start).sum() < QApplication.startDragDistance()
                and self._current(hit)):
            self._popup(hit)

    def _current(self, hit):
        return (hit.revision == self.state.session_revision() and hit.transaction is self.panel.edit
                and self._label_at(hit.layer, hit.position, hit.axes, hit.direction) == hit.label)

    def _available(self, hit):
        edit = self.panel.edit
        if edit is None:
            allowed = {"select", "isolate", "properties"}
            review = self.window.context_panel.mask_review_page
            source = effective_masks(self.state.segmentation_result)
            if (self.state.segmentation_is_current and self.window._project_idle()
                    and not review._filter_timer.isActive()
                    and (source is hit.layer.data or any((plane == hit.label).any() for plane in source))):
                allowed.add("start")
            return allowed
        if edit.operation != "paint/erase":
            return {"properties"}
        allowed = {"select", "properties"}
        if np.array_equal(edit.base, edit.preview):
            allowed.update(("add_merge", "split", "remove"))
        return allowed

    def _popup(self, hit):
        self._dismiss()
        menu = QMenu(self.window)
        self.menu = menu
        menu.addSection(f"{hit.layer.name} · Mask {hit.label}")
        allowed = self._available(hit)
        for key, title in self.actions:
            action = menu.addAction(title)
            action.setEnabled(key in allowed)
            action.triggered.connect(lambda checked=False, key=key: self._invoke(hit, key))

        def release():
            if self.menu is menu:
                self.menu = None
            menu.deleteLater()

        menu.aboutToHide.connect(release)
        menu.popup(QCursor.pos())

    def _invoke(self, hit, key):
        if not self._current(hit) or key not in self._available(hit):
            return
        window = self.window
        if key in {"start", "properties"}:
            window.toggle_context_action.setChecked(True)
            window.set_workflow("mask_review")
            window.navigation.set_subpage("mask_editing", emit=True)
        window.viewer_toolbar.activate_tool("review")
        if window.mask_selection.select_mask(hit.layer, hit.label) != hit.label:
            return
        if key == "isolate":
            window.mask_display.show("selected")
        elif key == "properties":
            review = window.context_panel.mask_review_page
            review.show_context_properties(hit.layer, hit.label)
        elif key != "select":
            self.panel.run(key)

    def dispose(self):
        self._detach()
        self.viewer.layers.selection.events.active.disconnect(self._bind)
        self.viewer.dims.events.ndisplay.disconnect(self._dismiss)
        self.state.active_session_about_to_change.disconnect(self._detach)
        self.state.segmentation_result_changed.disconnect(self._bind)
        self.window.viewer_toolbar.tool_changed.disconnect(self._dismiss)
