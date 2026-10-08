"""Menu-driven smoothing preview for the application's tissue ROI layers."""
import numpy as np
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QDoubleSpinBox, QLabel, QDialogButtonBox, QMessageBox
from .roi_smoothing import smooth_roi

ROI_NAMES = ('MNTB ROI - review before density', 'MNTB ROI - normalization review')


class RoiSmoothingDialog(QDialog):
    def __init__(self, viewer, source, controller):
        super().__init__(viewer.window._qt_window)
        self.setWindowTitle('Smooth MNTB ROI')
        self.setModal(False)
        self.viewer, self.source, self.controller = viewer, source, controller
        self.original = np.asarray(source.data).copy()
        self.original_visible = source.visible
        self.preview = None
        self.result = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Gaussian smoothing of the tissue ROI. Sigma is in pixels / Z planes.\nReview the preview across Z before applying.'))
        form = QFormLayout()
        self.xy = QDoubleSpinBox(); self.xy.setRange(0, 50); self.xy.setSingleStep(0.5); self.xy.setValue(2)
        self.z = QDoubleSpinBox(); self.z.setRange(0, 50); self.z.setSingleStep(0.5); self.z.setValue(0)
        self.z.setEnabled(self.original.ndim == 3)
        self.xy.setToolTip('Standard deviation in X and Y pixels; larger values smooth more strongly.')
        self.z.setToolTip('Zero smooths each XY plane independently. Positive values also smooth across Z.')
        form.addRow('XY sigma', self.xy); form.addRow('Z sigma', self.z)
        layout.addLayout(form)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.Ok); self.apply_button.setText('Apply')
        buttons.accepted.connect(self.apply); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(300)
        self.timer.timeout.connect(self.update_preview)
        self.xy.valueChanged.connect(self.schedule); self.z.valueChanged.connect(self.schedule)
        self.finished.connect(self.cleanup)
        self.update_preview()

    def schedule(self, *_):
        self.apply_button.setEnabled(False)
        self.timer.start()

    def update_preview(self):
        try:
            self.result = smooth_roi(self.original, self.xy.value(), self.z.value())
            if self.preview is None or self.preview not in self.viewer.layers:
                self.preview = self.viewer.add_labels(self.result, name='MNTB ROI - smoothing preview',
                    scale=self.source.scale, translate=self.source.translate, rotate=self.source.rotate,
                    shear=self.source.shear, affine=self.source.affine, opacity=self.source.opacity)
                self.preview.editable = False
            else:
                self.preview.data = self.result
            self.source.visible = False
            old = np.count_nonzero(self.original); new = np.count_nonzero(self.result)
            self.status.setText(f'ROI voxels: {old:,} → {new:,} ({100 * (new / old - 1):+.2f}% volume).\nAfter applying, accept the reviewed ROI again before exporting density.')
            self.apply_button.setEnabled(True)
        except Exception as exc:
            self.result = None
            self.status.setText(str(exc)); self.apply_button.setEnabled(False)

    def apply(self):
        if self.source not in self.viewer.layers or not np.array_equal(self.source.data, self.original):
            QMessageBox.warning(self, 'ROI changed', 'The source ROI changed or was removed. Cancel and reopen smoothing.')
            return
        if self.result is None or self.timer.isActive():
            return
        self.controller.backup = (self.source, self.original, self.result.copy())
        # Assignment emits the existing density invalidation event.
        self.source.data = self.result.copy()
        self.accept()

    def cleanup(self, *_):
        self.timer.stop()
        if self.preview is not None and self.preview in self.viewer.layers:
            self.viewer.layers.remove(self.preview)
        if self.source in self.viewer.layers:
            self.source.visible = self.original_visible
            self.viewer.layers.selection.active = self.source


class RoiSmoothingMenu:
    def __init__(self, viewer, menu):
        self.viewer = viewer
        self.dialog = None
        self.backup = None
        self.smooth_action = menu.addAction('Smooth ROI…')
        self.undo_action = menu.addAction('Undo last ROI smoothing')
        self.smooth_action.triggered.connect(self.open)
        self.undo_action.triggered.connect(self.undo)
        menu.aboutToShow.connect(self.refresh)
        self.refresh()

    def target(self):
        active = self.viewer.layers.selection.active
        if active is not None and active.name in ROI_NAMES:
            return active
        for name in ROI_NAMES:
            if name in self.viewer.layers:
                return self.viewer.layers[name]
        return None

    def refresh(self):
        self.smooth_action.setEnabled(self.target() is not None)
        self.undo_action.setEnabled(self.backup is not None)

    def open(self, *_):
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.raise_(); self.dialog.activateWindow(); return
        source = self.target()
        if source is not None:
            self.dialog = RoiSmoothingDialog(self.viewer, source, self)
            self.dialog.show()

    def undo(self, *_):
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.reject()
        if self.backup is None:
            return
        source, original, applied = self.backup
        if source not in self.viewer.layers or not np.array_equal(source.data, applied):
            QMessageBox.warning(self.viewer.window._qt_window, 'Cannot undo smoothing',
                'The ROI was edited or removed after smoothing. Undo was skipped to protect later changes.')
            return
        source.data = original.copy()
        self.backup = None
        self.refresh()
