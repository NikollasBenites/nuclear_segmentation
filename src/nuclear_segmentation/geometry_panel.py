"""Proofreading controls for the Mask Editing page."""
import math

from napari.layers import LayerLock
from qtpy.QtCore import QSignalBlocker, Qt
from qtpy.QtWidgets import (QComboBox, QFormLayout, QLabel, QLineEdit, QHBoxLayout, QMessageBox,
                            QListWidget, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from .collapsible_section import CollapsibleSection
from .geometry_edit import GeometryEdit, effective_masks, undo_stack
from .runtime import validate_roi_geometry


class GeometryPanel(CollapsibleSection):
    def __init__(self, window, review):
        super().__init__("geometry_editing", "Geometry Editing")
        self.window = window
        self.state = window.project_state
        self.viewer = window.embedding.viewer
        self.edit = self.layer = None
        layout = QVBoxLayout()
        self.set_content_layout(layout)
        self.buttons = {}
        self.selected_id = self.brush_id = None

        def button(name, caption):
            control = QPushButton(caption)
            control.clicked.connect(lambda checked=False: self.run(name))
            self.buttons[name] = control
            return control

        layout.addWidget(button("start", "Start Editing"))
        layout.addWidget(button("undo", "Undo Last Saved Edit"))
        self.transaction_controls = QWidget()
        layout.addWidget(self.transaction_controls)
        layout = QVBoxLayout(self.transaction_controls)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QFormLayout()
        controls.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.scope = QComboBox()
        self.scope.addItem("Edit Filtered Masks", True)
        self.scope.addItem("Edit All Masks", False)
        controls.addRow(self.scope)
        controls.addRow(button("commit", "Save Changes"), button("discard", "Discard Changes"))
        self.buttons["commit"].setToolTip("Apply validated mask edits to this session. Save Project writes them to disk.")
        controls.addRow(button("toggle_pause", "Pause Editing"), button("stop", "Stop Editing"))
        layout.addLayout(controls)

        fields = QFormLayout()
        fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.selected_object = QLabel("None")
        self.brush_label = QLineEdit()
        self.brush_label.setPlaceholderText("Select an object or enter an ID")
        self.brush_label.editingFinished.connect(self._brush_entered)
        fields.addRow("Selected Object ID", self.selected_object)
        fields.addRow("Brush Label ID", self.brush_label)
        self.brush_size = QSpinBox()
        self.brush_size.setRange(1, 2**31 - 1)
        self.brush_size.setSuffix(" px")
        self.brush_slider = QSlider(Qt.Orientation.Horizontal)
        self.brush_slider.setRange(1, 40)
        sizes = QHBoxLayout()
        sizes.addWidget(self.brush_slider)
        sizes.addWidget(self.brush_size)
        fields.addRow("Brush Size", sizes)
        self.brush_readout = QLabel()
        self.brush_readout.setWordWrap(True)
        fields.addRow(self.brush_readout)
        self.brush_size.valueChanged.connect(self._set_size)
        self.brush_slider.valueChanged.connect(self._set_size)
        for tool, title in (("paint", "Paint"), ("erase", "Erase"), ("review", "Review / Select")):
            control = QPushButton(title)
            control.setCheckable(True)
            control.clicked.connect(lambda checked=False, key=tool: window.viewer_toolbar.activate_tool(key))
            self.buttons[tool] = control
        fields.addRow(self.buttons["paint"], self.buttons["erase"])
        fields.addRow(self.buttons["review"])
        self.tool_hint = QLabel()
        self.tool_hint.setWordWrap(True)
        fields.addRow(self.tool_hint)
        fields.addRow(button("remove", "Remove Selected Object"))
        fields.addRow(button("split", "Split Selected Object"))
        fields.addRow(button("return_preview", "Return to Editing Preview"))
        layout.addLayout(fields)

        self.merge_section = CollapsibleSection("geometry_merge", "Merge Selection", expanded=False)
        merge = QVBoxLayout()
        self.merge_section.set_content_layout(merge)
        layout.addWidget(self.merge_section)
        merge.addWidget(button("add_merge", "Add Selected Object to Merge"))
        self.merge_list = QListWidget()
        self.merge_list.setMaximumHeight(85)
        self.merge_list.setAccessibleName("Objects selected for merge")
        merge.addWidget(self.merge_list)
        merge.addWidget(button("remove_merge", "Remove ID from List"))
        merge.addWidget(button("clear_merge", "Clear Merge Selection"))
        self.merge_target = QComboBox()
        self.merge_target.setAccessibleName("Retained target ID")
        self.merge_target.setPlaceholderText("Choose the retained ID")
        merge.addWidget(QLabel("Retained target ID"))
        merge.addWidget(self.merge_target)
        merge.addWidget(button("merge", "Preview Merge"))
        review.show_raw_button.parentWidget().layout().insertWidget(1, button("show_edited", "Show Complete Geometry (Diagnostic)"))
        review.show_raw_button.parentWidget().layout().insertWidget(2, button("show_retained", "Show Committed — Retained"))
        review.show_raw_button.parentWidget().layout().insertWidget(3, button("show_before", "Show Filtered — Before Editing"))
        self.merge_target.currentIndexChanged.connect(self.refresh)
        self.merge_list.currentRowChanged.connect(self.refresh)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.layout().insertWidget(1, self.status)
        review.pages["mask_editing"].layout().insertWidget(1, self)
        self.state.segmentation_result_changed.connect(self.refresh)
        self.viewer.dims.events.ndisplay.connect(self._dimensions)
        self.viewer.dims.events.order.connect(self._dimensions)
        window.viewer_toolbar.tool_changed.connect(self.refresh)
        self.scope.currentIndexChanged.connect(self._change_scope)
        self.state.filtering_result_changed.connect(self._sync_scope)
        window.mask_selection.selection_changed.connect(self._selection_changed)
        self._sync_scope()

    def dispose(self):
        self.viewer.dims.events.ndisplay.disconnect(self._dimensions)
        self.viewer.dims.events.order.disconnect(self._dimensions)
        self.window.viewer_toolbar.tool_changed.disconnect(self.refresh)
        self.state.segmentation_result_changed.disconnect(self.refresh)
        self.state.filtering_result_changed.disconnect(self._sync_scope)
        self.window.mask_selection.selection_changed.disconnect(self._selection_changed)

    def _sync_scope(self, *args):
        current = self.state.filtering_is_current and self.state.filtering_result is not None
        self.scope.model().item(0).setEnabled(current)
        if self.busy:
            if self.edit.filtered and not self.edit.filter_current:
                self._preview_changed()
            return
        with QSignalBlocker(self.scope):
            self.scope.setCurrentIndex(0 if current else 1)
        self.refresh()

    def _scope_text(self):
        if self.scope.currentData():
            return "Filtered · retained objects"
        return "All masks" if self.state.filtering_is_current else "All masks · filtered editing unavailable"

    def _change_scope(self):
        if self.busy:
            try:
                self._require_preview()
                self.edit.set_filtered(self.scope.currentData())
            except ValueError as error:
                with QSignalBlocker(self.scope):
                    self.scope.setCurrentIndex(0 if self.edit.filtered else 1)
                self.status.setText(str(error))
                return
            self.layer.editable = False  # Clear napari stroke history for the previous presentation.
            self._dimensions()
            self.layer.refresh()
            if self.selected_id is not None and not self.edit.allows_label(self.selected_id):
                self.window.mask_selection.clear_selection(self.layer)
                self.selected_id = None
                self.selected_object.setText("None")
                self._set_brush(None)
            if self.brush_id is not None and not self.edit.allows_label(self.brush_id):
                self._set_brush(None)
            for row in reversed(range(self.merge_target.count())):
                if not self.edit.allows_label(self.merge_target.itemData(row)):
                    self.merge_list.setCurrentRow(row)
                    self.remove_merge()
            status = "Editing active" if self.edit.is_pristine() else "Uncommitted changes"
            self.status.setText(f"{self._scope_text()} · {status}")
        self.refresh()

    def _prior_selection(self):
        selection = self.window.mask_selection
        layer, label = selection.selected_layer, selection.selected_mask_id
        if (label is None or not self.state.owns_mask_layer(layer)
                or layer not in (self.window.mask_display.layers.get(role) for role in ("raw", "filtered", "edited", "retained"))):
            return None
        if layer is self.window.mask_display.layers.get("filtered") and not self.state.filtering_is_current:
            return None
        if self.scope.currentData() and label not in (self.state.filtering_result or {}).get("retained_ids", ()):
            return None
        result = self.state.segmentation_result
        if result is None or not self.state.segmentation_is_current:
            return None
        source = effective_masks(result)
        if layer.data is result["masks"] and source is not layer.data:
            if not any((plane == label).any() for plane in source):
                return None
        return label

    @property
    def busy(self):
        return self.edit is not None

    @property
    def paused(self):
        return self.busy and self.edit.paused

    def run(self, action):
        try:
            getattr(self, action)()
        except ValueError as error:
            self.status.setText(str(error))

    def refresh(self, *args):
        result = self.state.segmentation_result or {}
        current = self.state.segmentation_is_current
        available = self.busy and self._owns_preview()
        choosing = available and not self.paused and self.edit.operation == "paint/erase"
        selected = (choosing and self.selected_id is not None and self.layer.visible
                    and self.viewer.layers.selection.active is self.layer)
        for name in ("remove", "split", "add_merge"):
            self.buttons[name].setEnabled(bool(selected))
        self.transaction_controls.setVisible(self.busy)
        self.buttons["start"].setVisible(not self.busy)
        self.buttons["toggle_pause"].setText("Resume Editing" if self.paused else "Pause Editing")
        self.buttons["toggle_pause"].setEnabled(bool(available))
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
        self.buttons["undo"].setVisible(self.buttons["undo"].isEnabled())
        self.buttons["show_retained"].setVisible("retained" in self.window.mask_display.layers)
        self.buttons["show_before"].setVisible("before" in self.window.mask_display.layers)
        self.buttons["show_before"].setEnabled(not self.busy)
        self.buttons["show_retained"].setEnabled(not self.busy)
        if self.busy:
            active = self.viewer.layers.selection.active is self.layer and self.layer.visible
            can_paint = available and active and self.layer.editable and not self.paused
            for key in ("paint", "erase", "review"):
                self.buttons[key].setEnabled(active if key == "review" else bool(can_paint))
                toolbar = self.window.viewer_toolbar.buttons[key]
                checked = active and toolbar.isChecked() and (key == "review" or str(self.layer.mode) == key)
                self.buttons[key].setChecked(checked)
            self.brush_size.setEnabled(bool(can_paint))
            self.brush_slider.setEnabled(bool(can_paint))
            hint = ""
            if self.paused:
                hint = "Resume editing to use the brush."
            elif not active:
                hint = "Return to the editing preview to use the brush."
            elif self.viewer.dims.ndisplay != 2:
                hint = "Switch to a 2D slice to use the brush."
            elif self.brush_id is None:
                hint = "Select an object or enter a brush label ID."
            elif not can_paint:
                hint = "Brush unavailable for this preview."
            self.tool_hint.setText(hint)
            self.tool_hint.setVisible(bool(hint))
        self.brush_label.setEnabled(bool(choosing))
        self.scope.setEnabled(not self.busy or choosing)
        self.merge_list.setVisible(self.merge_list.count() > 0)
        if not self.busy:
            label = self._prior_selection()
            self.selected_object.setText("None" if label is None else str(label))
            self.status.setText("No active edit · " + self._scope_text())

    def _set_size(self, value):
        if self.busy and not self.paused:
            self.layer.brush_size = value

    def _sync_size(self, event=None):
        size = self.layer.brush_size
        if self.paused and size != self._paused_size:
            self.layer.brush_size = self._paused_size
            return
        with QSignalBlocker(self.brush_size), QSignalBlocker(self.brush_slider):
            self.brush_size.setValue(size)
            self.brush_slider.setMaximum(max(40, size))
            self.brush_slider.setValue(size)
        axes = tuple(reversed(self.viewer.dims.displayed))
        spacing = self.state.voxel_spacing_zyx_um
        if len(axes) != 2:
            text = "Brush available in 2D slices"
        elif not self.state.calibration_source or spacing is None or not all(math.isfinite(v) and v > 0 for v in spacing):
            text = f"{size} px · physical size unknown"
        else:
            x, y = axes
            plane = "ZYX"[x] + "ZYX"[y]
            text = f"{size} px ≈ {size * spacing[x]:.2f} × {size * spacing[y]:.2f} µm (nominal {plane})"
        self.brush_readout.setText(text)

    def _set_brush(self, label):
        self.brush_id = label
        self.brush_label.setText("" if label is None else str(label))
        if self.layer is not None:
            if label is not None and self.layer.selected_label != label:
                self.layer.selected_label = label
            self._dimensions()

    def _brush_entered(self):
        if not self.busy or self.paused or self.edit.operation != "paint/erase":
            return
        try:
            label = int(self.brush_label.text())
            if not self.edit.allows_label(label):
                self._set_brush(None)
                self.status.setText("This ID is excluded. Use Edit All Masks to edit it.")
                return
            if not 0 < label <= self.edit.next_id:
                raise ValueError
        except ValueError:
            self._set_brush(None)
            self.status.setText("Choose a positive brush ID up to " + str(self.edit.next_id) + ".")
            return
        self._set_brush(label)

    def _selection_changed(self, layer, label):
        if self.busy and not self.paused and layer is self.layer and self.edit.operation == "paint/erase" and self.selected_id != label:
            self.selected_id = label
            self.selected_object.setText("None" if label is None else str(label))
            self._set_brush(label)
        self.refresh()

    def _painted(self, event):
        status = "Split preview" if self.edit.operation == "split" else "Uncommitted changes"
        self.status.setText(f"{self._scope_text()} · {status}")

    def _brush_changed(self, event):
        # Napari's pick tool and keyboard shortcuts can change the brush independently.
        if self.paused:
            self._set_brush(self.brush_id)
        elif self.edit.operation == "split":
            self._set_brush(self.edit.parameters["new_id"])
        elif self.edit.operation == "paint/erase":
            value = int(self.layer.selected_label)
            self._set_brush(value if 0 < value <= self.edit.next_id and self.edit.allows_label(value) else None)

    def _dimensions(self, event=None):
        if self.busy:
            self.layer.editable = (not self.paused and self.layer.visible and self.viewer.dims.ndisplay == 2 and self.brush_id is not None
                                   and self.edit.operation in ("split", "paint/erase")
                                   and self._owns_preview())
            self._sync_size()
            self.refresh()

    def _owns_preview(self):
        return (not self.invalidated and self.layer in self.viewer.layers and self.layer.data is self.edit.preview
                and self.state.owns_mask_layer(self.layer) and self.state.segmentation_is_current
                and self.edit.owner == self.state.session_revision()
                and (not self.edit.filtered or self.edit.filter_current))

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
        selected = self._prior_selection()
        edit = GeometryEdit(self.state, filtered=self.scope.currentData())
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
        self.selected_object.setText("None")
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
        layer.brush_size = 3
        layer.events.brush_size.connect(self._sync_size)
        layer.events.paint.connect(self._painted)
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
        if selected is not None and any((plane == selected).any() for plane in edit.preview):
            window.mask_selection.restore_selection(layer, selected)
        self.set_expanded(True)
        self.status.setText(self._scope_text() + " · Editing active")
        self.refresh()

    def discard(self):
        self.layer.events.brush_size.disconnect(self._sync_size)
        self.layer.events.paint.disconnect(self._painted)
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
        self._sync_scope()

    def commit(self):
        self._require_preview()
        session = self.state.active_session
        if self.state.filtering_result is not None:
            controls = self.window.context_panel.mask_review_page.intensity_control_settings()
        else:
            controls = None
        selected = self.edit.parameters.get("retained_id", self.selected_id)
        self.edit.commit()
        if controls is not None:
            session.intensity_controls = controls
        self.discard()
        display = self.window.mask_display
        layer = display.layers.get(display.preferences["mode"])
        if layer is not None and layer.visible:
            self.viewer.layers.selection.active = layer
            if selected is not None and any((plane == selected).any() for plane in layer.data):
                self.window.mask_selection.restore_selection(layer, selected)

    def toggle_pause(self):
        self._require_preview()
        if self.paused:
            self.return_preview()
            if self.selected_id is None:
                self.window.mask_selection.clear_selection(self.layer)
            else:
                self.window.mask_selection.restore_selection(self.layer, self.selected_id)
            self.edit.paused = False
            self._set_brush(self.brush_id)
            status = "Editing active" if self.edit.is_pristine() else "Uncommitted changes"
        else:
            self._paused_size = self.layer.brush_size
            self.edit.paused = True
            self.window.viewer_toolbar.activate_tool("pan_zoom")
            status = "Paused"
        self._dimensions()
        self.status.setText(f"{self._scope_text()} · {status}")

    def stop(self):
        if not self.edit.has_changes():
            self.discard()
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Stop Editing?")
        dialog.setText("Save the pending mask changes to this session?")
        save = dialog.addButton("Save Changes", QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton("Discard Changes", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        if dialog.clickedButton() is save:
            self.commit()
        elif dialog.clickedButton() is discard:
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
        self.status.setText("Removal preview. Save or discard.")

    def split(self):
        self.return_preview()
        new = self.edit.split(self.selected_id)
        self._set_brush(new)
        self.layer.brush_size = 1
        self.window.viewer_toolbar.set_ndisplay(2)
        self.window.viewer_toolbar.activate_tool("paint")
        self.refresh()
        self.status.setText(f"Paint part of parent {self.selected_id} as ID {new}. Save to accept the split.")

    def add_merge(self):
        self._require_preview()
        label = self.selected_id
        if label is None or not self.edit.allows_label(label) or not (self.edit.base == label).any():
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
        self.status.setText(f"Merge preview retains ID {retained}. Save or discard.")

    def show_before(self):
        self.window.mask_display.show("before")

    def show_retained(self):
        self.window.mask_display.show("retained")

    def show_edited(self):
        self.window.mask_display.show("edited")
