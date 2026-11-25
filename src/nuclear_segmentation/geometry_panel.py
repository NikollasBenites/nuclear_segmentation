"""Proofreading controls for the Mask Editing page."""
import math

from napari.layers import LayerLock
from qtpy.QtCore import QSignalBlocker, Qt
from qtpy.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QHBoxLayout, QMessageBox,
                            QListWidget, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from .collapsible_section import CollapsibleSection
from .geometry_edit import GeometryEdit, effective_masks, undo_stack
from .guided_panel import GuidedPanel
from .guided_split import OPERATION
from .preview_history import PreviewStrokes, ProofreadingShortcuts
from .ui_icons import icon
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
        self.scientific_status = window.working_mask_status
        layout.addWidget(button("recalculate", "Recalculate Measurements"))
        self.auto_recalculate = QCheckBox("Automatically recalculate\nmeasurements after saving edits")
        self.auto_recalculate.setAccessibleName("Automatically recalculate measurements after saving edits")
        settings = window.workspace_settings.settings
        key = "editing/auto_recalculate"
        self.auto_recalculate.setChecked(settings.value(key, False, type=bool))
        self.auto_recalculate.toggled.connect(lambda enabled: settings.setValue(key, enabled))
        layout.addWidget(self.auto_recalculate)
        self.advanced = CollapsibleSection("geometry_history", "Advanced Layers & History", expanded=False)
        advanced = QVBoxLayout()
        self.advanced.set_content_layout(advanced)
        advanced.addWidget(button("show_edited", "Complete Committed Geometry"))
        advanced.addWidget(button("show_before", "Filtered Before Editing"))
        advanced.addWidget(button("show_retained", "Return to Live Filtered Masks"))
        self.history = QListWidget()
        self.history.setMaximumHeight(100)
        self.history.setAccessibleName("Geometry revisions and editing history")
        advanced.addWidget(self.history)
        advanced.addWidget(button("undo", "Undo Last Saved Edit"))
        layout.addWidget(self.advanced)
        self.guided = GuidedPanel(self)
        layout.addWidget(self.guided)
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
        controls.addRow(button("commit", "Save Geometry Changes"), button("discard", "Discard Changes"))
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
        self.shortcuts = ProofreadingShortcuts(self)
        for action in ("undo", "redo"):
            control = button(action + "_preview", action.title())
            control.setIcon(icon(action))
            control.setToolTip(self.shortcuts.tooltip(action))
        fields.addRow(self.buttons["undo_preview"], self.buttons["redo_preview"])
        fields.addRow(self.buttons["review"])
        fields.addRow(button("apply_operation", "Apply Operation"), button("cancel_operation", "Cancel Operation"))
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
        self.merge_target.currentIndexChanged.connect(self.refresh)
        self.merge_list.currentRowChanged.connect(self.refresh)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.layout().insertWidget(1, self.status)
        review.proofreading_page.layout().insertWidget(1, self)
        self.state.segmentation_result_changed.connect(self._sync_scope)
        window.filtering.status_changed.connect(self.refresh)
        self.viewer.dims.events.ndisplay.connect(self._dimensions)
        self.viewer.dims.events.order.connect(self._dimensions)
        window.viewer_toolbar.tool_changed.connect(self.refresh)
        self.scope.currentIndexChanged.connect(self._change_scope)
        self.state.filtering_result_changed.connect(self._sync_scope)
        window.mask_selection.selection_changed.connect(self._selection_changed)
        self._sync_scope()

    def dispose(self):
        self.shortcuts.dispose()
        self.viewer.dims.events.ndisplay.disconnect(self._dimensions)
        self.viewer.dims.events.order.disconnect(self._dimensions)
        self.window.viewer_toolbar.tool_changed.disconnect(self.refresh)
        self.state.segmentation_result_changed.disconnect(self._sync_scope)
        self.window.filtering.status_changed.disconnect(self.refresh)
        self.state.filtering_result_changed.disconnect(self._sync_scope)
        self.window.mask_selection.selection_changed.disconnect(self._selection_changed)

    def _sync_scope(self, *args):
        current = self.state.proofreading_ids is not None
        self.scope.model().item(0).setEnabled(current)
        if self.busy:
            if not self.edit.selection_current:
                self._preview_changed()
            return
        with QSignalBlocker(self.scope):
            self.scope.setCurrentIndex(0 if current else 1)
        self.refresh()

    def _scope_text(self):
        if self.scope.currentData():
            return "Filtered · retained objects" if self.state.filtering_is_current else "Provisional · retained objects"
        return "All masks" if self.state.proofreading_ids is not None else "All masks · no retained selection available"

    def _change_scope(self):
        if self.busy:
            try:
                self._require_preview()
                if self.strokes.in_stroke:
                    raise ValueError("Finish the current stroke before changing scope.")
                self.edit.set_filtered(self.scope.currentData())
            except ValueError as error:
                with QSignalBlocker(self.scope):
                    self.scope.setCurrentIndex(0 if self.edit.filtered else 1)
                self.status.setText(str(error))
                return
            self.strokes.sync_write_protection()
            self.strokes.clear()
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
                or layer not in (self.window.mask_display.layers.get(role) for role in ("raw", "filtered", "edited"))):
            return None
        if layer is self.window.mask_display.layers.get("filtered") and self.state.proofreading_ids is None:
            return None
        if self.scope.currentData() and label not in (self.state.proofreading_ids or ()):
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
        self.buttons["undo_preview"].setEnabled(bool(available and self._can_replay(False)))
        self.buttons["redo_preview"].setEnabled(bool(available and self._can_replay(True)))
        for name in ("apply_operation", "cancel_operation"):
            self.buttons[name].setVisible(self.busy and self.edit.pending_source is not None)
            self.buttons[name].setEnabled(bool(available))
        watershed = self.busy and self.edit.operation == OPERATION
        self.buttons["apply_operation"].setText("Apply Watershed" if watershed else "Apply Operation")
        self.buttons["cancel_operation"].setText("Cancel Preview" if watershed else "Cancel Operation")
        for name in ("undo_preview", "redo_preview"):
            self.buttons[name].setVisible(not watershed)
        self.guided.refresh()
        choosing = available and not self.edit.dedicated and not self.paused and self.edit.operation == "paint/erase"
        selected = (choosing and self.selected_id is not None and self.layer.visible
                    and self.viewer.layers.selection.active is self.layer)
        for name in ("remove", "split", "add_merge"):
            self.buttons[name].setEnabled(bool(selected))
        self.transaction_controls.setVisible(self.busy)
        self.buttons["start"].setVisible(not self.busy)
        self.buttons["toggle_pause"].setText("Resume Editing" if self.paused else "Pause Editing")
        self.buttons["toggle_pause"].setEnabled(bool(available and not self.edit.dedicated))
        self.buttons["return_preview"].setEnabled(bool(available))
        self.buttons["commit"].setEnabled(bool(available and self.edit.pending_source is None
                                              and (not self.edit.dedicated or self.edit.steps)))
        self.buttons["discard"].setEnabled(self.busy)
        self.buttons["remove_merge"].setEnabled(bool(choosing and self.merge_list.currentRow() >= 0))
        self.buttons["clear_merge"].setEnabled(bool(choosing and self.merge_list.count()))
        choosing_merge = (available and self.edit.merge_selection is not None and
                          self.edit.pending_source is None and not self.edit.steps and not self.edit.redo_steps)
        self.merge_target.setEnabled(bool((choosing or choosing_merge) and self.merge_list.count() >= 2))
        self.buttons["merge"].setEnabled(self.merge_target.isEnabled() and self.merge_target.currentIndex() >= 0)
        self.buttons["start"].setEnabled(current and not self.busy)
        self.buttons["undo"].setEnabled(current and not self.busy and bool(undo_stack(result.get("edit_history", ()))))
        history = result.get("edit_history", ())
        if history is not getattr(self, "_displayed_history", None):
            self.history.clear()
            for record in reversed(history):
                self.history.addItem(f"Revision {record['result_revision']} · {record['operation']}")
                for number, step in enumerate(record.get("steps", ()), 1):
                    ids = ", ".join(map(str, step["affected_ids"]))
                    self.history.addItem(f"    {number}. {step['operation']} · IDs {ids}")
                    self.history.item(self.history.count() - 1).setToolTip(str(step["parameters"] or "Brush stroke"))
            self._displayed_history = history
        self.buttons["undo"].setVisible(self.buttons["undo"].isEnabled())
        self.advanced.setVisible(result.get("edited_masks") is not None)
        self.buttons["show_edited"].setEnabled(not self.busy)
        self.buttons["show_before"].setEnabled(not self.busy and bool(history) and (
            "previous_reviewed_ids" in history[-1] or history[-1].get("editing_scope") == "filtered"))
        self.buttons["show_retained"].setEnabled(not self.busy and self.state.proofreading_ids is not None)
        self.scientific_status.setText(self.window.filtering.working_status)
        self.scientific_status.setVisible(bool(result))
        self.buttons["recalculate"].setVisible(current and (
            self.state.geometry_pending or bool(self.state.active_session.measurement_invalidation_reason)
        ))
        self.buttons["recalculate"].setEnabled(not self.busy and not self.window.filtering.busy)
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
            self.status.setText("Committed geometry · Undo Last Saved Edit is available in History."
                                if self.buttons["undo"].isEnabled() else "No active edit · " + self._scope_text())

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
        if not self._owns_preview():
            return
        if self.edit.operation == "paint/erase":
            step = self.strokes.brush_step(event.value)
            if step["indexes"].size:
                self.edit.add_step(step)
            else:
                self.strokes.clear()
        self.strokes.changed_voxels += self.strokes.count_change(event.value)
        self._history_status()
        self.refresh()

    def _history_status(self):
        if self.edit.guided_context is not None and self.edit.pending_source is None:
            status = ("Applied watershed · Undo or Save Geometry Changes." if self.edit.steps else
                      "Watershed undone · Redo to restore the accepted proposal.")
        elif self.edit.merge_selection is not None and self.edit.pending_source is None:
            status = ("Applied merge · Undo or Save Geometry Changes." if self.edit.steps else
                      "Merge undone · Redo to restore it." if self.edit.redo_steps else
                      "Choose the ID to retain and preview the merge.")
        elif self.edit.operation in ("remove", "merge"):
            status = f"{self.edit.operation.title()} preview · Uncommitted changes"
        elif self.strokes.changed_voxels:
            status = "Uncommitted changes"
        else:
            status = "No pending changes"
        if self.edit.operation == "split":
            status = "Split preview · " + status
        if self.paused:
            status = "Paused · " + status
        self.status.setText(f"{self._scope_text()} · {status}")

    def _can_replay(self, redo):
        if self.edit.pending_source is not None:
            return self.edit.operation == "split" and self.strokes.available(redo)
        return bool(self.edit.redo_steps) if redo else len(self.edit.steps) > self.edit.undo_floor

    def _replay(self, redo):
        self._require_preview()
        if self.strokes.in_stroke or not self._can_replay(redo):
            return
        if self.edit.pending_source is not None:
            self.strokes.replay(redo)
        else:
            step = (self.edit.redo_steps if redo else self.edit.steps)[-1]
            native = None
            if step["operation"] == "paint/erase" and self.strokes.available(redo):
                native = self.layer.redo if redo else self.layer.undo
            self.edit.replay(redo, native)
            if native is None:
                self.strokes.clear()
                self.layer.refresh()
            delta = self.strokes.step_change(step)
            self.strokes.changed_voxels += delta if redo else -delta
            if step["operation"] != "paint/erase":
                self._sync_objects(step)
        self._dimensions()
        self._history_status()

    def _sync_objects(self, step):
        # Reconcile structural selections against the current preview. Ordinary
        # stroke replay keeps the active brush and never scans the whole volume.
        self.clear_merge()
        if self.selected_id is not None and not any((plane == self.selected_id).any() for plane in self.edit.preview):
            self.window.mask_selection.clear_selection(self.layer)
            self.selected_id = None
            self.selected_object.setText("None")
            self._set_brush(None)
        elif (self.selected_id in step["affected_ids"] and self.viewer.layers.selection.active is self.layer
              and self.window.viewer_toolbar.buttons["review"].isChecked()):
            self.window.mask_selection.restore_selection(self.layer, self.selected_id)
        if (self.brush_id in step["affected_ids"] and self.brush_id != self.selected_id
              and not any((plane == self.brush_id).any() for plane in self.edit.preview)):
            self._set_brush(self.selected_id)

    def apply_operation(self):
        self._require_finished_stroke()
        selected = self.edit.parameters.get("retained_id", self.edit.parameters.get("parent"))
        if self.edit.guided_context is not None:
            selected = self.edit.parameters["parent_id"] if self.edit.parameters["residual_voxels"] else None
        self.edit.apply_operation()
        self.guided.remove_reference()
        self.strokes.sync_write_protection()
        self.strokes.clear()
        self.strokes.changed_voxels = self._pending_dirty + self.strokes.step_change(self.edit.steps[-1])
        self._finish_operation(selected)

    def cancel_operation(self):
        self._require_finished_stroke()
        selected = self.edit.parameters.get("parent", self.selected_id)
        selected = self.edit.parameters.get("parent_id", selected)
        self.edit.cancel_operation()
        self.guided.remove_reference()
        self.strokes.sync_write_protection()
        self.strokes.clear()
        self.strokes.changed_voxels = self._pending_dirty
        self._finish_operation(selected)
        if self.edit.merge_selection is not None:
            self._fill_merge_selection(self.edit.merge_selection)

    def _finish_operation(self, selected):
        self.layer.refresh()
        self.clear_merge()
        self.return_preview()
        self.selected_id = selected
        self.selected_object.setText("None" if selected is None else str(selected))
        if selected is None:
            self.window.mask_selection.clear_selection(self.layer)
        else:
            self.window.mask_selection.restore_selection(self.layer, selected)
        self._set_brush(selected)
        self._history_status()

    def undo_preview(self):
        self._replay(False)

    def redo_preview(self):
        self._replay(True)

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
            owned = self._owns_preview()
            if owned:
                self.strokes.set_editable(not self.edit.dedicated and not self.paused and self.layer.visible and self.viewer.dims.ndisplay == 2
                                         and self.brush_id is not None and self.edit.operation in ("split", "paint/erase"))
            else:
                self.layer.editable = False
            self._sync_size()
            self.refresh()

    def _owns_preview(self):
        return (not self.invalidated and self.layer in self.viewer.layers and self.layer.data is self.edit.preview
                and self.state.owns_mask_layer(self.layer) and self.state.segmentation_is_current
                and self.edit.owner == self.state.session_revision()
                and self.edit.selection_current)

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

    def _require_finished_stroke(self):
        self._require_preview()
        if self.strokes.in_stroke:
            raise ValueError("Finish the current brush stroke first.")

    def return_preview(self):
        self._require_preview()
        self.layer.visible = True
        self.viewer.layers.selection.active = self.layer
        self.window.viewer_toolbar.activate_tool("review")

    def start(self, *, minimum_capacity=0):
        window = self.window
        review = window.context_panel.mask_review_page
        if not window._project_idle() or review._filter_timer.isActive():
            raise ValueError("Wait for the active operation to finish.")
        edit = GeometryEdit(self.state, filtered=self.scope.currentData(), track_strokes=True,
                            minimum_capacity=minimum_capacity)
        raw = window.segmentation.result_layer
        if raw is None:
            raise ValueError("Restore the segmentation layer before editing.")
        self.window.association_panel.restore()
        selected = self._prior_selection()
        spacing = self.state.voxel_spacing_zyx_um
        oy, ox = self.state.working_offset_yx
        self.translate = (0, oy * spacing[1], ox * spacing[2])
        self.previous_layer = self.viewer.layers.selection.active
        self.previous_tool = next((key for key, button in window.viewer_toolbar.buttons.items()
                                   if button.isChecked()), "pan_zoom")
        layer = self.viewer.add_labels(edit.preview, name=f"{self.state.active_session.display_name} · Uncommitted edits",
                                       scale=spacing, translate=self.translate,
                                       metadata={**self.state.mask_layer_metadata(), "nucytoseg_edit_preview": True})
        self.strokes = PreviewStrokes(
            layer, edit, self._owns_preview, lambda message: window.statusBar().showMessage(message, 3500)
        )
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
        self.guided.remove_reference()
        self.strokes.release_write_protection()
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
        self.edit = self.layer = self.strokes = None
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
        self.window.association_panel._render()

    def commit(self):
        self._require_finished_stroke()
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
        role = display.preferences.get("mode")
        layer = display.layers.get("filtered" if self.state.provisional_ids is not None else role)
        if layer is not None and layer.visible:
            self.viewer.layers.selection.active = layer
            if selected is not None and any((plane == selected).any() for plane in layer.data):
                self.window.mask_selection.restore_selection(layer, selected)

        if self.auto_recalculate.isChecked():
            self.recalculate()

    def recalculate(self):
        self.window.filtering.calculate()
        self.refresh()

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
        else:
            self._paused_size = self.layer.brush_size
            self.edit.paused = True
            self.window.viewer_toolbar.activate_tool("pan_zoom")
        self._dimensions()
        self._history_status()

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
            if self.state.filtering_result is not None:
                self.state.active_session.intensity_controls = self.window.context_panel.mask_review_page.intensity_control_settings()
            GeometryEdit.undo(self.state)
            if self.auto_recalculate.isChecked():
                self.recalculate()

    def remove(self):
        self._require_finished_stroke()
        self.edit.remove(self.selected_id)
        self.strokes.clear()
        self.strokes.changed_voxels += self.strokes.step_change(self.edit.steps[-1])
        self._sync_objects(self.edit.steps[-1])
        self.return_preview()
        self.layer.refresh()
        self._dimensions()
        self.refresh()
        self.status.setText("Object removed from the working preview. Undo to restore it.")

    def split(self):
        self._require_finished_stroke()
        self.return_preview()
        new = self.edit.split(self.selected_id)
        self._pending_dirty = self.strokes.changed_voxels
        self.strokes.clear()
        self.strokes.sync_write_protection()
        self._set_brush(new)
        self.layer.brush_size = 1
        self.window.viewer_toolbar.set_ndisplay(2)
        self.window.viewer_toolbar.activate_tool("paint")
        self.refresh()
        self.status.setText(f"Paint part of parent {self.selected_id} as ID {new}. Erase to restore parent voxels. Apply or cancel the split.")

    def handoff_merge(self):
        ui = self.window.context_panel.matching_page.candidate_explorer
        selection = ui.selection()
        started = False
        try:
            if not self.guided.idle() or selection is None or not selection.is_current(self.state.matching_pair):
                raise ValueError("Finish active operations and select two or more current candidates in Matching.")
            self.window._select_session(selection.cytoplasmic_revision.session_id)
            self.guided.require_current(selection)
            self.window.set_workflow("proofreading")
            self.guided.require_current(selection)
            self.scope.setCurrentIndex(0)
            self.start()
            started = True
            self.edit.require_guided_context(selection)
            self.edit.merge_selection = selection
            self._dimensions()
            self._fill_merge_selection(selection)
            self.merge_section.set_expanded(True)
        except ValueError as error:
            if started:
                self.discard()
            ui.summary.setText(str(error))
            self.status.setText(str(error))

    def _fill_merge_selection(self, selection):
        for label in selection.ids:
            self.merge_list.addItem(str(label))
            self.merge_target.addItem(str(label), label)
        self.merge_target.setCurrentIndex(-1)
        self.status.setText(f"Nucleus {selection.nucleus} · Choose the ID to retain. "
                            + selection.summary(self.state.matching_pair.review))
        self.refresh()

    def add_merge(self):
        self._require_preview()
        label = self.selected_id
        if label is None or not self.edit.allows_label(label) or not (self.edit.preview == label).any():
            raise ValueError("Select an object from the working preview.")
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
        self._require_finished_stroke()
        self.return_preview()
        ids = [self.merge_target.itemData(row) for row in range(self.merge_target.count())]
        retained = self.merge_target.currentData()
        self.edit.merge(ids, retained)
        self._pending_dirty = self.strokes.changed_voxels
        self.strokes.clear()
        self.window.mask_selection.clear_selection(self.layer)
        self.layer.refresh()
        self._dimensions()
        self.refresh()
        message = f"Merge preview retains ID {retained}. Disconnected parts keep their gaps. Apply or cancel."
        if self.edit.merge_selection is not None:
            message += "\n" + self.edit.merge_selection.summary(self.state.matching_pair.review)
        self.status.setText(message)

    def show_before(self):
        self.window.segmentation.ensure_comparison("before")
        self.window.mask_display.show("before")

    def show_retained(self):
        self.window.mask_display.show("filtered")

    def show_edited(self):
        self.window.segmentation.ensure_comparison("edited")
        self.window.mask_display.show("edited")
