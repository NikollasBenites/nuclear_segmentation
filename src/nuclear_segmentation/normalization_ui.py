"""Pre-segmentation ROI review and normalization comparison in the existing viewer."""
import copy
import json
from pathlib import Path
import numpy as np
from qtpy.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QDoubleSpinBox, QFormLayout, QFileDialog, QTabWidget
from .normalization import compare_percentiles, normalize_preview, source_identity, mask_digest
from .runtime import validate_roi_geometry


class NormalizationPreview(QWidget):
    def __init__(self, launcher, session):
        super().__init__()
        self.launcher, self.session, self.viewer = launcher, session, launcher.viewer
        self.report = None
        self.applied = None
        self.layers = []
        self.comparisons = []
        self.connections = []
        ns = session.ns
        self.volume = ns['segmentation_volume']
        self.source = source_identity(session.input_path, session.config)
        outer = QVBoxLayout(self)
        roi_widget = QWidget()
        layout = QVBoxLayout(roi_widget)
        note = QLabel('Review the MNTB ROI across Z before calculating.\nAll ROI voxels count, including zeros; guards are not applied.\nComparison: global linear normalization, before model resizing.\nApplying ROI limits replaces custom normalization options.\nOutside voxels are transformed too; no clipping or masking.')
        note.setWordWrap(True); layout.addWidget(note)
        form = QFormLayout(); layout.addLayout(form)
        self.lower, self.upper = QDoubleSpinBox(), QDoubleSpinBox()
        options = session.config['CELLPOSE_NORMALIZE']
        percentiles = options.get('percentile') if isinstance(options, dict) else None
        percentiles = percentiles if isinstance(percentiles, (list, tuple)) and len(percentiles) == 2 else [1, 99]
        for widget, value, title in [(self.lower, percentiles[0], 'Lower percentile'), (self.upper, percentiles[1], 'Upper percentile')]:
            widget.setRange(0, 100); widget.setDecimals(3); widget.setValue(value)
            widget.valueChanged.connect(self.invalidate); form.addRow(title, widget)
        self.status = QLabel('ROI is provisional. Review it, then calculate.'); self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.calculate_button = QPushButton('Calculate and preview')
        self.calculate_button.clicked.connect(lambda:launcher.safe(self.calculate));layout.addWidget(self.calculate_button)
        self.apply_button = QPushButton('Save ROI and use MNTB limits')
        self.apply_button.setEnabled(False);self.apply_button.clicked.connect(lambda:launcher.safe(self.apply));layout.addWidget(self.apply_button)
        save = QPushButton('Save comparison report…');save.clicked.connect(lambda:launcher.safe(self.save_report));layout.addWidget(save)
        reset = QPushButton('Reset normalization to Cellpose default')
        reset.clicked.connect(lambda:launcher.safe(self.reset));layout.addWidget(reset)
        self.original_layers = {}
        for c in ns['CHANNELS']:
            layer = self.viewer.add_image(ns['channel_volumes'][c['name']], name=f"Original: {c['name']}",
                         scale=ns['VOXEL_SPACING_UM'], colormap=c.get('colormap','gray'),
                         visible=c['name']==ns['SEGMENTATION_CHANNEL'])
            layer.units = ('um',)*3;self.layers.append(layer)
            self.original_layers[c['name']] = layer
        self.roi = self.viewer.add_labels(ns['mntb_roi'].astype(np.uint8), name='MNTB ROI - normalization review',
                         scale=ns['VOXEL_SPACING_UM'], opacity=0.25)
        self.roi.units=('um',)*3;self.layers.append(self.roi)
        for name in ('data','set_data','paint','scale','translate','rotate','shear','affine'):
            emitter = getattr(self.roi.events, name, None)
            if emitter is not None:
                emitter.connect(self.invalidate); self.connections.append(emitter)
        self.viewer.dims.axis_labels = ('Z','Y','X')
        self.viewer.canvas.overlays.scale_bar.visible=True
        from .z_correction_ui import ZCorrectionPanel
        self.z_panel = ZCorrectionPanel(self)
        tabs = QTabWidget()
        tabs.addTab(self.z_panel, 'Z correction')
        tabs.addTab(roi_widget, 'ROI percentile normalization')
        outer.addWidget(tabs)
        self.viewer.window.add_dock_widget(self, name='Image correction before segmentation', area='right')

    def invalidate(self, *args):
        self.report = None
        self.apply_button.setEnabled(False)
        self.status.setText('ROI or percentiles changed. Calculate again before applying or starting with ROI limits.')
        for layer in self.comparisons:
            layer.visible = False

    def reviewed_roi(self):
        if self.roi not in self.viewer.layers:
            raise ValueError('The normalization ROI was removed. Reopen the preview.')
        validate_roi_geometry(self.roi, self.session.ns['VOXEL_SPACING_UM'], self.volume.shape)
        return np.asarray(self.roi.data) > 0

    def calculate(self):
        if self.launcher.get_config()['Z_CORRECTIONS']:
            raise ValueError('ROI percentile preview currently uses original data. Reset Z corrections to use fixed ROI limits, or keep Z correction with native Cellpose normalization.')
        roi = self.reviewed_roi()
        self.report = None;self.apply_button.setEnabled(False)
        report = compare_percentiles(self.volume, roi, self.lower.value(), self.upper.value())
        for layer in self.comparisons:
            if layer in self.viewer.layers:self.viewer.layers.remove(layer)
        self.comparisons=[]
        for label, limits in [('Full stack - exact percentile reference', report['full_lowhigh']),
                              ('MNTB - ROI percentile preview', report['roi_lowhigh'])]:
            layer = self.viewer.add_image(normalize_preview(self.volume, limits), name=label,
                         scale=self.session.ns['VOXEL_SPACING_UM'], colormap='gray', contrast_limits=(0,1),
                         visible=label.startswith('MNTB'))
            layer.units=('um',)*3;self.comparisons.append(layer)
        self.roi.visible=False
        self.viewer.layers.selection.active=self.roi
        report['source']=self.source
        report['voxel_spacing_zyx_um']=list(self.session.ns['VOXEL_SPACING_UM'])
        report['cellpose_normalize']={'normalize':True,'lowhigh':report['roi_lowhigh'],'norm3D':True,'invert':False}
        self.report=report;self.apply_button.setEnabled(True)
        fl,fh=report['full_lowhigh'];rl,rh=report['roi_lowhigh']
        full_span=fh-fl
        gain=(f'{full_span/(rh-rl):.4g}×' if full_span>1e-3 else 'undefined (flat full-stack limits)')
        self.status.setText(f"Percentiles: {report['percentiles']}\nFull stack: {fl:.6g} → {fh:.6g}\nMNTB only: {rl:.6g} → {rh:.6g}\nROI voxels: {report['roi_voxels']:,}\nOutside ROI: {100*report['outside_fraction']:.2f}%\nZeros inside ROI: {100*report['roi_zero_fraction']:.2f}%\nROI/full normalization slope: {gain}\nToggle the preview layers with the eye icons.\nFull-stack reference uses exact percentiles; Cellpose may subsample large stacks.")
        self.launcher.log.appendPlainText(self.status.text())

    def checked_report(self):
        roi=self.reviewed_roi()
        if self.report is None or mask_digest(roi)!=self.report['roi_sha256'] or self.report['percentiles'] != [self.lower.value(),self.upper.value()]:
            raise ValueError('Recalculate after editing the ROI or percentiles.')
        cfg=self.launcher.get_config()
        if source_identity(self.launcher.image_path.text(),cfg)!=self.source:
            raise ValueError('The selected image or channel changed. Reopen the preview.')
        return copy.deepcopy(self.report),roi,cfg

    def apply(self):
        report,roi,cfg=self.checked_report()
        path,_=QFileDialog.getSaveFileName(self,'Save normalization ROI','mntb_normalization_roi.tif','TIFF (*.tif)')
        if not path:return
        # Prevent replacing the raw input with a mask.
        if Path(path).resolve()==self.session.input_path.resolve():
            raise ValueError('Choose a different file; the original image must be preserved.')
        self.session.ns['calibrated_label_tiff'](Path(path),roi.astype(np.uint8),self.session.ns['VOXEL_SPACING_UM'])
        report['roi_path']=str(Path(path).resolve())
        cfg['CELLPOSE_NORMALIZE']=report['cellpose_normalize']
        cfg['CELLPOSE_ROI_NORMALIZATION']=report
        cfg['MNTB_ROI_MASK_PATH']=report['roi_path']
        self.launcher.apply_config(cfg)
        self.applied=copy.deepcopy(report)
        self.status.setText(self.status.text()+'\nApplied. Start analysis will use these fixed limits and the saved ROI.')

    def ensure_current(self):
        cfg=self.launcher.get_config()
        if self.applied is not None and cfg['CELLPOSE_ROI_NORMALIZATION']==self.applied:
            self.checked_report()
            if self.report['roi_sha256']!=self.applied['roi_sha256'] or self.report['roi_lowhigh']!=self.applied['roi_lowhigh']:
                raise ValueError('The preview changed after application. Save and apply the updated limits first.')

    def reset(self):
        cfg=self.launcher.get_config();cfg['CELLPOSE_NORMALIZE']=True;cfg['CELLPOSE_ROI_NORMALIZATION']=None
        self.launcher.apply_config(cfg);self.applied=None
        self.status.setText('Cellpose default normalization selected. ROI mask path is retained.')

    def save_report(self):
        report,_,_=self.checked_report()
        path,_=QFileDialog.getSaveFileName(self,'Save percentile comparison','normalization_comparison.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(report,indent=2),encoding='utf-8')

    def close_preview(self):
        self.z_panel.cleanup()
        for emitter in self.connections:emitter.disconnect(self.invalidate)
        self.viewer.window.remove_dock_widget(self)
        for layer in self.layers+self.comparisons:
            if layer in self.viewer.layers:self.viewer.layers.remove(layer)
        self.deleteLater()
