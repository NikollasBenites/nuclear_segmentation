"""Independent napari 0.10 viewer embedding for the NuCytoSeg Qt shell.

This module owns the narrow boundary between an application-managed Qt window
and napari's public ``ViewerModel`` / ``QtViewer`` API. Scientific analysis is
deliberately absent from this proof of concept.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

import numpy as np
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


DISPLAY_NAME = "NuCytoSeg"
DISPLAY_DESCRIPTION = (
    "3D Nuclear and Cytoplasmic Segmentation, Editing, and Quantitative Analysis"
)
DEMO_SPACING_ZYX_UM = (2.0, 0.45, 0.45)


@dataclass(frozen=True)
class DemoVolumes:
    """Small deterministic volumes used only by the embedding smoke test."""

    nuclear: np.ndarray
    cytoplasmic: np.ndarray
    labels: np.ndarray
    multiscale: tuple[np.ndarray, np.ndarray]


def make_demo_volumes(shape: tuple[int, int, int] = (12, 96, 112)) -> DemoVolumes:
    """Return deterministic anisotropic test images and editable labels."""
    if len(shape) != 3 or any(int(size) < 8 for size in shape):
        raise ValueError("Demo shape must contain three dimensions of at least 8 voxels.")
    z, y, x = np.indices(shape, dtype=np.float32)
    nuclear = np.zeros(shape, dtype=np.float32)
    cytoplasmic = np.zeros(shape, dtype=np.float32)
    labels = np.zeros(shape, dtype=np.uint16)
    centers = (
        (shape[0] * 0.30, shape[1] * 0.34, shape[2] * 0.34),
        (shape[0] * 0.55, shape[1] * 0.63, shape[2] * 0.58),
        (shape[0] * 0.73, shape[1] * 0.38, shape[2] * 0.73),
    )
    for label_id, (cz, cy, cx) in enumerate(centers, start=1):
        distance = ((z - cz) / 1.6) ** 2 + ((y - cy) / 8.0) ** 2 + ((x - cx) / 8.0) ** 2
        nuclear += np.exp(-distance * 1.8) * (0.75 + label_id * 0.08)
        cytoplasmic += np.exp(-distance * 0.45) * 0.75
        labels[distance <= 1.0] = label_id
    nuclear = np.clip(nuclear + (x / max(shape[2] - 1, 1)) * 0.08, 0, 1).astype(np.float32)
    cytoplasmic = np.clip(cytoplasmic - nuclear * 0.25, 0, 1).astype(np.float32)
    coarse = nuclear[:, ::2, ::2].copy()
    return DemoVolumes(nuclear, cytoplasmic, labels, (nuclear, coarse))


def _shutdown_viewer_model(viewer: Any) -> None:
    """Stop napari 0.10 slicing tasks until ViewerModel gains public cleanup.

    ``napari.Viewer.close`` performs this operation, but ``ViewerModel`` has no
    public close method in napari 0.10.0. Keep the compatibility access here so
    it cannot spread into the application shell.
    """
    slicer = getattr(viewer, "_layer_slicer", None)
    shutdown = getattr(slicer, "shutdown", None)
    if shutdown is not None:
        shutdown()


class OwnedLayerList(QListWidget):
    """Small layer list that never invokes napari's injected context actions.

    napari 0.10's native ``QtLayerList`` right-click menu resolves its
    ``LayerList`` argument through napari's native-main-window providers. An
    independently embedded ``ViewerModel`` is intentionally absent from those
    global providers. This widget binds visibility and active selection
    directly to the owned model and disables a context menu until NuCytoSeg
    supplies its own explicit layer operations.
    """

    def __init__(self, viewer, parent: QWidget | None = None):
        super().__init__(parent)
        self.viewer = viewer
        self._syncing = False
        self._disposed = False
        self._layer_callbacks: dict[Any, tuple[Callable, Callable]] = {}
        self.setObjectName("ownedLayerList")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setSelectionMode(self.SelectionMode.SingleSelection)
        self.itemChanged.connect(self._on_item_changed)
        self.currentItemChanged.connect(self._on_current_item_changed)
        for event_name in ("inserted", "removed", "moved", "reordered", "renamed"):
            emitter = getattr(self.viewer.layers.events, event_name, None)
            if emitter is not None:
                emitter.connect(self._rebuild_from_event)
        self.viewer.layers.selection.events.active.connect(self._on_active_layer_changed)
        self._rebuild()

    def _rebuild_from_event(self, event=None) -> None:
        self._rebuild()

    def _disconnect_layer_callbacks(self) -> None:
        for layer, (name_callback, visible_callback) in self._layer_callbacks.items():
            layer.events.name.disconnect(name_callback)
            layer.events.visible.disconnect(visible_callback)
        self._layer_callbacks.clear()

    def _rebuild(self) -> None:
        if self._disposed:
            return
        self._syncing = True
        self._disconnect_layer_callbacks()
        self.clear()
        for layer in reversed(self.viewer.layers):
            item = QListWidgetItem(layer.name)
            item.setData(Qt.ItemDataRole.UserRole, layer)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEnabled
            )
            item.setCheckState(
                Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked
            )
            self.addItem(item)
            name_callback = lambda event, current=layer: self._sync_layer(current)
            visible_callback = lambda event, current=layer: self._sync_layer(current)
            layer.events.name.connect(name_callback)
            layer.events.visible.connect(visible_callback)
            self._layer_callbacks[layer] = (name_callback, visible_callback)
            if layer is self.viewer.layers.selection.active:
                self.setCurrentItem(item)
        self._syncing = False

    def _item_for_layer(self, layer):
        for row in range(self.count()):
            item = self.item(row)
            if item.data(Qt.ItemDataRole.UserRole) is layer:
                return item
        return None

    def _sync_layer(self, layer) -> None:
        if self._disposed or self._syncing:
            return
        item = self._item_for_layer(layer)
        if item is None:
            return
        self._syncing = True
        item.setText(layer.name)
        item.setCheckState(
            Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked
        )
        self._syncing = False

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._syncing:
            return
        layer = item.data(Qt.ItemDataRole.UserRole)
        if layer is not None:
            layer.visible = item.checkState() == Qt.CheckState.Checked

    def _on_current_item_changed(self, current, previous=None) -> None:
        if self._syncing or current is None:
            return
        layer = current.data(Qt.ItemDataRole.UserRole)
        if layer is not None and layer in self.viewer.layers:
            self.viewer.layers.selection.active = layer

    def _on_active_layer_changed(self, event=None) -> None:
        if self._syncing or self._disposed:
            return
        item = self._item_for_layer(self.viewer.layers.selection.active)
        if item is not None:
            self._syncing = True
            self.setCurrentItem(item)
            self._syncing = False

    def contextMenuEvent(self, event) -> None:
        """Accept right-clicks without opening napari's unsafe native menu."""
        event.accept()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._disconnect_layer_callbacks()
        for event_name in ("inserted", "removed", "moved", "reordered", "renamed"):
            emitter = getattr(self.viewer.layers.events, event_name, None)
            if emitter is not None:
                emitter.disconnect(self._rebuild_from_event)
        self.viewer.layers.selection.events.active.disconnect(
            self._on_active_layer_changed
        )
        self.clear()


class EmbeddedNapariWidget(QWidget):
    """Application-owned widget containing a public napari ``QtViewer``."""

    status_changed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        load_demo: bool = True,
        qt_viewer_factory=None,
        show_controls: bool = True,
    ):
        super().__init__(parent)
        from napari import __version__ as napari_version
        from napari.components import ViewerModel
        from napari.qt import QtViewer

        if tuple(int(part) for part in napari_version.split(".")[:2]) < (0, 10):
            raise RuntimeError(
                f"{DISPLAY_NAME} requires napari 0.10.0 or later; found {napari_version}."
            )
        self._closed = False
        self._demo_labels = None
        self._label_control_events_connected = False
        self.viewer = ViewerModel(title=DISPLAY_NAME)
        factory = qt_viewer_factory or QtViewer
        self.qt_viewer = factory(self.viewer, show_welcome_screen=False)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.qt_viewer, 1)
        self.viewer_tools = None
        self.layer_list = None
        if show_controls:
            self.viewer_tools = self._build_viewer_tools()
            root.addWidget(self.viewer_tools)

        if load_demo:
            self.load_demo_layers()

    def _build_viewer_tools(self) -> QWidget:
        tools = QFrame()
        tools.setObjectName("embeddingViewerTools")
        tools.setMinimumWidth(260)
        tools.setMaximumWidth(380)
        layout = QVBoxLayout(tools)
        layout.setContentsMargins(10, 10, 10, 10)
        title = QLabel("Viewer controls")
        title.setObjectName("embeddingToolsTitle")
        layout.addWidget(title)

        display = QHBoxLayout()
        self.display_group = QButtonGroup(self)
        self.display_group.setExclusive(True)
        self.display_buttons = {}
        for text, dimensions in (("2D", 2), ("3D", 3)):
            button = self._tool_button(text, f"Show the volume in {text}")
            button.setCheckable(True)
            button.setChecked(dimensions == 2)
            self.display_group.addButton(button)
            button.clicked.connect(
                lambda checked=False, value=dimensions: self._run_ui_action(
                    lambda: self.set_ndisplay(value)
                )
            )
            self.display_buttons[dimensions] = button
            display.addWidget(button)
        reset = self._tool_button("Reset", "Reset the camera view")
        reset.clicked.connect(
            lambda checked=False: self._run_ui_action(self.viewer.reset_view)
        )
        display.addWidget(reset)
        layout.addLayout(display)

        editing = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons = {}
        for text, mode, tooltip in (
            ("Pan", "pan_zoom", "Pan and zoom without editing labels"),
            ("Pick", "pick", "Pick the label under the cursor"),
            ("Paint", "paint", "Paint the selected label"),
            ("Erase", "erase", "Erase label voxels"),
        ):
            button = self._tool_button(text, tooltip)
            button.setCheckable(True)
            button.setChecked(mode == "pan_zoom")
            self.mode_group.addButton(button)
            button.clicked.connect(
                lambda checked=False, value=mode: self._run_ui_action(
                    lambda: self.set_demo_label_mode(value)
                )
            )
            self.mode_buttons[mode] = button
            editing.addWidget(button)
        layout.addLayout(editing)

        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Selected label"))
        self.selected_label_control = QSpinBox()
        self.selected_label_control.setRange(0, 2**31 - 1)
        self.selected_label_control.setToolTip(
            "Label value used by Pick, Paint, and Erase modes"
        )
        self.selected_label_control.valueChanged.connect(
            lambda value: self._run_ui_action(
                lambda: self.set_selected_label(value)
            )
        )
        label_row.addWidget(self.selected_label_control, 1)
        layout.addLayout(label_row)

        note = QLabel(
            "Select a layer below. Paint and Erase operate only on the disposable "
            "demonstration labels. Use the mouse wheel or dimension slider to move in Z."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.layer_list = OwnedLayerList(self.viewer)
        layout.addWidget(self.layer_list, 1)
        return tools

    @staticmethod
    def _tool_button(text: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAutoRaise(False)
        return button

    def _run_ui_action(self, callback: Callable[[], Any]) -> bool:
        """Keep expected control errors inside the Qt callback boundary."""
        try:
            callback()
        except Exception as exc:
            self.status_changed.emit(f"Viewer action failed: {exc}")
            return False
        return True

    def load_demo_layers(self) -> None:
        """Populate the viewer with synthetic calibrated smoke-test layers."""
        if self.viewer.layers:
            raise RuntimeError("Demo layers can only be loaded into an empty viewer.")
        data = make_demo_volumes()
        spacing = DEMO_SPACING_ZYX_UM
        units = ("um",) * 3
        self.viewer.add_image(
            data.nuclear,
            name="Nuclear channel — synthetic",
            colormap="cyan",
            blending="additive",
            scale=spacing,
            units=units,
            metadata={"nucytoseg_demo": True},
        )
        self.viewer.add_image(
            data.cytoplasmic,
            name="Cytoplasmic channel — synthetic",
            colormap="magenta",
            blending="additive",
            scale=spacing,
            units=units,
            opacity=0.65,
            metadata={"nucytoseg_demo": True},
        )
        labels = self.viewer.add_labels(
            data.labels.copy(),
            name="Editable demonstration labels",
            scale=spacing,
            units=units,
            opacity=0.55,
            metadata={"nucytoseg_demo": True},
        )
        self.viewer.add_image(
            list(data.multiscale),
            name="Multiscale demonstration",
            colormap="gray",
            scale=spacing,
            units=units,
            visible=False,
            multiscale=True,
            metadata={"nucytoseg_demo": True},
        )
        self.viewer.dims.axis_labels = ("Z", "Y", "X")
        self.viewer.dims.set_current_step(0, data.nuclear.shape[0] // 2)
        self.viewer.layers.selection.active = labels
        labels.selected_label = int(data.labels.max()) + 1
        labels.brush_size = 5
        labels.mode = "pan_zoom"
        labels.mouse_drag_callbacks.append(self._on_demo_label_mouse_drag)
        self._demo_labels = labels
        if self.viewer_tools is not None:
            labels.events.selected_label.connect(self._on_selected_label_changed)
            labels.events.mode.connect(self._on_demo_label_mode_changed)
            self._label_control_events_connected = True
            self._on_selected_label_changed()
            self._on_demo_label_mode_changed()
        self.viewer.canvas.overlays.scale_bar.visible = True
        self.status_changed.emit("Synthetic calibrated layers loaded")

    def _on_demo_label_mouse_drag(self, layer, event):
        """Expose real canvas callback dispatch through the smoke-test status bar."""
        position = tuple(float(value) for value in layer.world_to_data(event.position))
        self.status_changed.emit(
            "Labels mouse callback: ZYX "
            + ", ".join(f"{value:.1f}" for value in position[-3:])
        )
        yield

    def demo_labels(self):
        if self._demo_labels is None or self._demo_labels not in self.viewer.layers:
            raise RuntimeError("The disposable demonstration Labels layer is unavailable.")
        return self._demo_labels

    def set_ndisplay(self, dimensions: int) -> None:
        if dimensions not in (2, 3):
            raise ValueError("Display dimensions must be 2 or 3.")
        self.viewer.dims.ndisplay = dimensions
        self.display_buttons[dimensions].setChecked(True)
        self.status_changed.emit(f"{dimensions}D display active")

    def set_demo_label_mode(self, mode: str) -> None:
        if mode not in {"pan_zoom", "pick", "paint", "erase"}:
            raise ValueError("Unsupported labels interaction mode.")
        labels = self.demo_labels()
        self.viewer.layers.selection.active = labels
        labels.mode = mode
        self.mode_buttons[mode].setChecked(True)
        self.status_changed.emit(f"Demonstration labels mode: {mode}")

    def set_selected_label(self, value: int) -> None:
        labels = self.demo_labels()
        labels.selected_label = int(value)

    def _on_selected_label_changed(self, event=None) -> None:
        if self._demo_labels is None:
            return
        self.selected_label_control.blockSignals(True)
        self.selected_label_control.setValue(int(self._demo_labels.selected_label))
        self.selected_label_control.blockSignals(False)

    def _on_demo_label_mode_changed(self, event=None) -> None:
        if self._demo_labels is None:
            return
        mode = str(self._demo_labels.mode)
        button = self.mode_buttons.get(mode)
        if button is not None:
            button.setChecked(True)

    def pick_demo_label_at_data_position(self, position) -> int:
        """Headless equivalent of a Labels Pick click for regression tests."""
        labels = self.demo_labels()
        value = labels.get_value(position)
        selected = 0 if value is None else int(value)
        labels.selected_label = selected
        return selected

    def shutdown(self) -> None:
        """Idempotently release the embedded viewer and its slicing worker."""
        if self._closed:
            return
        self._closed = True
        labels = self._demo_labels
        if labels is not None:
            if self._on_demo_label_mouse_drag in labels.mouse_drag_callbacks:
                labels.mouse_drag_callbacks.remove(self._on_demo_label_mouse_drag)
            if self._label_control_events_connected:
                labels.events.selected_label.disconnect(self._on_selected_label_changed)
                labels.events.mode.disconnect(self._on_demo_label_mode_changed)
                self._label_control_events_connected = False
        if self.layer_list is not None:
            self.layer_list.dispose()
        _shutdown_viewer_model(self.viewer)
        self.viewer.layers.clear()
        self.qt_viewer.close()
        self._demo_labels = None

    def closeEvent(self, event) -> None:
        self.shutdown()
        event.accept()
