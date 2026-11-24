"""Application-owned projection, Shapes selection, and crop preview controller."""
from __future__ import annotations

import numpy as np
from qtpy.QtCore import QObject, QThread, Signal, Slot
from qtpy.QtWidgets import QMessageBox

from .projection_crop import (
    generate_xy_projection,
    masked_crop_czyx,
    polygon_roi_mask,
    selection_bounding_box,
)


class ProjectionWorker(QObject):
    succeeded = Signal(object, int, str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, volume, channel_index: int, method: str):
        super().__init__()
        self.volume = volume
        self.channel_index = int(channel_index)
        self.method = str(method)

    @Slot()
    def run(self):
        try:
            projection = generate_xy_projection(self.volume, self.method)
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(projection, self.channel_index, self.method)
        finally:
            self.finished.emit()


class CropWorkspaceController(QObject):
    working_dataset_changed = Signal(object)
    status_changed = Signal(str)

    def __init__(self, viewer, project_state, panel, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.project_state = project_state
        self.panel = panel
        self.projection_layer = None
        self.selection_layer = None
        self.preview_layers = []
        self._source_visibility = {}
        self._preview_bbox_original = None
        self._preview_vertices_original = None
        self._preview_roi_mask = None
        self._preview_selection_type = None
        self._thread = None
        self._worker = None
        self._disposed = False
        panel.generate_requested.connect(self.generate_projection)
        panel.clear_projection_requested.connect(self.clear_projection)
        panel.draw_requested.connect(self.start_drawing)
        panel.edit_requested.connect(self.edit_selection)
        panel.clear_requested.connect(self.clear_selection)
        panel.preview_requested.connect(self.preview_crop)
        panel.confirm_requested.connect(self.confirm_crop)
        panel.cancel_requested.connect(self.cancel_preview)
        panel.reset_requested.connect(self.reset_crop)
        self.viewer.layers.events.removed.connect(self._layer_removed)

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def _channel(self, index):
        return next(
            (channel for channel in self.project_state.channels if channel.index == index),
            None,
        )

    def generate_projection(self, channel_index: int, method: str) -> bool:
        if self._thread is not None:
            self._status("A projection is already being generated.")
            return False
        dataset = self.project_state.working_dataset
        channel = self._channel(channel_index)
        if dataset is None or channel is None:
            self._status("Load a project image before generating a projection.")
            return False
        self.cancel_preview()
        self._remove_layer(self.projection_layer)
        self._remove_layer(self.selection_layer)
        self.projection_layer = None
        self.selection_layer = None
        self._status(f"Generating {method} projection for {channel.name}…")
        worker = ProjectionWorker(dataset.data_czyx[channel.index], channel.index, method)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._show_projection)
        worker.failed.connect(self._status)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._projection_finished)
        thread.finished.connect(thread.deleteLater)
        self._worker = worker
        self._thread = thread
        thread.start()
        return True

    def _show_projection(self, data, channel_index: int, method: str) -> None:
        channel = self._channel(channel_index)
        spacing = self.project_state.voxel_spacing_zyx_um
        offset_y, offset_x = self.project_state.working_offset_yx
        self.projection_layer = self.viewer.add_image(
            data,
            name=f"XY {method} projection · {channel.name}",
            colormap=channel.colormap,
            scale=spacing[1:],
            translate=(offset_y * spacing[1], offset_x * spacing[2]),
            units=("µm", "µm"),
            metadata={"nucytoseg_projection": True, "channel_index": channel_index},
        )
        self.viewer.dims.ndisplay = 2
        self.viewer.layers.selection.active = self.projection_layer
        self.panel.clear_projection_button.setEnabled(True)
        self._status(
            f"Projection ready: {data.shape[0]} × {data.shape[1]} pixels. Draw a crop selection."
        )

    def _projection_finished(self) -> None:
        self._thread = None
        self._worker = None

    def clear_projection(self, *, confirm: bool = True) -> bool:
        """Remove only temporary projection-dependent state, never a confirmed crop."""
        if self.busy:
            self._status("Wait for projection generation to finish before clearing it.")
            return False
        has_selection = (
            self.selection_layer is not None
            and self.selection_layer in self.viewer.layers
            and len(self.selection_layer.data) > 0
        )
        has_preview = bool(self.preview_layers)
        if confirm and (has_selection or has_preview):
            answer = QMessageBox.question(
                self.panel,
                "Clear Projection?",
                "Clearing the projection will discard the unfinished crop selection "
                "and crop preview. Confirmed crops are not affected.",
                QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                return False
        self.cancel_preview()
        self._remove_layer(self.selection_layer)
        self._remove_layer(self.projection_layer)
        self.selection_layer = None
        self.projection_layer = None
        self.panel.clear_projection_button.setEnabled(False)
        source = next(
            (
                layer for layer in self.viewer.layers
                if (getattr(layer, "metadata", {}) or {}).get("nucytoseg_imported")
            ),
            None,
        )
        if source is not None:
            self.viewer.layers.selection.active = source
        self._status("XY projection cleared. The working image and confirmed crop are unchanged.")
        return True

    def _layer_removed(self, event=None) -> None:
        """Drop stale references when an owned temporary layer is removed externally."""
        if self.projection_layer is not None and self.projection_layer not in self.viewer.layers:
            self.projection_layer = None
            self.panel.clear_projection_button.setEnabled(False)
        if self.selection_layer is not None and self.selection_layer not in self.viewer.layers:
            self.selection_layer = None
        self.preview_layers[:] = [
            layer for layer in self.preview_layers if layer in self.viewer.layers
        ]

    def _ensure_selection_layer(self):
        if self.projection_layer is None or self.projection_layer not in self.viewer.layers:
            raise ValueError("Generate an XY projection before drawing a crop.")
        # A 2D Shapes layer must be created only after the viewer has left 3D
        # display. Creating/editing it during the transition can make napari
        # combine two-coordinate vertices with a three-axis displayed transform.
        self.viewer.dims.ndisplay = 2
        if self.selection_layer is None or self.selection_layer not in self.viewer.layers:
            self.selection_layer = self.viewer.add_shapes(
                name="MNTB XY crop selection",
                ndim=2,
                shape_type="polygon",
                edge_color="#63d9d0",
                face_color="#63d9d033",
                edge_width=2,
                scale=tuple(self.projection_layer.scale),
                translate=tuple(self.projection_layer.translate),
                metadata={"nucytoseg_crop_selection": True},
            )
        return self.selection_layer

    def start_drawing(self, shape_type: str) -> bool:
        try:
            layer = self._ensure_selection_layer()
        except ValueError as error:
            self._status(str(error))
            return False
        self.cancel_preview()
        layer.mode = "add_polygon" if shape_type == "polygon" else "add_rectangle"
        self.viewer.layers.selection.active = layer
        self._status(f"Draw one {shape_type} on the XY projection.")
        return True

    def edit_selection(self) -> bool:
        try:
            layer = self._ensure_selection_layer()
        except ValueError as error:
            self._status(str(error))
            return False
        layer.mode = "select"
        self.viewer.layers.selection.active = layer
        self._status("Crop selection edit mode active.")
        return True

    def clear_selection(self) -> None:
        self.cancel_preview()
        if self.selection_layer is not None and self.selection_layer in self.viewer.layers:
            self.selection_layer.data = []
        self._status("Crop selection cleared.")

    def _selection(self):
        if (
            self.selection_layer is None
            or self.selection_layer not in self.viewer.layers
            or len(self.selection_layer.data) == 0
        ):
            raise ValueError("Draw a rectangle or polygon before previewing the crop.")
        vertices = np.asarray(self.selection_layer.data[-1], dtype=float)
        if vertices.ndim != 2 or vertices.shape[1] != 2:
            raise ValueError("XY crop vertices must contain exactly Y and X coordinates.")
        dataset = self.project_state.working_dataset
        raw_type = self.selection_layer.shape_type[-1]
        selection_type = str(getattr(raw_type, "value", raw_type)).lower()
        if selection_type == "polygon":
            local_bbox, roi_mask = polygon_roi_mask(
                vertices, dataset.data_czyx.shape[-2:]
            )
        elif selection_type == "rectangle":
            local_bbox = selection_bounding_box(
                vertices, dataset.data_czyx.shape[-2:]
            )
            y0, y1, x0, x1 = local_bbox
            roi_mask = np.ones((y1 - y0, x1 - x0), dtype=bool)
        else:
            raise ValueError(f"Unsupported crop selection type: {selection_type}")
        offset_y, offset_x = self.project_state.working_offset_yx
        y0, y1, x0, x1 = local_bbox
        original_bbox = (y0 + offset_y, y1 + offset_y, x0 + offset_x, x1 + offset_x)
        original_vertices = vertices + np.asarray((offset_y, offset_x))
        return original_bbox, original_vertices, roi_mask, selection_type

    def preview_crop(self) -> bool:
        try:
            bbox, vertices, roi_mask, selection_type = self._selection()
        except ValueError as error:
            self._status(str(error))
            return False
        self.cancel_preview()
        original = self.project_state.original_dataset
        y0, y1, x0, x1 = bbox
        spacing = self.project_state.voxel_spacing_zyx_um
        self._source_visibility = {
            layer: bool(layer.visible)
            for layer in self.viewer.layers
            if (getattr(layer, "metadata", {}) or {}).get("nucytoseg_imported")
        }
        for layer in self._source_visibility:
            layer.visible = False
        for channel in self.project_state.channels:
            if selection_type == "polygon":
                preview_data = masked_crop_czyx(
                    original.data_czyx[channel.index : channel.index + 1],
                    bbox,
                    roi_mask,
                )[0]
            else:
                preview_data = original.data_czyx[channel.index, :, y0:y1, x0:x1]
            layer = self.viewer.add_image(
                preview_data,
                name=f"Crop preview · {channel.name}",
                colormap=channel.colormap,
                blending="additive",
                visible=channel.visible,
                scale=spacing,
                translate=(0.0, y0 * spacing[1], x0 * spacing[2]),
                units=("µm", "µm", "µm"),
                metadata={"nucytoseg_crop_preview": True, "channel_index": channel.index},
            )
            self.preview_layers.append(layer)
        self._preview_bbox_original = bbox
        self._preview_vertices_original = vertices
        self._preview_roi_mask = np.array(roi_mask, copy=True)
        self._preview_selection_type = selection_type
        if self.preview_layers:
            self.viewer.layers.selection.active = self.preview_layers[0]
        self._status(f"Crop preview: CZYX {original.data_czyx[:, :, y0:y1, x0:x1].shape}")
        return True

    def cancel_preview(self) -> None:
        for layer in list(self.preview_layers):
            self._remove_layer(layer)
        self.preview_layers.clear()
        for layer, visible in self._source_visibility.items():
            if layer in self.viewer.layers:
                layer.visible = visible
        self._source_visibility.clear()
        self._preview_bbox_original = None
        self._preview_vertices_original = None
        self._preview_roi_mask = None
        self._preview_selection_type = None

    def confirm_crop(self) -> bool:
        if self._preview_bbox_original is None:
            self._status("Preview a valid crop before applying it.")
            return False
        bbox = self._preview_bbox_original
        vertices = np.array(self._preview_vertices_original, copy=True)
        roi_mask = np.array(self._preview_roi_mask, copy=True)
        selection_type = self._preview_selection_type
        self.cancel_preview()
        dataset = self.project_state.apply_crop(
            bbox,
            vertices,
            roi_mask_yx=roi_mask if selection_type == "polygon" else None,
            selection_type=selection_type,
        )
        self.working_dataset_changed.emit(dataset)
        self._remove_layer(self.projection_layer)
        self._remove_layer(self.selection_layer)
        self.projection_layer = None
        self.selection_layer = None
        self.panel.clear_projection_button.setEnabled(False)
        self._status(f"Crop applied: CZYX {dataset.standardized_shape}")
        return True

    def reset_crop(self) -> bool:
        if self.project_state.original_dataset is None:
            self._status("No imported dataset is available to restore.")
            return False
        self.cancel_preview()
        if self.project_state.crop_provenance is not None:
            dataset = self.project_state.reset_crop()
            self.working_dataset_changed.emit(dataset)
        self._remove_layer(self.projection_layer)
        self._remove_layer(self.selection_layer)
        self.projection_layer = None
        self.selection_layer = None
        self.panel.clear_projection_button.setEnabled(False)
        self._status("Original uncropped dataset restored.")
        return True

    def _remove_layer(self, layer) -> None:
        if layer is not None and layer in self.viewer.layers:
            self.viewer.layers.remove(layer)

    def _status(self, message: str) -> None:
        self.panel.set_status(message)
        self.status_changed.emit(str(message))

    def prepare_display_change(self, dimensions: int) -> None:
        """End 2D Shapes interaction before napari changes displayed dimensions."""
        if int(dimensions) != 3:
            return
        layer = self.selection_layer
        if layer is not None and layer in self.viewer.layers:
            layer.mode = "pan_zoom"
            if self.viewer.layers.selection.active is layer:
                replacement = next(
                    (
                        item for item in self.viewer.layers
                        if type(item).__name__.lower() == "image" and item.ndim >= 3
                    ),
                    None,
                )
                self.viewer.layers.selection.active = replacement

    def clear_workspace_for_new_project(self) -> None:
        self.cancel_preview()
        self._remove_layer(self.projection_layer)
        self._remove_layer(self.selection_layer)
        self.projection_layer = None
        self.selection_layer = None
        self.panel.clear_projection_button.setEnabled(False)
        self._preview_bbox_original = None
        self._preview_vertices_original = None
        self._preview_roi_mask = None
        self._preview_selection_type = None

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
        self.clear_workspace_for_new_project()
        self.viewer.layers.events.removed.disconnect(self._layer_removed)
