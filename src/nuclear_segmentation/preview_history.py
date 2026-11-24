"""Stroke history and window-scoped shortcuts for the active proofreading preview."""
import sys

import numpy as np
from napari.layers import Labels, Points, Shapes
from qtpy.QtCore import QEvent, QObject
from qtpy.QtGui import QKeySequence
from qtpy.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QLineEdit,
                            QPlainTextEdit, QTextEdit, QWidget)

from .geometry_edit import voxel_step


_SKIPPED_VOXELS = "Protected excluded-object voxels were skipped."
_EXCLUDED_BRUSH = "This brush ID is excluded. Use Edit All Masks to edit it."


class PreviewStrokes:
    """Keep napari's bounded stroke queues on one preview and one editing scope.

    Labels exposes public replay methods and paint events. Queue availability,
    grouped stroke atoms and the read-only reset are internal to napari 0.10.
    """
    def __init__(self, layer, edit, owns_preview, notify):
        self.layer = layer
        self.edit = edit
        self.changed_voxels = 0
        self.original_writes = None
        self.owns_preview = owns_preview
        self.notify = notify
        self.sync_write_protection()

    def clear(self):
        self.layer._undo_history.clear()
        self.layer._redo_history.clear()

    def set_editable(self, editable):
        if self.layer.editable and not editable:
            # Labels replaces both queues when entering read-only mode. Keep the
            # original queues (no copies) for pause, navigation and 3D viewing.
            self.layer._commit_staged_history()
            undo, redo = self.layer._undo_history, self.layer._redo_history
            self.layer.editable = False
            self.layer._undo_history, self.layer._redo_history = undo, redo
        else:
            self.layer.editable = editable

    def available(self, redo):
        return bool(self.layer._redo_history if redo else self.layer._undo_history)

    def brush_step(self, atoms):
        """Normalize a grouped native stroke, preserving first-before and last-after on overlap."""
        indices, before, after = [], [], []
        shape = self.layer.data.shape
        for atom in atoms:
            if hasattr(atom, "slice_key"):
                extent = tuple(len(range(*sl.indices(n))) for sl, n in zip(atom.slice_key, shape, strict=True))
                local = np.indices(extent).reshape(len(shape), -1) if atom.mask is None else np.nonzero(atom.mask)
                coords = tuple(sl.indices(n)[0] + v * sl.indices(n)[2]
                               for sl, n, v in zip(atom.slice_key, shape, local, strict=True))
                old, new = atom.old_values, atom.new_value
            else:
                coords, old, new = atom
            locations = np.ravel_multi_index(coords, shape).ravel()
            indices.append(locations)
            before.append(np.asarray(old).ravel())
            after.append(np.broadcast_to(new, locations.shape))
        idx = np.concatenate(indices)
        unique, first = np.unique(idx, return_index=True)
        _, last = np.unique(idx[::-1], return_index=True)
        old, new = np.concatenate(before)[first], np.concatenate(after)[len(idx) - 1 - last]
        changed = old != new
        return voxel_step("paint/erase", {}, unique[changed], old[changed], new[changed], self.edit.scope)

    def step_change(self, step):
        coords = np.unravel_index(step["indexes"], self.layer.data.shape)
        return self.count_change([(coords, step["before"], step["after"])])

    def sync_write_protection(self):
        # Intercept the same two napari write paths before either history or
        # the displayed slice texture can see a protected voxel.
        layer, edit = self.layer, self.edit
        if not edit.filtered and edit.operation != "split":
            self.release_write_protection()
            return
        if self.original_writes is not None:
            return
        paint_region = layer._paint_region_with_mask
        setitem = layer.data_setitem

        def paint(slice_key, mask, new_label, dims_to_paint, refresh=True, region_data=None):
            if edit.paused or not self.owns_preview():
                return
            extra_axes = tuple(i for i in range(layer.ndim) if i not in dims_to_paint)
            if edit.operation == "split":
                parent, child = edit.parameters["parent"], edit.parameters["new_id"]
                if new_label not in (child, 0):
                    return
                allowed = np.squeeze(edit.pending_source[slice_key], axis=extra_axes) == parent
                new_label = child if new_label else parent
            elif edit.operation == "paint/erase":
                if not edit.allows_label(new_label):
                    self.notify(_EXCLUDED_BRUSH)
                    return
                hidden = np.squeeze(edit.protected_voxels(slice_key), axis=extra_axes)
                if np.any(mask & hidden):
                    self.notify(_SKIPPED_VOXELS)
                allowed = ~hidden
            else:
                return
            clipped = mask & allowed
            if clipped.any():
                paint_region(slice_key, clipped, new_label, dims_to_paint, refresh, region_data)

        def write(indices, value, refresh=True):
            if edit.paused or not self.owns_preview():
                return
            coords = tuple(np.broadcast_arrays(*indices))
            values = np.asarray(value)
            if values.ndim:
                values = np.broadcast_to(values, coords[0].shape)
            if edit.operation == "split":
                parent, child = edit.parameters["parent"], edit.parameters["new_id"]
                allowed = (edit.pending_source[coords] == parent) & np.isin(values, (0, child))
                values = np.where(values == 0, parent, child)
            elif edit.operation == "paint/erase":
                hidden = edit.protected_voxels(coords)
                invalid = edit.excluded_values(values)
                if invalid.any():
                    self.notify(_EXCLUDED_BRUSH)
                elif hidden.any():
                    self.notify(_SKIPPED_VOXELS)
                allowed = ~(invalid | hidden)
            else:
                return
            if not allowed.any():
                return
            if not allowed.all():
                coords = tuple(axis[allowed] for axis in coords)
                if values.ndim:
                    values = values[allowed]
            setitem(coords, values if values.ndim else values.item(), refresh)

        self.original_writes = {name: vars(layer)[name] for name in ("_paint_region_with_mask", "data_setitem")
                                if name in vars(layer)}
        layer._paint_region_with_mask = paint
        layer.data_setitem = write

    def release_write_protection(self):
        if self.original_writes is None:
            return
        for name in ("_paint_region_with_mask", "data_setitem"):
            if name in self.original_writes:
                setattr(self.layer, name, self.original_writes[name])
            else:
                delattr(self.layer, name)
        self.original_writes = None

    def count_change(self, atoms):
        # Compare only the touched voxels. Overlapping strokes, including an
        # erase that cancels an earlier paint, contribute their exact net change.
        edit = self.edit
        source = edit.filter_record["filtered_masks"] if edit.filtered and edit.filter_record is not None else edit.base
        delta = 0
        for atom in atoms:
            if hasattr(atom, "slice_key"):
                reference = source[atom.slice_key]
                if atom.mask is not None:
                    reference = reference[atom.mask]
                old, new = atom.old_values, atom.new_value
            else:
                indices, old, new = atom
                reference = source[indices]
            if edit.filtered and edit.filter_record is None:
                # Membership cost depends on the stroke footprint even for sessions
                # with many retained IDs. Raw labels under the hidden scope read as 0.
                reference = np.where(edit.excluded_values(reference), 0, reference)
            delta += np.count_nonzero(reference != new) - np.count_nonzero(reference != old)
        return delta

    def replay(self, redo):
        queue = self.layer._redo_history if redo else self.layer._undo_history
        delta = self.count_change(queue[-1])
        if redo:
            self.layer.redo()
            self.changed_voxels += delta
        else:
            self.layer.undo()
            self.changed_voxels -= delta

    @property
    def in_stroke(self):
        return self.layer._block_history


class ProofreadingShortcuts(QObject):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.bindings = {}
        for name, standard in (("undo", QKeySequence.StandardKey.Undo), ("redo", QKeySequence.StandardKey.Redo)):
            keys = QKeySequence.keyBindings(standard)
            if name == "redo":
                # Qt maps portable Ctrl to Command on macOS.
                keys.append(QKeySequence("Ctrl+Shift+Z"))
                if sys.platform != "darwin":
                    keys.append(QKeySequence("Ctrl+Y"))
            self.bindings[name] = list(dict.fromkeys(keys))
        self.bindings["escape"] = [QKeySequence("Escape")]
        QApplication.instance().installEventFilter(self)

    def tooltip(self, name):
        keys = (key.toString(QKeySequence.SequenceFormat.NativeText) for key in self.bindings[name])
        return f"{name.title()} preview edit ({', '.join(keys)})"

    @staticmethod
    def _text_input(widget):
        while widget is not None:
            if isinstance(widget, (QLineEdit, QAbstractSpinBox, QPlainTextEdit, QTextEdit)):
                return True
            if isinstance(widget, QComboBox) and widget.isEditable():
                return True
            widget = widget.parentWidget()
        return False

    def eventFilter(self, receiver, event):
        kind = event.type()
        if kind not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        if not isinstance(receiver, QWidget) or QWidget.window(receiver) is not self.panel.window:
            return False
        sequence = QKeySequence(event.keyCombination())
        action = next((name for name, keys in self.bindings.items() if sequence in keys), None)
        if action is None or self._text_input(receiver):
            return False
        panel = self.panel
        if action == "escape":
            if (not panel.busy or panel.paused or panel.strokes.in_stroke or not panel._owns_preview()
                    or QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None
                    or self._text_input(QApplication.focusWidget()) or str(panel.layer.mode) not in ("paint", "erase")
                    or panel.viewer.layers.selection.active is not panel.layer):
                return False
            button = panel.buttons["review"]
        elif panel.busy:
            button = panel.buttons[action + "_preview"]
        elif action == "undo" and panel.window.navigation.current_workflow in ("mask_review", "proofreading"):
            current = panel.viewer.layers.selection.active
            if isinstance(current, (Labels, Points, Shapes)) and current.editable:
                return False
            button = panel.buttons["undo"]
            if not button.isEnabled():
                return False
        else:
            return False
        # Claim the override before Qt actions, then consume the press before
        # QtViewer forwards it to napari's Labels CtrlCmd+Z/Shift+Z bindings.
        event.accept()
        # A text editor may ignore a shortcut (e.g. an empty undo stack) and
        # propagate it to its parent. Keep that press away from canvas bindings.
        if self._text_input(QApplication.focusWidget()):
            return True
        if kind == QEvent.Type.KeyPress and not event.isAutoRepeat() and button.isEnabled():
            button.click()
        return True

    def dispose(self):
        QApplication.instance().removeEventFilter(self)
