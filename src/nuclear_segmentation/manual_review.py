"""Whole-object review; original label geometry and IDs remain unchanged."""
import copy
import hashlib
import numpy as np


def mask_signature(masks):
    # Canonical dtype allows uint16 TIFF exports of originally uint32 masks.
    h = hashlib.sha256(str(tuple(masks.shape)).encode())
    for plane in masks:
        h.update(np.asarray(plane, dtype='<u8').tobytes())
    return h.hexdigest()


class ManualReview:
    def __init__(self, masks, ids, saved=None):
        self.signature = mask_signature(masks)
        self.ids = {int(i) for i in ids}
        self.decisions = {}
        self.history = []
        if saved:
            if saved.get('mask_sha256') != self.signature:
                raise ValueError('Manual review does not match these raw masks. Use the original analysis masks.')
            for key, record in saved.get('decisions', {}).items():
                label = int(key)
                if label not in self.ids or record.get('decision') not in {'keep', 'exclude'}:
                    raise ValueError('Invalid saved manual review decision.')
                self.decisions[label] = dict(decision=record['decision'], reason=str(record.get('reason', '')))

    def set(self, label, decision, reason=''):
        label = int(label)
        if label not in self.ids:
            raise ValueError('Select an existing object, not background.')
        if decision not in {'keep', 'exclude', 'automatic'}:
            raise ValueError('Invalid manual decision.')
        self.history.append((label, copy.deepcopy(self.decisions.get(label))))
        if decision == 'automatic':
            self.decisions.pop(label, None)
        else:
            self.decisions[label] = dict(decision=decision, reason=str(reason))

    def undo(self):
        if self.history:
            label, previous = self.history.pop()
            if previous is None:
                self.decisions.pop(label, None)
            else:
                self.decisions[label] = previous

    def apply(self, automatic, eligible):
        result = automatic.copy()
        for label, record in self.decisions.items():
            result.loc[label] = record['decision'] == 'keep'
        return result & eligible

    def to_record(self):
        return dict(mask_sha256=self.signature, decisions={str(k): dict(v) for k,v in self.decisions.items()})


class ManualReviewPanel:
    """Qt widget bound to raw and live Labels selections and the live namespace."""
    def __init__(self, ns, viewer):
        from qtpy.QtWidgets import QWidget, QVBoxLayout, QLabel, QSpinBox, QLineEdit, QPushButton, QCheckBox, QScrollArea
        self.ns, self.viewer = ns, viewer
        self.widget = QWidget(); layout = QVBoxLayout(self.widget)
        note = QLabel('Click a whole object in a 2D slice of raw or live masks.\nKeep overrides appearance filters; ROI and Z guards remain mandatory.\nExport results to save decisions. Geometry editing is disabled.')
        note.setWordWrap(True); layout.addWidget(note)
        self.label = QSpinBox(); self.label.setRange(0, min(max(ns['manual_review'].ids), 2147483647))
        layout.addWidget(QLabel('Mask ID')); layout.addWidget(self.label)
        self.details = QLabel(); self.details.setWordWrap(True); self.details.setTextInteractionFlags(__import__('qtpy.QtCore',fromlist=['Qt']).Qt.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.reason = QLineEdit(); self.reason.setPlaceholderText('Optional reason for this decision'); layout.addWidget(self.reason)
        self.only = QCheckBox('Show selected mask only'); layout.addWidget(self.only)
        self.layers = [viewer.layers['raw Cellpose masks'], viewer.layers['live filtered masks']]
        for layer in self.layers:
            layer.editable = False
            layer.events.selected_label.connect(self.on_selection)
            layer.mouse_drag_callbacks.append(self.on_click)
        buttons = [('Select from all masks', self.pick), ('Show retained masks', self.show_retained), ('Keep selected mask', lambda:self.decide('keep')),
                   ('Exclude selected mask', lambda:self.decide('exclude')),
                   ('Use automatic filters', lambda:self.decide('automatic')), ('Undo last decision', self.undo)]
        self.buttons = []
        for title, fn in buttons:
            b=QPushButton(title); b.clicked.connect(lambda checked=False,f=fn:self.safe(f));layout.addWidget(b);self.buttons.append(b)
        self.message=QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        self.label.valueChanged.connect(self.on_id)
        self.only.toggled.connect(self.isolate)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setWidget(self.widget)
        self.scroll.setMinimumWidth(340)
        self.dock = viewer.window.add_dock_widget(self.scroll, name='Manual mask review', area='right')
        self.refresh()

    def safe(self, fn):
        try:
            fn();self.message.setText('')
        except ValueError as exc:
            self.message.setText(str(exc))

    def on_click(self, layer, event):
        if event.button != 1 or self.viewer.dims.ndisplay != 2:
            return
        coordinates = np.asarray(layer.world_to_data(event.position), dtype=float)
        if coordinates.shape != (3,) or not np.isfinite(coordinates).all():
            return
        index = np.floor(coordinates + 0.5).astype(int)
        if np.all(index >= 0) and np.all(index < np.asarray(layer.data.shape)):
            layer.selected_label = int(layer.data[tuple(index)])

    def on_selection(self, event):
        self.label.setValue(int(event.source.selected_label))

    def on_id(self, value):
        for layer in self.layers:
            if layer.selected_label != value:
                layer.selected_label = value
        record=self.ns['manual_review'].decisions.get(value, {})
        self.reason.setText(record.get('reason',''))
        self.refresh()

    def isolate(self, checked):
        for layer in self.layers:
            layer.show_selected_label = checked

    def pick(self):
        layer=self.layers[0];layer.visible=True
        layer.show_selected_label=False;self.only.setChecked(False)
        self.viewer.layers.selection.active=layer;self.viewer.dims.ndisplay=2;layer.mode='pan_zoom'
        self.layers[1].visible=False

    def show_retained(self):
        self.layers[0].visible=False;self.layers[1].visible=True
        self.viewer.layers.selection.active=self.layers[1]
        self.only.setChecked(False)

    def decide(self, decision):
        if self.ns['current_keep'] is None:
            raise ValueError('Fix invalid filter settings before reviewing objects.')
        if not self.ns['roi_reviewed']:
            raise ValueError('Accept the reviewed ROI before recording manual decisions.')
        label=self.label.value()
        if decision=='keep' and label in self.ns['filter_properties'].index:
            row=self.ns['filter_properties'].loc[label]
            if row['excluded_by_z_guard'] or row['excluded_by_sampling_roi']:
                raise ValueError('This object is outside the counting region or excluded by a Z guard.')
        self.ns['manual_review'].set(label, decision, self.reason.text())
        self.ns['refresh_live_filter']()

    def undo(self):
        self.ns['manual_review'].undo();self.ns['refresh_live_filter']()
        self.reason.setText(self.ns['manual_review'].decisions.get(self.label.value(),{}).get('reason',''))

    def refresh(self):
        label=self.label.value(); table=self.ns['filter_properties']
        if label not in table.index:
            self.details.setText('Select a mask; 0 is background.');return
        row=table.loc[label]; review=self.ns['manual_review'].decisions.get(label,{})
        lines=[f'Mask ID: {label}', 'Decision: '+review.get('decision','automatic')]
        valid=self.ns['current_keep'] is not None and self.ns['roi_reviewed']
        if valid:
            lines.append('Status: '+('retained' if self.ns['current_keep'].loc[label] else 'excluded'))
            if row['excluded_by_z_guard']:lines.append('Blocked by Z guard')
            if row['excluded_by_sampling_roi']:lines.append('Outside ROI or counting Z range')
            if not row['automatic_keep']:lines.append('Automatic criteria failed: '+str(row.get('automatic_failure_reasons','')))
        else:lines.append('Status pending: accept ROI / fix filter settings')
        for key,value in row.items():
            if key in {'volume_um3','sphericity'} or 'intensity' in key or 'background_corrected_mean' in key or 'positive_fraction' in key:
                if isinstance(value,(int,float,np.number)):lines.append(f'{key}: {value:.4g}')
        self.details.setText('\n'.join(lines))

    def close(self):
        for layer in self.layers:
            layer.events.selected_label.disconnect(self.on_selection)
            layer.mouse_drag_callbacks.remove(self.on_click)
        self.viewer.window.remove_dock_widget(self.dock)
        self.widget.deleteLater()
