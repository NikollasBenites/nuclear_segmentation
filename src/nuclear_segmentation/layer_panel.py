"""Event-driven, application-owned layer tree for embedded napari models."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QStandardItem, QStandardItemModel
from qtpy.QtWidgets import QAbstractItemView, QTreeView, QVBoxLayout, QWidget


LAYER_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class LayerTreeModel(QStandardItemModel):
    """Grouped adapter over a napari LayerList without copying layer data."""

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self._syncing = False
        self._disposed = False
        self._callbacks: dict[Any, tuple[Callable, Callable]] = {}
        self.setHorizontalHeaderLabels(["Layer", "Type"])
        self.itemChanged.connect(self._on_item_changed)
        for name in ("inserted", "removed", "moved", "reordered"):
            emitter = getattr(viewer.layers.events, name, None)
            if emitter is not None:
                emitter.connect(self._rebuild_from_event)
        self.rebuild()

    @staticmethod
    def group_for(layer) -> str:
        layer_type = type(layer).__name__.lower()
        metadata = getattr(layer, "metadata", {}) or {}
        role = str(metadata.get("mask_type", "")).lower()
        if layer_type == "image":
            return "Image Layers"
        if layer_type == "labels" or role in {"nuclear", "cytoplasmic"}:
            return "Segmentation Layers"
        return "Review Layers"

    def _disconnect_layer_callbacks(self) -> None:
        for layer, callbacks in self._callbacks.items():
            layer.events.name.disconnect(callbacks[0])
            layer.events.visible.disconnect(callbacks[1])
        self._callbacks.clear()

    def _rebuild_from_event(self, event=None) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        if self._disposed:
            return
        self._syncing = True
        self._disconnect_layer_callbacks()
        self.removeRows(0, self.rowCount())
        groups: dict[str, QStandardItem] = {}
        for title in ("Image Layers", "Segmentation Layers", "Review Layers"):
            group = QStandardItem(title)
            group.setEditable(False)
            group.setSelectable(False)
            type_cell = QStandardItem("")
            type_cell.setEditable(False)
            self.appendRow((group, type_cell))
            groups[title] = group
        for layer in reversed(self.viewer.layers):
            name_item = QStandardItem(layer.name)
            name_item.setData(layer, LAYER_ROLE)
            name_item.setCheckable(True)
            name_item.setCheckState(
                Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked
            )
            type_item = QStandardItem(type(layer).__name__)
            type_item.setEditable(False)
            type_item.setData(layer, LAYER_ROLE)
            groups[self.group_for(layer)].appendRow((name_item, type_item))
            name_callback = lambda event, current=layer: self._sync_layer(current)
            visible_callback = lambda event, current=layer: self._sync_layer(current)
            layer.events.name.connect(name_callback)
            layer.events.visible.connect(visible_callback)
            self._callbacks[layer] = (name_callback, visible_callback)
        self._syncing = False

    def item_for_layer(self, layer) -> QStandardItem | None:
        for row in range(self.rowCount()):
            group = self.item(row, 0)
            for child_row in range(group.rowCount()):
                item = group.child(child_row, 0)
                if item.data(LAYER_ROLE) is layer:
                    return item
        return None

    def _sync_layer(self, layer) -> None:
        if self._disposed or self._syncing:
            return
        item = self.item_for_layer(layer)
        if item is None:
            return
        self._syncing = True
        item.setText(layer.name)
        item.setCheckState(
            Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked
        )
        self._syncing = False

    def _on_item_changed(self, item: QStandardItem) -> None:
        if self._syncing:
            return
        layer = item.data(LAYER_ROLE)
        if layer is None or layer not in self.viewer.layers:
            return
        self._syncing = True
        if layer.name != item.text():
            layer.name = item.text()
        layer.visible = item.checkState() == Qt.CheckState.Checked
        self._syncing = False

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._disconnect_layer_callbacks()
        for name in ("inserted", "removed", "moved", "reordered"):
            emitter = getattr(self.viewer.layers.events, name, None)
            if emitter is not None:
                emitter.disconnect(self._rebuild_from_event)
        self.removeRows(0, self.rowCount())


class LayerPanel(QWidget):
    """Tree view synchronized bidirectionally with the owned ViewerModel."""

    active_layer_changed = Signal(object)

    def __init__(self, viewer, parent: QWidget | None = None):
        super().__init__(parent)
        self.viewer = viewer
        self._syncing = False
        self._disposed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        self.tree = QTreeView()
        self.tree.setObjectName("layerTree")
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(
            QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.model = LayerTreeModel(viewer, self)
        self.tree.setModel(self.model)
        self.tree.expandAll()
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, self.tree.header().ResizeMode.ResizeToContents)
        self.tree.selectionModel().currentChanged.connect(self._on_current_changed)
        viewer.layers.selection.events.active.connect(self._on_active_changed)
        for name in ("inserted", "removed", "moved", "reordered"):
            emitter = getattr(viewer.layers.events, name, None)
            if emitter is not None:
                emitter.connect(self._after_structure_changed)
        layout.addWidget(self.tree)
        self._on_active_changed()

    def _after_structure_changed(self, event=None) -> None:
        self.tree.expandAll()
        self._on_active_changed()

    def _on_current_changed(self, current, previous=None) -> None:
        if self._syncing:
            return
        layer = current.data(LAYER_ROLE)
        if layer is not None and layer in self.viewer.layers:
            self.viewer.layers.selection.active = layer

    def _on_active_changed(self, event=None) -> None:
        if self._disposed:
            return
        layer = self.viewer.layers.selection.active
        item = self.model.item_for_layer(layer)
        if item is not None:
            self._syncing = True
            self.tree.setCurrentIndex(item.index())
            self._syncing = False
        self.active_layer_changed.emit(layer)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.viewer.layers.selection.events.active.disconnect(self._on_active_changed)
        for name in ("inserted", "removed", "moved", "reordered"):
            emitter = getattr(self.viewer.layers.events, name, None)
            if emitter is not None:
                emitter.disconnect(self._after_structure_changed)
        self.model.dispose()
