"""Session orchestrator. Bundled Python stages preserve v5.5 analysis behavior.

Stages run in one isolated namespace per analysis. No notebook, IPython or
kernel is read or executed. Heavy stages use a background worker; Qt stages
must be invoked on the main thread by the application.
"""
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from .config import validate_config, find_saved_roi


def validate_roi_geometry(layer, spacing, shape):
    if tuple(layer.data.shape) != tuple(shape):
        raise ValueError('ROI shape differs from the original image grid.')
    if (not np.allclose(layer.scale, spacing) or not np.allclose(layer.translate, 0)
            or not np.allclose(layer.rotate, np.eye(3))
            or not np.allclose(layer.shear, 0)
            or not np.allclose(layer.affine.affine_matrix, np.eye(4))):
        raise ValueError('ROI transform changed. Restore its transform; edit voxels with the brush instead.')


class LayerCollector:
    """Collect optional shell layer arrays without touching Qt from a worker."""
    def __init__(self):
        self.layers = {}

    def add_labels(self, data, **kwargs):
        self.layers[kwargs['name']] = (data, kwargs)


class AnalysisSession:
    def __init__(self, config, input_path, previous_folder=None, log=print):
        self.config = validate_config(config)
        self.input_path = Path(input_path)
        self.previous_folder = Path(previous_folder) if previous_folder else None
        self.log = log
        self.ns = {}
        self.collector = LayerCollector()

    def stage(self, name):
        source = files('nuclear_segmentation').joinpath('stages', name + '.py').read_text()
        exec(compile(source, 'nuclear_segmentation/stages/' + name + '.py', 'exec'), self.ns)

    def prepare(self, preview_only=False):
        """Heavy operations, no GUI calls. Safe to run in a worker."""
        import pandas as pd
        import tifffile
        from datetime import datetime
        from time import perf_counter
        import json
        import textwrap
        import re
        self.log('Loading analysis functions…')
        source = files('nuclear_segmentation').joinpath('core.py').read_text()
        exec(compile(source, 'nuclear_segmentation/core.py', 'exec'), self.ns)
        self.ns.update(self.config)
        self.ns.update(session_input_path=self.input_path, session_previous_folder=self.previous_folder,
                       datetime=datetime, textwrap=textwrap, validate_roi_geometry=validate_roi_geometry,
                       display=lambda *_: None, viewer=self.collector,
                       print=lambda *args, **kw: self.log(' '.join(str(x) for x in args)))
        for key in ['OME_ZARR_CACHE_DIRECTORY', 'MNTB_ROI_MASK_PATH']:
            if self.ns.get(key):
                self.ns[key] = Path(self.ns[key])
        self.stage('restore')
        # Preserve settings chosen explicitly in the app; restore loaded channel/filter
        # settings still take precedence in resume and density_only as in the notebook.
        if not self.ns['MNTB_ROI_MASK_PATH'] and self.previous_folder:
            candidate = find_saved_roi(self.previous_folder)
            if candidate:
                self.ns['MNTB_ROI_MASK_PATH'] = candidate
                self.log('Automatically selected reviewed ROI: ' + str(candidate))
        self.log('Reading TIFF and spatial calibration…')
        self.stage('load_image')
        self.log('Loading or reconstructing ROI…')
        self.stage('roi')
        if preview_only:
            self.log('Image and ROI ready for normalization review. Cellpose has not run.')
            return self
        if self.ns['RUN_MODE'] == 'segment':
            from .normalization import validate_normalization_source
            validate_normalization_source(self.config, self.input_path, self.ns['mntb_roi'])
        if self.ns['RUN_MODE'] == 'density_only':
            self.stage('density_load')
            self.log('Saved retained nuclei loaded; no measurements rerun.')
            return self
        from .z_correction import prepare_volumes
        if self.ns['RUN_MODE'] == 'segment' and self.ns.get('Z_CORRECTION_SOURCE'):
            from .normalization import source_identity
            if source_identity(self.input_path, self.config) != self.ns['Z_CORRECTION_SOURCE']:
                raise ValueError('Z correction belongs to a different image/channel. Reopen the preview and apply again.')
        prepare_volumes(self.ns)
        if self.ns['RUN_MODE'] == 'segment':
            self.log('Loading PyTorch and Cellpose…')
            try:
                import torch
                from cellpose import io, models
            except ImportError as exc:
                raise RuntimeError('New segmentation needs Cellpose and PyTorch. Install the segmentation extra or use your working cellposesam environment.') from exc
            self.ns.update(torch=torch, io=io, models=models)
            self.stage('segment')
        else:
            self.log('Loading existing raw masks (Cellpose will not run)…')
            start = perf_counter()
            masks = tifffile.imread(self.previous_folder / 'cellpose_raw_masks.tif')
            if masks.ndim != 3 or masks.shape != self.ns['segmentation_volume'].shape:
                raise ValueError('Raw masks do not match original image ZYX shape.')
            self.ns.update(masks=masks, raw_3d_probs=None, DEVICE=SimpleNamespace(type='cpu'),
                           CELLPOSE_MODEL_LOAD_SECONDS=None, CELLPOSE_EVAL_SECONDS=None)
            self.log(f'Masks loaded in {perf_counter()-start:.2f} s; remeasuring original image.')
        self.log('Measuring nuclei morphology and intensities…')
        self.stage('measure')
        self.log('Measuring perinuclear markers…')
        self.stage('shells')
        # Release inference-only arrays/model before visualization where possible.
        for key in ('model', 'flows', 'styles'):
            self.ns.pop(key, None)
        from .manual_review import ManualReview
        saved = (self.ns.get('previous_config') or {}).get('manual_review') if self.ns['RUN_MODE'] == 'resume' else None
        self.ns['manual_review'] = ManualReview(self.ns['masks'], self.ns['mask_properties'].index, saved)
        self.log('Analysis ready. Review the ROI and adjust filters.')
        return self

    def attach(self, viewer):
        """Create Napari layers and widgets on the GUI thread."""
        import napari
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
        from magicgui.widgets import ComboBox, Container, FloatSlider, Label, PushButton, Slider
        from qtpy.QtWidgets import QFileDialog
        self.ns.update(viewer=viewer, napari=napari, plt=plt, PdfPages=PdfPages,
                       ComboBox=ComboBox, Container=Container, FloatSlider=FloatSlider,
                       Label=Label, PushButton=PushButton, Slider=Slider, QFileDialog=QFileDialog)
        if self.ns['RUN_MODE'] == 'density_only':
            self.stage('density_ui')
            self._watch_geometry(self.ns['retrospective_roi_layer'], self.ns['invalidate_retrospective_roi'])
        else:
            self.stage('scene')
            for data, kwargs in self.collector.layers.values():
                viewer.add_labels(data, **kwargs)
            for name, data in self.ns.get('z_corrected_volumes', {}).items():
                viewer.add_image(data, name=name + ' — Z corrected',
                                 scale=self.ns['VOXEL_SPACING_UM'], visible=False)
            self.stage('live')
            from .manual_review import ManualReviewPanel
            self.ns['manual_review_panel'] = ManualReviewPanel(self.ns, viewer)
            self._watch_geometry(self.ns['mntb_roi_layer'], self.ns['invalidate_mntb_roi'])
        viewer.dims.axis_labels = ('Z', 'Y', 'X')
        # Napari 0.9 obtains scale-bar units from calibrated layers.
        for layer in viewer.layers:
            layer.units = ('um',) * layer.ndim
        viewer.canvas.overlays.scale_bar.visible = True

    @staticmethod
    def _watch_geometry(layer, callback):
        for name in ('scale', 'translate', 'rotate', 'shear', 'affine'):
            if hasattr(layer.events, name):
                getattr(layer.events, name).connect(callback)

    def export(self):
        if self.ns['RUN_MODE'] == 'density_only':
            self.ns['save_retrospective_density']()
        else:
            # Recheck live state before exporting, including zero retained objects.
            self.ns['refresh_live_filter']()
            self.stage('export')

    def show_qc(self):
        if self.ns['RUN_MODE'] == 'density_only':
            raise ValueError('QC plots require resume or segment mode.')
        if self.ns.get('current_keep') is None:
            raise ValueError('Fix invalid filter settings first.')
        self.stage('qc')
        self.ns['plt'].show(block=False)

    def save_roi(self, path):
        density_only = self.ns['RUN_MODE'] == 'density_only'
        layer = self.ns['retrospective_roi_layer'] if density_only else self.ns['mntb_roi_layer']
        validate_roi_geometry(layer, self.ns['VOXEL_SPACING_UM'], self.ns['segmentation_volume'].shape)
        roi = np.asarray(layer.data) > 0
        if not roi.any():
            raise ValueError('Cannot save an empty ROI.')
        self.ns['calibrated_label_tiff'](path, roi.astype(np.uint8), self.ns['VOXEL_SPACING_UM'])
        self.log('ROI saved: ' + str(path))
