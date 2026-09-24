"""Correction of a selected image layer before starting segmentation."""
import copy
import numpy as np
from qtpy.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel, QPushButton, QDoubleSpinBox, QCheckBox, QFileDialog, QDialog, QListWidget, QAbstractItemView, QDialogButtonBox
from .z_correction import correct_z, DEFAULTS
from .normalization import source_identity


class ZCorrectionPanel(QWidget):
    def __init__(self, preview):
        super().__init__()
        self.preview = preview
        self.launcher = preview.launcher
        self.viewer = preview.viewer
        self.outputs = {}
        self.records = {}
        layout = QVBoxLayout(self)
        note = QLabel('Select an original image layer, then correct Z.\nPositive pixels define the reference; zeros remain zero.\nMasks keep the original voxel grid. Cellpose normalization is separate.')
        note.setWordWrap(True); layout.addWidget(note)
        form = QFormLayout(); layout.addLayout(form)
        self.fields = {}
        for key, label, low, high in [
                ('percentile', 'Reference percentile', .001, 100),
                ('sigma_planes', 'Z smoothing sigma (planes)', 0, 1000),
                ('min_gain', 'Minimum gain', .001, 100),
                ('max_gain', 'Maximum gain', .001, 100)]:
            field = QDoubleSpinBox(); field.setRange(low, high); field.setDecimals(3)
            field.setValue(DEFAULTS[key]); form.addRow(label, field); self.fields[key] = field
        self.segment = QCheckBox('Use corrected channel for segmentation'); self.segment.setChecked(True)
        self.measure = QCheckBox('Measure this channel on the corrected image')
        layout.addWidget(self.segment); layout.addWidget(self.measure)
        for title, fn in [('Correct Z of selected layer', self.correct),
                          ('Apply selected correction to analysis', self.apply),
                          ('Show selected Z profile', self.plot),
                          ('Save reviewed ROI for analysis…', self.save_roi),
                          ('Reset all Z correction settings', self.reset)]:
            button = QPushButton(title); button.clicked.connect(lambda checked=False, f=fn: self.launcher.safe(f)); layout.addWidget(button)
        self.include_export = QCheckBox('Include corrected images in analysis export')
        self.include_export.setChecked(self.launcher.get_config()['EXPORT_CORRECTED_IMAGES'])
        self.include_export.toggled.connect(self.set_export_option)
        layout.addWidget(self.include_export)
        for title, fn in [('Save corrected TIFF…', self.save_corrected),
                          ('Export selected image layers as multichannel TIFF…', self.save_selected)]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, f=fn: self.launcher.safe(f))
            layout.addWidget(button)
        self.status = QLabel('No correction applied. Measurements default to original images.')
        self.status.setWordWrap(True); layout.addWidget(self.status)

    def set_export_option(self, checked):
        cfg = self.launcher.get_config(); cfg['EXPORT_CORRECTED_IMAGES'] = checked
        self.launcher.apply_config(cfg)

    def save_corrected(self):
        name, _ = self.selected()
        if name not in self.outputs or self.outputs[name] not in self.viewer.layers:
            raise ValueError('Correct the selected channel first.')
        self.save_layers([self.outputs[name]])

    def save_selected(self):
        layers = [layer for layer in self.viewer.layers if layer in self.viewer.layers.selection]
        if not layers:
            raise ValueError('Select the image layers to export in the Napari layer list.')
        # Explicit ordering prevents ambiguous channel numbering.
        dialog = QDialog(self); dialog.setWindowTitle('Export channel order — drag to reorder')
        box = QVBoxLayout(dialog)
        box.addWidget(QLabel('Channel 1 is the first row. Drag rows to set the order.'))
        rows = QListWidget(); rows.setDragDropMode(QAbstractItemView.InternalMove)
        rows.addItems([l.name for l in layers]); box.addWidget(rows)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); box.addWidget(buttons)
        if not dialog.exec():
            return
        lookup = {l.name: l for l in layers}
        self.save_layers([lookup[rows.item(i).text()] for i in range(rows.count())])

    def save_layers(self, layers):
        from .image_export import export_layers
        records = []
        for layer in layers:
            match = next((name for name, original in self.preview.original_layers.items()
                          if layer is original or layer is self.outputs.get(name)), None)
            if match is None:
                raise ValueError('Select original channels loaded by this preview or their corrected outputs.')
            corrected = layer is self.outputs.get(match)
            records.append(dict(channel=match, source_path=str(self.preview.session.input_path),
                                corrected=corrected, correction=self.records.get(match) if corrected else None))
        path, _ = QFileDialog.getSaveFileName(self, 'Save image data as OME-TIFF',
                                             'selected_channels.ome.tif', 'OME-TIFF (*.ome.tif *.ome.tiff)')
        if not path:
            return
        saved = export_layers(path, layers, self.preview.session.ns['VOXEL_SPACING_UM'], records,
                              [self.preview.session.input_path, self.launcher.get_config().get('MNTB_ROI_MASK_PATH')])
        self.status.setText('Saved image data and correction metadata: ' + str(saved))

    def selected(self):
        active = self.viewer.layers.selection.active
        for name, original in self.preview.original_layers.items():
            if active is original or active is self.outputs.get(name):
                if original not in self.viewer.layers:
                    raise ValueError('The original layer was removed. Reopen the preview.')
                return name, original
        raise ValueError('Select an original channel or its Z corrected image layer.')

    def options(self):
        return {**{k: v.value() for k, v in self.fields.items()},
                'segmentation': self.segment.isChecked(), 'measurement': self.measure.isChecked()}

    def correct(self):
        name, original = self.selected()
        # Always recompute from the original, including when output is selected.
        data, report = correct_z(original.data, self.options())
        old = self.outputs.get(name)
        if old is not None and old in self.viewer.layers:
            self.viewer.layers.remove(old)
        layer = self.viewer.add_image(data, name=name + ' — Z corrected',
            scale=original.scale, translate=original.translate, rotate=original.rotate,
            shear=original.shear, affine=original.affine.affine_matrix,
            colormap=original.colormap, units=original.units)
        self.outputs[name] = layer
        self.records[name] = report
        original.visible = False
        self.viewer.layers.selection.active = layer
        self.status.setText(f'{name}: preview ready. Gain {min(report["gain"]):.3f}–{max(report["gain"]):.3f}.\nApply to use this correction in the next analysis.')

    def apply(self):
        name, original = self.selected()
        report = self.records.get(name)
        if report is None or report['options'] != self.options():
            raise ValueError('Parameters changed or no preview exists. Correct Z again first.')
        from .runtime import validate_roi_geometry
        validate_roi_geometry(original, self.preview.session.ns['VOXEL_SPACING_UM'], self.preview.volume.shape)
        cfg = self.launcher.get_config()
        if source_identity(self.launcher.image_path.text(), cfg) != self.preview.source:
            raise ValueError('Input settings changed. Reopen the preview before applying.')
        if cfg['CELLPOSE_ROI_NORMALIZATION'] is not None:
            raise ValueError('Reset the old ROI normalization limits first; they describe the uncorrected image.')
        opts = copy.deepcopy(report['options'])
        cfg['Z_CORRECTIONS'][name] = opts
        if opts['segmentation']:
            cfg['SEGMENTATION_CHANNEL'] = name
            for c in cfg['CHANNELS']:
                c['role'] = 'segmentation' if c['name'] == name else 'measurement'
        cfg['Z_CORRECTION_SOURCE'] = source_identity(self.launcher.image_path.text(), cfg)
        self.launcher.apply_config(cfg)
        # Primary channel can change here without changing the source file.
        self.preview.source = source_identity(self.preview.session.input_path, cfg)
        self.preview.volume = self.preview.session.ns['channel_volumes'][cfg['SEGMENTATION_CHANNEL']]
        self.preview.invalidate()
        self.status.setText(f'{name}: correction applied.\nModel channel: {cfg["SEGMENTATION_CHANNEL"]}.\nMeasurements for {name}: {"Z corrected" if opts["measurement"] else "original"}.\nCellpose normalization remains as configured. Review intensity thresholds when using corrected measurements.')

    def plot(self):
        name, _ = self.selected()
        if name not in self.records:
            raise ValueError('Correct the selected layer first.')
        import matplotlib.pyplot as plt
        r = self.records[name]
        fig, axes = plt.subplots(2, 1, sharex=True)
        axes[0].plot(r['observed_profile'], label='Positive-pixel percentile')
        axes[0].plot(r['smoothed_profile'], label='Smoothed reference')
        axes[0].set_ylabel('Intensity'); axes[0].legend()
        axes[1].plot(r['gain']); axes[1].set_ylabel('Gain'); axes[1].set_xlabel('Z plane (zero-based)')
        fig.suptitle(name + ' — Z correction'); fig.tight_layout(); plt.show(block=False)

    def save_roi(self):
        from pathlib import Path
        roi = self.preview.reviewed_roi()
        if not roi.any():
            raise ValueError('The reviewed ROI is empty.')
        path, _ = QFileDialog.getSaveFileName(self, 'Save reviewed ROI', 'mntb_roi.tif', 'TIFF (*.tif)')
        if not path:
            return
        if Path(path).resolve() == self.preview.session.input_path.resolve():
            raise ValueError('Choose a different file from the original TIFF.')
        self.preview.session.ns['calibrated_label_tiff'](Path(path), roi.astype(np.uint8),
                                                       self.preview.session.ns['VOXEL_SPACING_UM'])
        cfg = self.launcher.get_config(); cfg['MNTB_ROI_MASK_PATH'] = str(Path(path).resolve())
        self.launcher.apply_config(cfg)
        self.status.setText('Reviewed ROI saved and selected for the next analysis.')

    def reset(self):
        cfg = self.launcher.get_config(); cfg['Z_CORRECTIONS'] = {}; cfg['Z_CORRECTION_SOURCE'] = None
        self.launcher.apply_config(cfg)
        self.status.setText('Z correction disabled for analysis. Preview layers remain for comparison.')

    def cleanup(self):
        for layer in self.outputs.values():
            if layer in self.viewer.layers:
                self.viewer.layers.remove(layer)
