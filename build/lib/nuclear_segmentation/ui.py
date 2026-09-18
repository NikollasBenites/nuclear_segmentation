from pathlib import Path
import copy
import json
import traceback
from qtpy.QtCore import QObject, QThread, Signal, Slot, QEvent
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QLineEdit,
    QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QLabel,
    QPlainTextEdit, QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QScrollArea, QProgressBar,
)
from .config import defaults, validate_config
from .runtime import AnalysisSession


class Worker(QObject):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, config, image, previous):
        super().__init__()
        self.arguments = (config, image, previous)

    @Slot()
    def run(self):
        try:
            session = AnalysisSession(*self.arguments, log=self.progress.emit)
            self.completed.emit(session.prepare())
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            self.finished.emit()


def double(value, minimum=0, maximum=1e9, decimals=4):
    w = QDoubleSpinBox()
    w.setRange(minimum, maximum)
    w.setDecimals(decimals)
    w.setValue(value)
    return w


class DataTable(QWidget):
    """Editable channel and marker tables; blank optional numbers mean automatic."""
    def __init__(self, columns):
        super().__init__()
        self.columns = columns
        self.records = []
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels([c[1] for c in columns])
        layout.addWidget(self.table)
        bar = QHBoxLayout()
        add = QPushButton('Add row'); remove = QPushButton('Remove selected row')
        add.clicked.connect(lambda: self.set_records(self.get_records() + [{}]))
        remove.clicked.connect(self.remove)
        bar.addWidget(add); bar.addWidget(remove); layout.addLayout(bar)

    def remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.records.pop(row)

    def set_records(self, records):
        self.records = copy.deepcopy(records)
        self.table.setRowCount(len(records))
        for row, rec in enumerate(records):
            for col, (key, title, kind) in enumerate(self.columns):
                value = rec.get(key)
                if kind is bool:
                    w = QCheckBox(); w.setChecked(bool(value)); self.table.setCellWidget(row, col, w)
                else:
                    self.table.setItem(row, col, QTableWidgetItem('' if value is None else str(value)))
        self.table.resizeColumnsToContents()

    def get_records(self):
        result = []
        for row in range(self.table.rowCount()):
            rec = copy.deepcopy(self.records[row])
            for col, (key, title, kind) in enumerate(self.columns):
                if kind is bool:
                    rec[key] = self.table.cellWidget(row, col).isChecked()
                else:
                    item = self.table.item(row, col)
                    value = item.text().strip() if item else ''
                    rec[key] = (kind(value) if value else None)
            result.append(rec)
        return result


class Launcher(QWidget):
    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.session = None
        self.running = False
        self.config = defaults()
        layout = QVBoxLayout(self)
        title = QLabel('Nuclear Segmentation')
        title.setStyleSheet('font-size: 19px; font-weight: bold;')
        layout.addWidget(title)
        description = QLabel('Open a TIFF, review your channels, then start.\nReview and accept the ROI before exporting density.')
        description.setWordWrap(True); layout.addWidget(description)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs)
        files = QWidget(); form = QFormLayout(files)
        self.mode = QComboBox()
        for title, mode in [('New segmentation', 'segment'), ('Resume analysis / adjust filters', 'resume'), ('Previous analysis: density only', 'density_only')]:
            self.mode.addItem(title, mode)
        form.addRow('Workflow', self.mode)
        self.image_path = self.path_row(form, 'Original cropped TIFF', False)
        self.previous_path = self.path_row(form, 'Previous analysis folder', True)
        self.roi_path = self.path_row(form, 'ROI mask (optional)', False)
        note = QLabel('Resume/density-only restore saved channels and filters.\nAn empty ROI path selects the latest saved ROI in the previous folder or its density addenda. Otherwise it reconstructs an estimated ROI.')
        note.setWordWrap(True); form.addRow(note)
        self.tabs.addTab(files, 'Files')
        parameters = QWidget(); params = QFormLayout(parameters)
        self.nominal = double(60, .001, 10000); params.addRow('Nominal thickness (µm)', self.nominal)
        self.roi_mode = QComboBox();self.roi_mode.addItems(['per_slice', 'constant_xy']);params.addRow('ROI reconstruction',self.roi_mode)
        self.axes = QLineEdit();self.axes.setPlaceholderText('Auto from TIFF metadata');params.addRow('Axes override',self.axes)
        self.spacing = QLineEdit();self.spacing.setPlaceholderText('Auto, or Z,Y,X in µm');params.addRow('Voxel spacing override', self.spacing)
        self.model = QLineEdit();params.addRow('Cellpose model / local path',self.model)
        self.prob = double(-1, -100, 100);params.addRow('cellprob_threshold', self.prob)
        self.min_size = QSpinBox();self.min_size.setRange(0,100000000);params.addRow('Cellpose min_size (voxels)',self.min_size)
        self.batch = QSpinBox();self.batch.setRange(1,1024);params.addRow('Batch size',self.batch)
        self.smooth = QLineEdit();params.addRow('flow3D_smooth (number or [Z,Y,X])',self.smooth)
        self.device = QComboBox();self.device.addItems(['auto','cpu']);params.addRow('Processing device',self.device)
        self.cache = QCheckBox('Use optional OME-Zarr cache');params.addRow(self.cache)
        self.sample = QLineEdit();self.sample.setPlaceholderText('Use TIFF filename');params.addRow('Export sample name',self.sample)
        explanation = QLabel('3D segmentation uses calibrated anisotropy.\nAuto device: NVIDIA CUDA → Apple MPS → CPU.\nThickness correction uses the live first/last tissue Z, before guards.')
        explanation.setWordWrap(True);params.addRow(explanation)
        self.tabs.addTab(parameters,'Parameters')
        self.channels = DataTable([
            ('name','Name',str),('index','Index (0-based)',int),('measure','Measure',bool),
            ('filter_enabled','Intensity filter',bool),('filter_min','Minimum',float),
            ('filter_max','Maximum',float),('colormap','Colormap',str)])
        self.tabs.addTab(self.channels,'Channels')
        self.markers = DataTable([
            ('name','Channel name',str),('enabled','Enabled',bool),('ring_inner_um','Inner µm',float),
            ('ring_outer_um','Outer µm',float),('pixel_threshold','Threshold (blank=Otsu)',float),
            ('initial_min_positive_fraction','Min positive fraction',float)])
        self.tabs.addTab(self.markers,'Perinuclear markers')
        self.log = QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(1500)
        self.tabs.addTab(self.log,'Activity')
        presetbar = QHBoxLayout()
        for label, callback in [('Load preset',self.load_preset),('Save preset',self.save_preset),('Advanced settings',self.advanced)]:
            b=QPushButton(label);b.clicked.connect(lambda checked=False, fn=callback:self.safe(fn));presetbar.addWidget(b)
        layout.addLayout(presetbar)
        self.start = QPushButton('Start analysis');self.start.clicked.connect(lambda:self.safe(self.start_analysis));layout.addWidget(self.start)
        self.progress = QProgressBar();self.progress.setRange(0,1);self.progress.setValue(0);layout.addWidget(self.progress)
        bar=QHBoxLayout()
        self.export_button=QPushButton('Export results');self.qc_button=QPushButton('QC plots');self.roi_button=QPushButton('Save ROI…')
        for button,fn in [(self.export_button,self.export),(self.qc_button,self.qc),(self.roi_button,self.save_roi)]:
            button.setEnabled(False);button.clicked.connect(lambda checked=False,f=fn:self.safe(f));bar.addWidget(button)
        layout.addLayout(bar)
        self.apply_config(self.config)
        self.setMinimumWidth(470)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Close and self.running:
            QMessageBox.information(self, 'Analysis in progress', 'Wait for the current processing stage to finish before closing the application.')
            event.ignore()
            return True
        return super().eventFilter(obj,event)

    def path_row(self, form, label, directory):
        row=QWidget();bar=QHBoxLayout(row);bar.setContentsMargins(0,0,0,0)
        edit=QLineEdit();button=QPushButton('Browse…');bar.addWidget(edit);bar.addWidget(button)
        def browse():
            if directory:
                value=QFileDialog.getExistingDirectory(self,label)
            else:
                value,_=QFileDialog.getOpenFileName(self,label,'','TIFF (*.tif *.tiff);;All files (*)')
            if value:edit.setText(value)
        button.clicked.connect(browse);form.addRow(label,row);return edit

    def safe(self, fn):
        try:return fn()
        except Exception as exc:
            self.log.appendPlainText(traceback.format_exc())
            QMessageBox.critical(self,'Nuclear Segmentation',str(exc))

    def apply_config(self, config):
        self.config=validate_config(config)
        c=self.config
        self.mode.setCurrentIndex(self.mode.findData(c['RUN_MODE']))
        self.nominal.setValue(c['NOMINAL_SECTION_THICKNESS_UM']);self.roi_mode.setCurrentText(c['MNTB_ROI_MODE'])
        self.roi_path.setText(str(c['MNTB_ROI_MASK_PATH'] or ''))
        self.axes.setText(c['AXES_OVERRIDE'] or '')
        self.spacing.setText(','.join(map(str,c['VOXEL_SPACING_OVERRIDE_UM'])) if c['VOXEL_SPACING_OVERRIDE_UM'] else '')
        self.model.setText(c['MODEL_NAME']);self.prob.setValue(c['CELLPROB_THRESHOLD'])
        self.min_size.setValue(c['CELLPOSE_MIN_SIZE_VOXELS']);self.batch.setValue(c['BATCH_SIZE'])
        self.smooth.setText(json.dumps(c['FLOW3D_SMOOTH']));self.device.setCurrentText(c['PROCESSING_DEVICE'])
        self.cache.setChecked(c['USE_OME_ZARR_CACHE']);self.sample.setText(c['CUSTOM_SAMPLE_NAME'] or '')
        self.channels.set_records(c['CHANNELS']);self.markers.set_records(c['PERINUCLEAR_MARKERS'])

    def get_config(self):
        c=copy.deepcopy(self.config)
        c.update(RUN_MODE=self.mode.currentData(),NOMINAL_SECTION_THICKNESS_UM=self.nominal.value(),
                 MNTB_ROI_MODE=self.roi_mode.currentText(),MNTB_ROI_MASK_PATH=self.roi_path.text().strip() or None,
                 AXES_OVERRIDE=self.axes.text().strip() or None,
                 VOXEL_SPACING_OVERRIDE_UM=[float(x) for x in self.spacing.text().split(',')] if self.spacing.text().strip() else None,
                 MODEL_NAME=self.model.text().strip(),CELLPROB_THRESHOLD=self.prob.value(),
                 CELLPOSE_MIN_SIZE_VOXELS=self.min_size.value(),BATCH_SIZE=self.batch.value(),
                 FLOW3D_SMOOTH=json.loads(self.smooth.text()),PROCESSING_DEVICE=self.device.currentText(),
                 USE_OME_ZARR_CACHE=self.cache.isChecked(),CUSTOM_SAMPLE_NAME=self.sample.text().strip() or None,
                 CHANNELS=self.channels.get_records(),PERINUCLEAR_MARKERS=self.markers.get_records())
        for channel in c['CHANNELS']:
            channel['role'] = ('segmentation' if channel['name'] == c['SEGMENTATION_CHANNEL'] else channel.get('role', 'measurement'))
            channel['colormap'] = channel.get('colormap') or 'gray'
        return validate_config(c)

    def load_preset(self):
        path,_=QFileDialog.getOpenFileName(self,'Load app preset','','JSON (*.json)')
        if path:self.apply_config(json.loads(Path(path).read_text(encoding='utf-8')))

    def save_preset(self):
        config=self.get_config()
        path,_=QFileDialog.getSaveFileName(self,'Save app preset','nuclear_segmentation_preset.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(config,indent=2),encoding='utf-8')

    def advanced(self):
        from qtpy.QtWidgets import QDialog,QDialogButtonBox
        dialog=QDialog(self);dialog.setWindowTitle('Advanced configuration — JSON values');dialog.resize(760,650)
        layout=QVBoxLayout(dialog);editor=QPlainTextEdit(json.dumps(self.get_config(),indent=2));layout.addWidget(editor)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec():self.apply_config(json.loads(editor.toPlainText()))

    def start_analysis(self):
        if self.running:return
        config=self.get_config();image=Path(self.image_path.text().strip())
        previous=self.previous_path.text().strip() or None
        if not image.is_file():raise ValueError('Choose the original cropped TIFF.')
        if config['RUN_MODE']!='segment' and (not previous or not (Path(previous)/'analysis_config.json').is_file()):
            raise ValueError('Choose the original exported analysis folder containing analysis_config.json.')
        if self.session:
            if QMessageBox.question(self,'Replace current view?','Save any ROI edits first. Replace the current analysis view?') != QMessageBox.StandardButton.Yes:return
            ns=self.session.ns
            for key in ('filter_dock_widget','retrospective_panel'):
                if key in ns:
                    widget=ns[key];self.viewer.window.remove_dock_widget(getattr(widget,'native',widget))
            self.viewer.layers.clear();self.session=None
        self.running=True;self.start.setEnabled(False)
        for b in (self.export_button,self.qc_button,self.roi_button):b.setEnabled(False)
        self.tabs.setCurrentWidget(self.log);self.progress.setRange(0,0)
        self.thread=QThread(self);self.worker=Worker(config,image,previous);self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run);self.worker.progress.connect(self.log.appendPlainText)
        self.worker.completed.connect(self.analysis_ready);self.worker.failed.connect(self.analysis_failed)
        self.worker.finished.connect(self.thread.quit);self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.processing_finished);self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    @Slot(object)
    def analysis_ready(self, session):
        try:
            session.log=self.log.appendPlainText
            session.attach(self.viewer);self.session=session
            self.export_button.setEnabled(True);self.roi_button.setEnabled(True)
            self.qc_button.setEnabled(session.ns['RUN_MODE']!='density_only')
            self.log.appendPlainText('Ready. Review ROI across Z, accept it, then export.')
        except Exception:
            self.analysis_failed(traceback.format_exc())

    @Slot(str)
    def analysis_failed(self, message):
        self.log.appendPlainText(message)
        QMessageBox.critical(self,'Analysis failed',message.splitlines()[-1]+'\nSee Activity for details.')

    @Slot()
    def processing_finished(self):
        self.running=False;self.start.setEnabled(True);self.progress.setRange(0,1);self.progress.setValue(1)

    def export(self):
        if self.session:self.session.export()

    def qc(self):
        if self.session:self.session.show_qc()

    def save_roi(self):
        path,_=QFileDialog.getSaveFileName(self,'Save reviewed ROI','mntb_roi.tif','TIFF (*.tif)')
        if path and self.session:self.session.save_roi(Path(path))
