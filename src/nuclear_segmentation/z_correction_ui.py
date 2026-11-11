"""Correction of a selected image layer before starting segmentation."""
import copy
import numpy as np
from qtpy.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel, QPushButton, QDoubleSpinBox, QCheckBox, QFileDialog, QDialog, QListWidget, QAbstractItemView, QDialogButtonBox
from .z_correction import correct_z, DEFAULTS
from .normalization import source_identity


def intensity_profiles(original, corrected=None, include_zeros=True):
    """P99 and mean per Z; positive-only comparisons use original-positive pixels."""
    original = np.asarray(original)
    corrected = None if corrected is None else np.asarray(corrected)
    if original.ndim != 3 or (corrected is not None and corrected.shape != original.shape):
        raise ValueError('Profile images must share the same 3D ZYX grid.')
    profiles = {}
    for title, volume in [('Original', original), ('Z corrected', corrected)]:
        if volume is None:
            continue
        p99 = np.full(original.shape[0], np.nan)
        mean = np.full(original.shape[0], np.nan)
        for z, plane in enumerate(volume):
            valid = np.isfinite(original[z])
            if corrected is not None:
                valid &= np.isfinite(corrected[z])
            if not include_zeros:
                valid &= original[z] > 0
            values = plane[valid]
            if values.size:
                p99[z] = np.percentile(values, 99)
                mean[z] = np.mean(values, dtype=np.float64)
        profiles[title] = {'p99': p99, 'mean': mean}
    return profiles


class ZCorrectionPanel(QWidget):
    def __init__(self, preview):
        super().__init__()
        self.preview = preview
        self.launcher = preview.launcher
        self.viewer = preview.viewer
        self.outputs = {}
        self.records = {}
        self.profile_dialogs = []
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
        self.profile_include_zeros = QCheckBox('Intensity profiles: include zero pixels')
        self.profile_include_zeros.setChecked(True)
        self.profile_include_zeros.setToolTip('Checked: all finite pixels. Unchecked: original-positive pixels, at identical locations before and after correction. Applies to both P99 and mean; does not change correction settings.')
        layout.addWidget(self.profile_include_zeros)
        button = QPushButton('Compare P99 and mean across Z')
        button.clicked.connect(lambda checked=False: self.launcher.safe(self.plot_intensity_profiles))
        layout.addWidget(button)
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
        path, selected_filter = QFileDialog.getSaveFileName(
            self, 'Save multichannel image data', 'selected_channels.tif',
            'ImageJ TIFF - float32 (*.tif *.tiff);;'
            'ImageJ TIFF - uint16 rescaled per channel (0 to volume max) (*.tif *.tiff);;'
            'OME-TIFF - float32 (*.ome.tif *.ome.tiff);;'
            'OME-TIFF - uint16 rescaled per channel (0 to volume max) (*.ome.tif *.ome.tiff)')
        if not path:
            return
        saved = export_layers(path, layers, self.preview.session.ns['VOXEL_SPACING_UM'], records,
                              [self.preview.session.input_path, self.launcher.get_config().get('MNTB_ROI_MASK_PATH')],
                              file_format='ome' if selected_filter.startswith('OME-TIFF') else 'imagej',
                              pixel_type='uint16' if 'uint16' in selected_filter else 'float32')
        note = ('uint16: each channel rescaled from 0 to its whole-volume maximum; '
                'conversion recorded in JSON. ' if 'uint16' in selected_filter else 'float32. ')
        self.status.setText('Saved ' + note + str(saved))

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

    def plot_intensity_profiles(self):
        name, original = self.selected()
        output = self.outputs.get(name)
        if output is not None and output not in self.viewer.layers:
            output = None
        if output is not None:
            same_grid = (np.allclose(original.scale, output.scale)
                         and np.allclose(original.affine.affine_matrix, output.affine.affine_matrix)
                         and np.allclose(original.translate, output.translate)
                         and np.allclose(original.rotate, output.rotate)
                         and np.allclose(original.shear, output.shear))
            if not same_grid:
                raise ValueError('Original and corrected layers have different transforms. Recreate the Z correction before comparing.')
        include = self.profile_include_zeros.isChecked()
        profiles = intensity_profiles(original.data, None if output is None else output.data, include)
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
        fig = Figure(figsize=(9, 6)); axes = fig.subplots(2, 1, sharex=True)
        for title, values in profiles.items():
            color = '#0072B2' if title == 'Original' else '#D55E00'
            for ax, metric in zip(axes, ['p99', 'mean']):
                ax.plot(np.arange(len(values[metric])), values[metric], label=title, color=color)
        for ax, label in zip(axes, ['P99 intensity', 'Mean intensity']):
            ax.set_ylabel(label); ax.legend(); ax.grid(alpha=.25)
        axes[1].set_xlabel('Z plane (zero-based)')
        scope = 'All pixels, including zeros' if include else 'Original-positive pixels only'
        fig.suptitle(name + ' — intensity profiles\n' + scope); fig.tight_layout()
        dialog = QDialog(self); dialog.setWindowTitle(name + ' — P99 and mean across Z')
        box = QVBoxLayout(dialog); canvas = FigureCanvasQTAgg(fig)
        box.addWidget(NavigationToolbar2QT(canvas, dialog)); box.addWidget(canvas)
        note = ('Per-plane statistics of actual image values; nonfinite pixels excluded. '
                'No ROI mask or display brightness adjustment is used. '
                'P99 is always percentile 99, independent of the correction reference percentile. '
                'This plot is a snapshot; reopen after changing a layer or the zero-pixel option.')
        if output is None:
            note += ' Only the original is shown. Correct Z first to compare both curves.'
        elif name in self.records and self.records[name]['options'] != self.options():
            note += ' Controls changed: the corrected curve is the existing layer. Correct Z again to apply new settings.'
        label = QLabel(note); label.setWordWrap(True); box.addWidget(label)
        dialog.resize(900, 720); self.profile_dialogs.append(dialog)
        dialog.finished.connect(lambda *_: self.profile_dialogs.remove(dialog) if dialog in self.profile_dialogs else None)
        dialog.show()

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
