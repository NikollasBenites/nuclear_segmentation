"""Independent napari 0.10 viewer embedding for the NuCytoSeg Qt shell.

This module owns the narrow boundary between an application-managed Qt window
and napari's public ``ViewerModel`` / ``QtViewer`` API. Scientific analysis is
deliberately absent from this proof of concept.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
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


class EmbeddedNapariWidget(QWidget):
    """Application-owned widget containing a public napari ``QtViewer``."""

    status_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, load_demo: bool = True):
        super().__init__(parent)
        from napari import __version__ as napari_version
        from napari.components import ViewerModel
        from napari.qt import QtViewer

        if tuple(int(part) for part in napari_version.split(".")[:2]) < (0, 10):
            raise RuntimeError(
                f"{DISPLAY_NAME} requires napari 0.10.0 or later; found {napari_version}."
            )
        self._closed = False
        self.viewer = ViewerModel(title=DISPLAY_NAME)
        self.qt_viewer = QtViewer(self.viewer, show_welcome_screen=False)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.qt_viewer, 1)
        root.addWidget(self._build_viewer_tools())

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
        for text, dimensions in (("2D", 2), ("3D", 3)):
            button = QPushButton(text)
            button.clicked.connect(
                lambda checked=False, value=dimensions: self.set_ndisplay(value)
            )
            display.addWidget(button)
        reset = QPushButton("Reset view")
        reset.clicked.connect(lambda checked=False: self.viewer.reset_view())
        display.addWidget(reset)
        layout.addLayout(display)

        editing = QHBoxLayout()
        for text, mode in (("Pan", "pan_zoom"), ("Paint", "paint"), ("Erase", "erase")):
            button = QPushButton(text)
            button.clicked.connect(
                lambda checked=False, value=mode: self.set_demo_label_mode(value)
            )
            editing.addWidget(button)
        layout.addLayout(editing)

        note = QLabel(
            "Select a layer below. Paint and Erase operate only on the disposable "
            "demonstration labels. Use the mouse wheel or dimension slider to move in Z."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addWidget(self.qt_viewer.layerButtons)
        layout.addWidget(self.qt_viewer.layers, 1)
        layout.addWidget(self.qt_viewer.controls)
        return tools

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
        )
        self.viewer.add_image(
            data.cytoplasmic,
            name="Cytoplasmic channel — synthetic",
            colormap="magenta",
            blending="additive",
            scale=spacing,
            units=units,
            opacity=0.65,
        )
        labels = self.viewer.add_labels(
            data.labels.copy(),
            name="Editable demonstration labels",
            scale=spacing,
            units=units,
            opacity=0.55,
        )
        self.viewer.add_image(
            list(data.multiscale),
            name="Multiscale demonstration",
            colormap="gray",
            scale=spacing,
            units=units,
            visible=False,
            multiscale=True,
        )
        self.viewer.dims.axis_labels = ("Z", "Y", "X")
        self.viewer.dims.set_current_step(0, data.nuclear.shape[0] // 2)
        self.viewer.layers.selection.active = labels
        labels.selected_label = int(data.labels.max()) + 1
        labels.brush_size = 5
        labels.mode = "pan_zoom"
        labels.mouse_drag_callbacks.append(self._on_demo_label_mouse_drag)
        self.viewer.scale_bar.visible = True
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
        return self.viewer.layers["Editable demonstration labels"]

    def set_ndisplay(self, dimensions: int) -> None:
        if dimensions not in (2, 3):
            raise ValueError("Display dimensions must be 2 or 3.")
        self.viewer.dims.ndisplay = dimensions
        self.status_changed.emit(f"{dimensions}D display active")

    def set_demo_label_mode(self, mode: str) -> None:
        if mode not in {"pan_zoom", "paint", "erase"}:
            raise ValueError("Unsupported labels interaction mode.")
        labels = self.demo_labels()
        self.viewer.layers.selection.active = labels
        labels.mode = mode
        self.status_changed.emit(f"Demonstration labels mode: {mode}")

    def shutdown(self) -> None:
        """Idempotently release the embedded viewer and its slicing worker."""
        if self._closed:
            return
        self._closed = True
        if self.viewer.layers:
            labels = next(
                (
                    layer
                    for layer in self.viewer.layers
                    if layer.name == "Editable demonstration labels"
                ),
                None,
            )
            if labels is not None and self._on_demo_label_mouse_drag in labels.mouse_drag_callbacks:
                labels.mouse_drag_callbacks.remove(self._on_demo_label_mouse_drag)
        _shutdown_viewer_model(self.viewer)
        self.viewer.layers.clear()
        self.qt_viewer.close()

    def closeEvent(self, event) -> None:
        self.shutdown()
        event.accept()
