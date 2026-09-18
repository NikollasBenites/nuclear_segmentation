import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLBACKEND', 'Agg')
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import tifffile
from nuclear_segmentation import core
from nuclear_segmentation.config import defaults, find_saved_roi
from nuclear_segmentation.runtime import AnalysisSession


def settings(mode='none',guard=0):
    return dict(first_tissue_z=0,last_tissue_z=11,z_guard_um=guard,z_guard_mode=mode,
                minimum_volume_um3=0,minimum_sphericity=0,perinuclear_combination='all',
                perinuclear_filters={},intensity_combination='all',intensity_filters={})


def test_density_units_and_correction():
    result=core.calculate_density_summary(np.full(12,100),settings(),(2,1,1),6,True)
    assert result['roi_effective_volume_um3']==2400
    assert result['retained_nuclei_per_100um_cube']==2500
    assert result['measured_full_tissue_thickness_um']==24
    assert result['nominal_corrected_nuclei_per_100um_cube']==1000
    guarded=core.calculate_density_summary(np.full(12,100),settings('both',2),(2,1,1),4,True)
    assert guarded['roi_effective_voxels']==800
    assert guarded['measured_full_tissue_thickness_um']==24
    assert guarded['thickness_density_correction_factor']==.4
    assert core.calculate_density_summary(np.full(12,100),settings(),(2,1,1),6,False)['retained_nuclei_per_mm3'] is None
    assert core.calculate_density_summary(np.full(12,100),settings('both',100),(2,1,1),0,True)['retained_nuclei_per_mm3'] is None


def test_roi_and_calibration():
    image=np.zeros((2,3,8,8),dtype='uint8');image[1,:,1:7,1:7]=1;image[:,:,3,3]=0
    roi=core.reconstruct_mntb_roi(image)
    assert roi.sum()==108
    with pytest.raises(ValueError):core.calculate_density_summary([1],settings(),(0,1,1),1,True)


def test_roi_discovery(tmp_path):
    root=tmp_path/'mntb_roi.tif';root.write_bytes(b'0')
    recent=tmp_path/'density_addendum_001'/'mntb_roi.tif';recent.parent.mkdir();recent.write_bytes(b'1')
    os.utime(root,(100,100));os.utime(recent,(200,200))
    assert find_saved_roi(tmp_path)==recent


@pytest.fixture
def sample(tmp_path):
    cfg=defaults();cfg.update(RUN_MODE='resume',COMPUTE_INPUT_SHA256=False,INITIAL_MIN_VOLUME_UM3=0)
    z,y,x=np.indices((12,32,32));masks=np.zeros(z.shape,dtype='uint16')
    masks[((z-4)/2)**2+((y-10)/3)**2+((x-10)/3)**2<=1]=2
    masks[((z-7)/2)**2+((y-22)/3)**2+((x-22)/3)**2<=1]=7
    image=np.zeros((12,4,32,32),dtype='uint8');image[:,:,2:30,2:30]=12
    image[:,0][masks>0]=100;image[:,3][masks>0]=80
    image_path=tmp_path/'sample.tif'
    tifffile.imwrite(image_path,image,imagej=True,metadata={'axes':'ZCYX','spacing':2,'unit':'um'},resolution=(1,1))
    previous=tmp_path/'previous';previous.mkdir();core.calibrated_label_tiff(previous/'cellpose_raw_masks.tif',masks,(2,1,1))
    filters=settings()
    channels=cfg['CHANNELS']
    for channel in channels:channel['filter_enabled']=False
    markers=[]
    for item in cfg['PERINUCLEAR_MARKERS']:
        markers.append({**item,'minimum_positive_fraction':0,'resolved_pixel_threshold':1})
    saved=dict(input={'sha256':None},channels=channels,segmentation_channel='DAPI',
               filters=filters,intensity_measurements={'mode':'raw','global_background_percentile':50},
               cellpose={'model_name':'cpsam_v2','cellprob_threshold':-1,'minimum_size_voxels':100,'batch_size':8,'flow3d_smooth':[1,0,0]},
               calibration={'voxel_spacing_zyx_um':[2,1,1]},perinuclear_markers=markers,perinuclear_combination='all',nominal_section_thickness_um=60)
    (previous/'analysis_config.json').write_text(json.dumps(saved))
    return cfg,image_path,previous,masks


def model_viewer():
    # Real Napari layer model + real Qt widgets, without requiring an OpenGL display.
    from napari.components import ViewerModel
    from qtpy.QtWidgets import QApplication, QMainWindow, QDockWidget
    from qtpy.QtCore import Qt
    app = QApplication.instance() or QApplication([])
    model = ViewerModel()
    class Window:
        def __init__(self):
            self._qt_window = QMainWindow()
            self.app = app
        def add_dock_widget(self, widget, area="right", name=""):
            dock = QDockWidget(name, self._qt_window)
            dock.setWidget(getattr(widget, "native", widget))
            self._qt_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            return dock
        def remove_dock_widget(self, widget):
            self._qt_window.removeDockWidget(widget)
    class Adapter:
        def __init__(self): self.window=Window()
        def __getattr__(self, key): return getattr(model,key)
        def close(self): self.window._qt_window.close()
    return Adapter()


def test_resume_export_and_density_only(sample,tmp_path):
    import napari
    cfg,image,previous,masks=sample
    session=AnalysisSession(cfg,image,previous,log=lambda *_:None).prepare()
    assert 'torch' not in session.ns and 'models' not in session.ns
    assert len(session.ns['mask_properties'])==2
    viewer=model_viewer()
    try:
        session.attach(viewer)
        assert viewer.canvas.overlays.scale_bar.visible
        for layer in viewer.layers:
            assert all(str(unit) == 'micrometer' for unit in layer.units)
        session.ns['accept_mntb_roi']()
        assert session.ns['roi_reviewed']
        assert session.ns['current_keep'].sum()==2
        # Actual Labels brush events must invalidate the reviewed ROI.
        roi_layer = session.ns['mntb_roi_layer']
        original_voxel = int(roi_layer.data[4, 10, 10])
        roi_layer.brush_size = 1
        roi_layer.paint((4, 10, 10), 0)
        assert not session.ns['roi_reviewed']
        roi_layer.paint((4, 10, 10), original_voxel)
        session.ns['accept_mntb_roi']()
        assert session.ns['roi_reviewed']
        # Exercise a live morphology control, not just initial filter settings.
        session.ns['sphericity_widget'].value = 1.0
        assert session.ns['current_keep'].sum() == 0
        session.ns['sphericity_widget'].value = 0.0
        assert session.ns['current_keep'].sum() == 2
        assert 'nuclei/(100 µm)³' in session.ns['roi_volume_label'].value
        class Dialog:
            @staticmethod
            def getExistingDirectory(*args):return str(tmp_path)
        session.ns['QFileDialog']=Dialog
        session.export()
        export=session.ns['sample_export_directory']
        summary=pd.read_csv(export/'mntb_density_summary.csv').iloc[0]
        assert summary['retained_nuclei_in_roi']==2
        assert summary['nominal_corrected_nuclei_per_100um_cube']==pytest.approx(summary['retained_nuclei_per_100um_cube']*.4)
        assert (export/'analysis_report.pdf').is_file()
        assert (export/'analysis_config.json').is_file()
        session.show_qc()
        cfg['RUN_MODE']='density_only'
        retro=AnalysisSession(cfg,image,export,log=lambda *_:None).prepare()
        assert 'mask_properties' not in retro.ns
        assert Path(retro.ns['MNTB_ROI_MASK_PATH'])==export/'mntb_roi.tif'
        v2=model_viewer()
        try:
            retro.attach(v2);retro.ns['calculate_retrospective_density']()
            assert retro.ns['retrospective_result']['retained_nuclei_in_roi']==2
            retro.export()
            addendum=next(export.glob('density_addendum_*'))
            assert (addendum/'density_addendum.json').is_file()
            retro.ns['retrospective_roi_layer'].data=np.zeros_like(masks)
            assert not retro.ns['retrospective_reviewed']
        finally:v2.close()
        # Invalid settings must remove stale display and block export.
        session.ns['first_z_widget'].value=10
        session.ns['last_z_widget'].value=2
        assert session.ns['current_keep'] is None
        with pytest.raises(RuntimeError):session.export()
    finally:
        viewer.close()
        import matplotlib.pyplot as plt
        plt.close('all')


def test_launcher_roundtrip():
    import napari
    from nuclear_segmentation.ui import Launcher
    viewer=napari.Viewer(show=False)
    try:
        widget=Launcher(viewer)
        cfg=widget.get_config()
        assert cfg['CHANNELS']==defaults()['CHANNELS']
        assert cfg['NOMINAL_SECTION_THICKNESS_UM']==60
        assert cfg['FLOW3D_SMOOTH']==[1,0,0]
        widget.apply_config(cfg)
        assert widget.get_config()==cfg
    finally:viewer.close()


def test_new_segmentation_dispatch_without_downloading_model(sample,monkeypatch):
    # Test parameter/device dispatch, not Cellpose inference accuracy.
    import sys
    from types import SimpleNamespace, ModuleType
    cfg,image,previous,masks=sample;cfg['RUN_MODE']='segment'
    calls={}
    class Device:
        def __init__(self,kind):self.type=kind
        def __str__(self):return self.type
    torch=ModuleType('torch');torch.device=Device
    torch.cuda=SimpleNamespace(is_available=lambda:False)
    torch.backends=SimpleNamespace(mps=SimpleNamespace(is_built=lambda:False,is_available=lambda:False))
    cp=ModuleType('cellpose');cp.io=SimpleNamespace(logger_setup=lambda:None)
    class Model:
        def __init__(self,**kw):calls['model']=kw
        def eval(self,volume,**kw):
            calls['eval']=kw
            return masks,[None,None,np.zeros_like(masks)],None
    cp.models=SimpleNamespace(CellposeModel=Model)
    monkeypatch.setitem(sys.modules,'torch',torch);monkeypatch.setitem(sys.modules,'cellpose',cp)
    session=AnalysisSession(cfg,image,log=lambda *_:None).prepare()
    assert calls['eval']['do_3D'] is True
    assert calls['eval']['anisotropy']==2
    assert calls['eval']['flow3D_smooth']==[1,0,0]
    assert calls['model']['device'].type=='cpu'
    assert len(session.ns['mask_properties'])==2


def test_launcher_background_resume(sample):
    from nuclear_segmentation.ui import Launcher
    from qtpy.QtWidgets import QApplication
    import time
    cfg,image,previous,masks=sample
    viewer=model_viewer();launcher=Launcher(viewer)
    launcher.image_path.setText(str(image));launcher.previous_path.setText(str(previous))
    launcher.apply_config(cfg)
    errors=[]
    launcher.analysis_failed=lambda message:errors.append(message)
    launcher.start_analysis()
    deadline=time.monotonic()+20
    while launcher.running and time.monotonic()<deadline:
        QApplication.processEvents()
        time.sleep(.01)
    try:
        assert not launcher.running
        assert not errors,errors
        assert launcher.session is not None
        assert launcher.export_button.isEnabled()
    finally:
        if launcher.running:
            launcher.thread.quit();launcher.thread.wait(10000)
        viewer.close()


def test_synchronized_notebooks_and_m3_density_equivalence():
    notebooks=Path(__file__).resolve().parents[1]/'notebooks'
    m3=notebooks/'general_nuclei_segmentation_m3_v5_5_live_perinuclear_shells.ipynb'
    windows=notebooks/'general_nuclei_segmentation_v5_5_live_perinuclear_shells.ipynb'
    assert m3.read_bytes()==windows.read_bytes()
    data=json.loads(m3.read_text())
    source=next(''.join(c['source']) for c in data['cells'] if ''.join(c['source']).startswith('UNIT_TO_UM'))
    reference=vars(core).copy();exec(source,reference)
    for mode in ['none','first','last','both']:
        for guard in [0,.5,2,100]:
            args=(np.arange(12)*19,settings(mode,guard),(2,.6,.6),17,True)
            assert reference['calculate_density_summary'](*args)==core.calculate_density_summary(*args)


def test_cli_main_with_real_napari_viewer(monkeypatch):
    """Exercise actual CLI setup with a real Pydantic Viewer, without blocking."""
    import napari
    from nuclear_segmentation.app import main
    from nuclear_segmentation.ui import Launcher
    viewers=[]
    original_viewer=napari.Viewer
    def create_viewer(*args,**kwargs):
        kwargs['show']=False
        viewer=original_viewer(*args,**kwargs)
        viewers.append(viewer)
        return viewer
    def run():
        assert len(viewers)==1
        launchers=viewers[0].window._qt_window.findChildren(Launcher)
        assert len(launchers)==1
        assert launchers[0].start.isEnabled()
    monkeypatch.setattr(napari,'Viewer',create_viewer)
    monkeypatch.setattr(napari,'run',run)
    try:
        main([])
    finally:
        for viewer in viewers:viewer.close()
