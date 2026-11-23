"""Proofreading controls for the Mask Editing page."""
from qtpy.QtWidgets import QGroupBox, QInputDialog, QLabel, QPushButton, QSpinBox, QVBoxLayout

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
        for name, caption in (
            ("start", "Start Editing"), ("commit", "Commit Changes"), ("discard", "Discard Changes"),
            ("undo", "Undo Last Commit"), ("remove", "Remove Selected Object"),
            ("split", "Split Selected Object"), ("merge", "Merge Selected Objects"),
            ("show_edited", "Show Committed Edits"),
        ):
            button = QPushButton(caption)
            layout.addWidget(button)
            button.clicked.connect(lambda checked=False, action=name: self.run(action))
            self.buttons[name] = button
        layout.addWidget(QLabel("Brush / selected object ID"))
        self.label = QSpinBox()
        self.label.setRange(1, 2147483647)
        self.label.valueChanged.connect(self._select_label)
        layout.addWidget(self.label)
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
        for name in ("commit", "discard", "remove", "merge", "split"):
            self.buttons[name].setEnabled(self.busy)
        self.buttons["start"].setEnabled(current and not self.busy)
        self.buttons["undo"].setEnabled(current and not self.busy and bool(undo_stack(result.get("edit_history", ()))))
        self.buttons["show_edited"].setEnabled(not self.busy and "edited" in self.window.mask_display.layers)
        self.label.setEnabled(self.busy)
        if not self.busy:
            self.status.setText("No uncommitted edits.")

    def _select_label(self, label):
        if self.layer is not None:
            self.layer.selected_label = label

    def _picked(self, event):
        if self.layer.selected_label > 0:
            self.label.setValue(int(self.layer.selected_label))

    def _dimensions(self, event=None):
        if self.layer is not None:
            self.layer.editable = self.viewer.dims.ndisplay == 2 and self.edit.operation in ("split", "paint/erase")

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
                                       scale=spacing, translate=self.translate, metadata=self.state.mask_layer_metadata())
        self.edit, self.layer = edit, layer
        self.label.setMaximum(min(2147483647, edit.next_id))
        self.label.setValue(selected or 1)
        self._select_label(self.label.value())
        layer.events.selected_label.connect(self._picked)
        layer.n_edit_dimensions = 2
        self._dimensions()
        self.locked = []
        for control in (window.navigation, window.layers_section, window.open_image_action,
                        window.close_project_action, window.restore_layout_action, window.reset_layout_action,
                        *window.project_actions, window.import_legacy_action, review.show_raw_button,
                        review.show_all_button, review.show_selected_button):
            self.locked.append((control, control.isEnabled()))
            control.setEnabled(False)
        window.mask_display.suspend()
        window.viewer_toolbar.activate_tool("pan_zoom")
        self.status.setText("Uncommitted preview. Paint or erase in 2D, then commit or discard.")
        self.refresh()

    def discard(self):
        self.layer.events.selected_label.disconnect(self._picked)
        if self.layer in self.viewer.layers:
            self.viewer.layers.remove(self.layer)
        self.edit = self.layer = None
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
        if self.layer not in self.viewer.layers or self.layer.data is not self.edit.preview:
            raise ValueError("The edit layer was removed or replaced. Discard this preview.")
        validate_roi_geometry(self.layer, self.state.voxel_spacing_zyx_um, self.edit.base.shape,
                              translate=self.translate)
        self.edit.commit()
        self.discard()

    def undo(self):
        if self.window._project_idle():
            GeometryEdit.undo(self.state)

    def remove(self):
        self.edit.remove(self.label.value())
        self.layer.refresh()
        self._dimensions()
        self.status.setText("Removal preview. Commit or discard.")

    def split(self):
        new = self.edit.split(self.label.value())
        self.label.setValue(new)
        self.layer.brush_size = 1
        self.window.viewer_toolbar.set_ndisplay(2)
        self.window.viewer_toolbar.activate_tool("paint")
        self.status.setText(f"Paint part of the parent as ID {new}. Commit to accept the split.")

    def merge(self):
        labels, ok = QInputDialog.getText(self, "Merge objects", "Object IDs, separated by commas")
        if not ok:
            return
        ids = sorted(set(int(text.strip()) for text in labels.split(",")))
        if len(ids) < 2:
            raise ValueError("Choose at least two different IDs.")
        retained, ok = QInputDialog.getItem(self, "Retained ID", "Choose the existing target ID",
                                             ["Choose an ID", *(str(label) for label in ids)], 0, False)
        if ok and retained == "Choose an ID":
            raise ValueError("Select the target ID to retain.")
        if ok and retained:
            self.edit.merge(ids, int(retained))
            self.layer.refresh()
            self._dimensions()
            self.status.setText(f"Merge preview retains ID {retained}. Commit or discard.")

    def show_edited(self):
        self.window.mask_display.show("edited")
