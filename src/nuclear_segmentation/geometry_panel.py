"""Proofreading controls for the Mask Editing page."""
from napari.layers import LayerLock
from qtpy.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit,
                            QListWidget, QPushButton, QVBoxLayout)

from .geometry_edit import GeometryEdit, undo_stack
from .runtime import validate_roi_geometry


class GeometryPanel(QGroupBox):
    def __init__(self, window, review):
        super().__init__("Geometry editing")
        self.window = window
        self.state = window.project_state
        self.viewer = window.embedding.viewer
        self.edit = self.layer = None
        layout = QVBoxLayout(self)
        self.buttons = {}
        self.selected_id = self.brush_id = None
        fields = QFormLayout()
        self.selected_object = QLabel("None")
        self.brush_label = QLineEdit()
        self.brush_label.setPlaceholderText("Select an object or enter a positive ID")
        self.brush_label.editingFinished.connect(self._brush_entered)
        fields.addRow("Selected Object ID", self.selected_object)
        fields.addRow("Brush Label ID", self.brush_label)
        layout.addLayout(fields)
        self.merge_list = QListWidget()
        self.merge_list.setMaximumHeight(85)
        self.merge_list.setAccessibleName("Objects selected for merge")
        self.merge_target = QComboBox()
        self.merge_target.setAccessibleName("Retained target ID")
        self.merge_target.setPlaceholderText("Choose the retained ID")
        for name, caption in (
            ("start", "Start Editing"), ("commit", "Commit Changes"), ("discard", "Discard Changes"),
            ("undo", "Undo Last Commit"), ("remove", "Remove Selected Object"),
            ("split", "Split Selected Object"), ("return_preview", "Return to Editing Preview"),
            ("add_merge", "Add Selected Object to Merge"), ("remove_merge", "Remove ID from List"),
            ("clear_merge", "Clear Merge Selection"), ("merge", "Preview Merge"),
            ("show_edited", "Show Committed Edits"),
        ):
            button = QPushButton(caption)
            if name == "remove_merge":
                layout.addWidget(self.merge_list)
            if name == "merge":
                layout.addWidget(QLabel("Retained target ID"))
                layout.addWidget(self.merge_target)
            layout.addWidget(button)
            button.clicked.connect(lambda checked=False, action=name: self.run(action))
            self.buttons[name] = button
        self.merge_target.currentIndexChanged.connect(self.refresh)
        self.merge_list.currentRowChanged.connect(self.refresh)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        review.pages["mask_editing"].layout().insertWidget(1, self)
        self.state.segmentation_result_changed.connect(self.refresh)
        self.viewer.dims.events.ndisplay.connect(self._dimensions)
        self.refresh()

    @property
    def busy(self):
        return self.edit is not None

    def run(self, action):
        try:
            getattr(self, action)()
        except ValueError as error:
            self.status.setText(str(error))

    def refresh(self, *args):
        result = self.state.segmentation_result or {}
        current = self.state.segmentation_is_current
        available = self.busy and self._owns_preview()
        choosing = available and self.edit.operation == "paint/erase"
        selected = (choosing and self.selected_id is not None and self.layer.visible
                    and self.viewer.layers.selection.active is self.layer)
        for name in ("remove", "split", "add_merge"):
            self.buttons[name].setEnabled(bool(selected))
        self.buttons["return_preview"].setEnabled(bool(available))
        self.buttons["commit"].setEnabled(bool(available))
        self.buttons["discard"].setEnabled(self.busy)
        self.buttons["remove_merge"].setEnabled(bool(choosing and self.merge_list.currentRow() >= 0))
        self.buttons["clear_merge"].setEnabled(bool(choosing and self.merge_list.count()))
        self.merge_target.setEnabled(bool(choosing and self.merge_list.count() >= 2))
        self.buttons["merge"].setEnabled(self.merge_target.isEnabled() and self.merge_target.currentIndex() >= 0)
        self.buttons["start"].setEnabled(current and not self.busy)
        self.buttons["undo"].setEnabled(current and not self.busy and bool(undo_stack(result.get("edit_history", ()))))
        self.buttons["show_edited"].setEnabled(not self.busy and "edited" in self.window.mask_display.layers)
        self.brush_label.setEnabled(bool(choosing))
        if not self.busy:
            self.status.setText("No uncommitted edits.")

    def _set_brush(self, label):
        self.brush_id = label
        self.brush_label.setText("" if label is None else str(label))
        if self.layer is not None:
            if label is not None and self.layer.selected_label != label:
                self.layer.selected_label = label
            self._dimensions()

    def _brush_entered(self):
        if not self.busy or self.edit.operation != "paint/erase":
            return
        try:
            label = int(self.brush_label.text())
            if not 0 < label <= self.edit.next_id:
                raise ValueError
        except ValueError:
            self._set_brush(None)
            self.status.setText("Choose a positive brush ID up to " + str(self.edit.next_id) + ".")
            return
        self._set_brush(label)

    def _selection_changed(self, layer, label):
        if layer is self.layer and self.edit.operation == "paint/erase" and self.selected_id != label:
            self.selected_id = label
            self.selected_object.setText("None" if label is None else str(label))
            self._set_brush(label)
        self.refresh()

    def _brush_changed(self, event):
        # Napari's pick tool and keyboard shortcuts can change the brush independently.
        if self.edit.operation == "split":
            self._set_brush(self.edit.parameters["new_id"])
        elif self.edit.operation == "paint/erase":
            value = int(self.layer.selected_label)
            self._set_brush(value if 0 < value <= self.edit.next_id else None)

    def _dimensions(self, event=None):
        if self.busy:
            self.layer.editable = (self.layer.visible and self.viewer.dims.ndisplay == 2 and self.brush_id is not None
                                   and self.edit.operation in ("split", "paint/erase")
                                   and self._owns_preview())

    def _owns_preview(self):
        return (not self.invalidated and self.layer in self.viewer.layers and self.layer.data is self.edit.preview
                and self.state.owns_mask_layer(self.layer) and self.state.segmentation_is_current
                and self.edit.owner == self.state.session_revision())

    def _preview_changed(self, *args):
        if not self._owns_preview():
            self.invalidated = True
            self.status.setText("Preview ownership or data changed. Discard to finish.")
        self._dimensions()
        self.refresh()

    def _layer_removed(self, event):
        if event.value is self.layer:
            self.invalidated = True
            self._preview_changed()

    def _protect_layer(self, layer):
        mode = None
        if layer.editable and type(layer).__name__ in ("Shapes", "Labels", "Points"):
            mode = layer.mode
            layer.editable = False
        self.protected_layers.append((layer, layer.locked, mode))
        layer.locked |= LayerLock.DELETION

    def _layer_inserted(self, event):
        if self._owns_preview() and not event.value.metadata.get("nucytoseg_selection_highlight"):
            self._protect_layer(event.value)

    def _grid_changed(self, event=None):
        try:
            validate_roi_geometry(self.layer, self.state.voxel_spacing_zyx_um, self.edit.base.shape,
                                  translate=self.translate)
        except ValueError as error:
            self.invalidated = True
            self._preview_changed()
            self.status.setText(str(error))

    def _require_preview(self):
        if not self._owns_preview():
            raise ValueError("The editing preview, transform, or session changed. Discard it.")
        validate_roi_geometry(self.layer, self.state.voxel_spacing_zyx_um, self.edit.base.shape,
                              translate=self.translate)

    def return_preview(self):
        self._require_preview()
        self.layer.visible = True
        self.viewer.layers.selection.active = self.layer
        self.window.viewer_toolbar.activate_tool("review")

    def start(self):
        window = self.window
        review = window.context_panel.mask_review_page
        if not window._project_idle() or review._filter_timer.isActive():
            raise ValueError("Wait for the active operation to finish.")
        edit = GeometryEdit(self.state)
        selected = review._selected_id if review._selected_is_raw else None
        raw = window.segmentation.result_layer
        if raw is None:
            raise ValueError("Restore the segmentation layer before editing.")
        spacing = self.state.voxel_spacing_zyx_um
        oy, ox = self.state.working_offset_yx
        self.translate = (0, oy * spacing[1], ox * spacing[2])
        self.previous_layer = self.viewer.layers.selection.active
        self.previous_tool = next((key for key, button in window.viewer_toolbar.buttons.items()
                                   if button.isChecked()), "pan_zoom")
        layer = self.viewer.add_labels(edit.preview, name=f"{self.state.active_session.display_name} · Uncommitted edits",
                                       scale=spacing, translate=self.translate,
                                       metadata={**self.state.mask_layer_metadata(), "nucytoseg_edit_preview": True})
        self.edit, self.layer = edit, layer
        self.invalidated = False
        self.protected_layers = []
        layer.locked = LayerLock.DELETION
        for other in self.viewer.layers:
            if other is not layer:
                self._protect_layer(other)
        self.viewer.layers.events.removed.connect(self._layer_removed)
        self.viewer.layers.events.inserted.connect(self._layer_inserted)
        self.state.segmentation_result_changed.connect(self._preview_changed)
        layer.events.selected_label.connect(self._brush_changed)
        layer.events.visible.connect(self._dimensions)
        layer.events.editable.connect(self._dimensions)
        layer.events.data.connect(self._preview_changed)
        for name in ("scale", "translate", "rotate", "shear", "affine"):
            getattr(layer.events, name).connect(self._grid_changed)
        window.mask_selection.selection_changed.connect(self._selection_changed)
        layer.n_edit_dimensions = 2
        self._dimensions()
        self.locked = []
        for control in (window.navigation, window.open_image_action,
                        window.close_project_action, window.restore_layout_action, window.reset_layout_action,
                        *window.project_actions, window.import_legacy_action, review.show_raw_button,
                        review.show_all_button, review.show_selected_button):
            self.locked.append((control, control.isEnabled()))
            control.setEnabled(False)
        window.mask_display.suspend()
        self.return_preview()
        if selected is not None and (edit.base == selected).any():
            window.mask_selection.restore_selection(layer, selected)
        self.status.setText("Click an object in the preview to select it. Paint or erase in 2D.")
        self.refresh()

    def discard(self):
        self.window.mask_selection.selection_changed.disconnect(self._selection_changed)
        self.layer.events.selected_label.disconnect(self._brush_changed)
        self.layer.events.visible.disconnect(self._dimensions)
        self.layer.events.editable.disconnect(self._dimensions)
        self.layer.events.data.disconnect(self._preview_changed)
        for name in ("scale", "translate", "rotate", "shear", "affine"):
            getattr(self.layer.events, name).disconnect(self._grid_changed)
        self.viewer.layers.events.removed.disconnect(self._layer_removed)
        self.viewer.layers.events.inserted.disconnect(self._layer_inserted)
        self.state.segmentation_result_changed.disconnect(self._preview_changed)
        self.window.mask_selection.clear_selection(self.layer)
        self.layer.locked = LayerLock.NONE
        if self.layer in self.viewer.layers:
            self.viewer.layers.remove(self.layer)
        self.edit = self.layer = None
        self.selected_id = self.brush_id = None
        self.selected_object.setText("None")
        self.brush_label.clear()
        self.clear_merge()
        for other, lock, mode in self.protected_layers:
            if other in self.viewer.layers:
                other.locked = lock
                if mode is not None:
                    other.editable = True
                    other.mode = mode
        self.protected_layers.clear()
        for control, enabled in self.locked:
            control.setEnabled(enabled)
        self.window.mask_display.resume()
        if self.previous_layer in self.viewer.layers and self.previous_layer.visible:
            self.viewer.layers.selection.active = self.previous_layer
            self.window.viewer_toolbar.activate_tool(self.previous_tool)
        self.previous_layer = None
        self.window._update_project_actions()
        self.window.context_panel.mask_review_page._refresh_actions()
        self.refresh()

    def commit(self):
        self._require_preview()
        self.edit.commit()
        self.discard()

    def undo(self):
        if self.window._project_idle():
            GeometryEdit.undo(self.state)

    def remove(self):
        self._require_preview()
        self.edit.remove(self.selected_id)
        self.layer.refresh()
        self._dimensions()
        self.refresh()
        self.status.setText("Removal preview. Commit or discard.")

    def split(self):
        self.return_preview()
        new = self.edit.split(self.selected_id)
        self._set_brush(new)
        self.layer.brush_size = 1
        self.window.viewer_toolbar.set_ndisplay(2)
        self.window.viewer_toolbar.activate_tool("paint")
        self.refresh()
        self.status.setText(f"Paint part of parent {self.selected_id} as ID {new}. Commit to accept the split.")

    def add_merge(self):
        self._require_preview()
        label = self.selected_id
        if label is None or not (self.edit.base == label).any():
            raise ValueError("Select an object from the committed editing source.")
        if self.merge_target.findData(label) < 0:
            self.merge_list.addItem(str(label))
            self.merge_target.addItem(str(label), label)
        self.merge_list.setCurrentRow(self.merge_target.findData(label))
        self.refresh()

    def remove_merge(self):
        row = self.merge_list.currentRow()
        retained = self.merge_target.currentData()
        if row >= 0:
            self.merge_list.takeItem(row)
            self.merge_target.removeItem(row)
            self.merge_target.setCurrentIndex(self.merge_target.findData(retained))
        self.refresh()

    def clear_merge(self):
        self.merge_list.clear()
        self.merge_target.clear()
        self.refresh()

    def merge(self):
        self.return_preview()
        ids = [self.merge_target.itemData(row) for row in range(self.merge_target.count())]
        retained = self.merge_target.currentData()
        self.edit.merge(ids, retained)
        self.window.mask_selection.clear_selection(self.layer)
        self.layer.refresh()
        self._dimensions()
        self.refresh()
        self.status.setText(f"Merge preview retains ID {retained}. Commit or discard.")

    def show_edited(self):
        self.window.mask_display.show("edited")
