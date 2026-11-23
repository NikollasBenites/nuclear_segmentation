"""Display preferences for session-owned napari Labels layers."""
from __future__ import annotations

from qtpy.QtCore import QObject, Signal


class SessionMaskDisplay(QObject):
    """Apply display settings to the current generation of session-owned layers."""

    changed = Signal()
    status = Signal(str)

    _keys = {
        "edited": "nucytoseg_edited_segmentation",
        "raw": "nucytoseg_raw_segmentation",
        "filtered": "nucytoseg_filtered_segmentation",
        "roi": "nucytoseg_scientific_roi",
    }

    def __init__(self, viewer, state, selection, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.state = state
        self.selection = selection
        self.layers = {}
        self._syncing = False
        self.suspended = False
        viewer.layers.events.inserted.connect(self._inserted)
        viewer.layers.events.removed.connect(self._removed)

    @property
    def preferences(self):
        return self.state.active_session.display_state

    def _inserted(self, event):
        layer = event.value
        if not self.state.owns_mask_layer(layer):
            return
        role = next((key for key, flag in self._keys.items() if layer.metadata.get(flag)), None)
        if role is None or role in self.layers:
            return
        self.layers[role] = layer
        layer.events.visible.connect(self._appearance_changed)
        layer.events.opacity.connect(self._appearance_changed)
        self._restore_layer(role, layer)

    def _removed(self, event):
        for role, layer in tuple(self.layers.items()):
            if layer is event.value:
                self._disconnect(layer)
                del self.layers[role]

    def _disconnect(self, layer):
        layer.events.visible.disconnect(self._appearance_changed)
        layer.events.opacity.disconnect(self._appearance_changed)

    def _appearance_changed(self, event):
        layer = event.source
        if self.suspended or self._syncing or not self.state.owns_mask_layer(layer):
            return
        role = next(key for key, value in self.layers.items() if value is layer)
        settings = self.preferences.setdefault(role, {})
        settings[event.type] = getattr(layer, event.type)
        if event.type == "visible" and role != "roi":
            selected_target = self._mode() == "selected" and role == self.preferences["selected_layer"]
            if not selected_target:
                for key in ("raw", "edited", "filtered"):
                    other = self.layers.get(key)
                    if other is not None:
                        self.preferences.setdefault(key, {})["visible"] = other.visible
                        other.show_selected_label = False
                self.preferences["mode"] = "custom"
            self._status()
        self.changed.emit()

    def _mode(self):
        return self.preferences.get("mode", "filtered" if self.state.filtering_is_current else "raw")

    def _restore_layer(self, role, layer):
        settings = self.preferences.get(role, {})
        mode = self._mode()
        self._syncing = True
        try:
            if "opacity" in settings and layer.opacity != settings["opacity"]:
                layer.opacity = settings["opacity"]
            if role == "roi":
                layer.visible = settings.get("visible", False)
                return
            selected = (mode == "selected" and self.preferences.get("selected_layer") == role
                        and self.preferences.get("geometry_revision") == self.state.active_session.geometry_revision)
            if selected and layer.selected_label != self.preferences["selected_id"]:
                layer.selected_label = self.preferences["selected_id"]
            if layer.show_selected_label != selected:
                layer.show_selected_label = selected
            if mode == "custom":
                layer.visible = settings.get("visible", role == "raw")
            elif mode == "selected":
                layer.visible = selected and settings.get("visible", True)
            else:
                layer.visible = mode == role
        finally:
            self._syncing = False

    def suspend(self):
        self.suspended = True
        for layer in self.layers.values():
            layer.visible = False

    def resume(self):
        self.suspended = False
        self.sync(restore_selection=True)

    def sync(self, *, restore_selection=False):
        if self.suspended:
            return
        mode = self._mode()
        filtered = self.layers.get("filtered")
        retained = (self.state.filtering_result or {}).get("retained_ids", ())
        if mode == "selected":
            role = self.preferences["selected_layer"]
            label = self.preferences["selected_id"]
            if (self.preferences["geometry_revision"] != self.state.active_session.geometry_revision
                    or (role == "filtered" and label not in retained)):
                self.preferences["mode"] = "filtered" if self.state.filtering_is_current else "raw"
        if filtered is not None:
            selected = self.selection.label_for_layer(filtered)
            if selected is not None and selected not in retained:
                self.selection.clear_selection(filtered)
        if self._mode() == "edited" and "edited" not in self.layers:
            self.preferences["mode"] = "raw"
        if self._mode() == "filtered" and not self.state.filtering_is_current:
            self.preferences["mode"] = "raw"
        for role, layer in self.layers.items():
            if self.state.owns_mask_layer(layer):
                self._restore_layer(role, layer)
        if self._mode() == "selected" and restore_selection:
            layer = self.layers.get(self.preferences["selected_layer"])
            label = self.preferences["selected_id"]
            if (layer is not None and self.state.owns_mask_layer(layer)
                    and self.selection.label_for_layer(layer) != label):
                self.selection.restore_selection(layer, label)
        self._status()

    def _status(self):
        mode = self._mode()
        if "raw" not in self.layers:
            message = "No segmentation mask layer is available."
        elif mode == "selected":
            role = self.preferences["selected_layer"]
            layer = self.layers.get(role)
            visibility = "" if layer is not None and layer.visible else " · hidden"
            message = f"Selected only: Mask ID {self.preferences['selected_id']} ({role}){visibility}."
        elif mode == "custom":
            visible = [key for key in ("raw", "edited", "filtered") if key in self.layers and self.layers[key].visible]
            message = "Visible masks: " + (" and ".join(visible) if visible else "none") + "."
        else:
            message = {"raw": "Showing all raw segmentation masks.", "edited": "Showing committed edited masks.",
                       "filtered": "Showing all retained masks."}[mode]
        self.status.emit(message)

    def show(self, mode):
        if mode == "filtered" and (not self.state.filtering_is_current or "filtered" not in self.layers):
            self.status.emit(f"Calculate current filtering results to show retained masks. Current display: {self._mode()}.")
            return
        if mode == "selected":
            layer = self.selection.selected_layer
            label = self.selection.selected_mask_id
            role = next((key for key in ("raw", "edited", "filtered") if self.layers.get(key) is layer), None)
            if layer is None or label is None or role is None or not self.state.owns_mask_layer(layer):
                self.status.emit("Select a segmentation mask with Review / Select first.")
                return
            self.preferences.update(selected_layer=role, selected_id=int(label),
                                    geometry_revision=self.state.active_session.geometry_revision)
        for key in ("raw", "edited", "filtered"):
            self.preferences.get(key, {}).pop("visible", None)
        self.preferences["mode"] = mode
        self.sync()
        if mode in ("raw", "edited", "filtered") and mode in self.layers:
            self.viewer.layers.selection.active = self.layers[mode]
        self.changed.emit()

    def selection_cleared(self, layer):
        if self._mode() == "selected" and self.layers.get(self.preferences["selected_layer"]) is layer:
            role = self.preferences["selected_layer"]
            self.show("raw" if role == "filtered" and not self.state.filtering_is_current else role)

    def selection_changed(self, layer, label):
        if (label is not None and self._mode() == "selected"
                and self.layers.get(self.preferences["selected_layer"]) is layer
                and self.state.owns_mask_layer(layer) and layer.show_selected_label
                and label != self.preferences["selected_id"]):
            self.preferences["selected_id"] = int(label)
            layer.selected_label = int(label)
            self._status()
            self.changed.emit()

    def dispose(self):
        self.viewer.layers.events.inserted.disconnect(self._inserted)
        self.viewer.layers.events.removed.disconnect(self._removed)
        for layer in self.layers.values():
            self._disconnect(layer)
        self.layers.clear()
