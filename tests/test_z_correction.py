import json
import sys
from types import SimpleNamespace, ModuleType
import numpy as np
import pytest
from nuclear_segmentation.z_correction import correct_z, prepare_volumes
from nuclear_segmentation.runtime import AnalysisSession
from test_workflow import sample, model_viewer


def test_positive_profile_gain_and_zeros():
    data = np.zeros((5, 4, 4), np.float32)
    data[:, 1:3, 1:3] = np.array([100, 40, 20, 40, 100])[:, None, None]
    original = data.copy()
    result, report = correct_z(data, {'sigma_planes': 0})
    np.testing.assert_allclose(report['gain'], [.5, 1, 2, 1, .5])
    np.testing.assert_array_equal(result[data == 0], 0)
    np.testing.assert_array_equal(data, original)
    assert result.dtype == np.float32
    empty = data.copy(); empty[2] = 0
    result, report = correct_z(empty)
    assert report['observed_profile'][2] is None
    assert not result[2].any()
    assert np.isfinite(result).all()
    with pytest.raises(ValueError): correct_z(np.zeros_like(data))
    with pytest.raises(ValueError): correct_z(data, {'min_gain': 4, 'max_gain': 1})


@pytest.mark.parametrize('measure', [False, True])
def test_routing(measure):
    source = np.arange(1, 13).reshape(3, 2, 2).astype('float32')
    ns = dict(channel_volumes={'MAP2': source}, SEGMENTATION_CHANNEL='MAP2',
              Z_CORRECTIONS={'MAP2': {'measurement': measure, 'sigma_planes': 0}})
    prepare_volumes(ns)
    expected, _ = correct_z(source, {'measurement': measure, 'sigma_planes': 0})
    np.testing.assert_array_equal(ns['model_input_volume'], expected)
    np.testing.assert_array_equal(ns['channel_volumes']['MAP2'], expected if measure else source)
    assert ns['original_channel_volumes']['MAP2'] is source


def test_selected_layer_preview_apply(sample, tmp_path, monkeypatch):
    from nuclear_segmentation.ui import Launcher
    from nuclear_segmentation.normalization_ui import NormalizationPreview
    cfg, image, previous, masks = sample; cfg['RUN_MODE'] = 'segment'
    session = AnalysisSession(cfg, image, log=lambda *_: None).prepare(preview_only=True)
    viewer = model_viewer()
    try:
        launcher = Launcher(viewer); launcher.apply_config(cfg); launcher.image_path.setText(str(image))
        preview = NormalizationPreview(launcher, session); panel = preview.z_panel
        viewer.layers.selection.active = preview.original_layers['MAP2']
        raw = preview.original_layers['MAP2'].data.copy()
        panel.correct()
        first = panel.outputs['MAP2'].data.copy()
        from qtpy.QtWidgets import QFileDialog
        import tifffile
        target = tmp_path / 'preview.ome.tif'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(target), ''))
        panel.save_corrected()
        np.testing.assert_array_equal(tifffile.imread(target), first)
        panel.include_export.setChecked(True)
        assert launcher.get_config()['EXPORT_CORRECTED_IMAGES'] is True
        assert panel.outputs['MAP2'].name == 'MAP2 — Z corrected'
        panel.correct()  # Output selected: must start from original, not cascade.
        np.testing.assert_array_equal(panel.outputs['MAP2'].data, first)
        panel.apply()
        saved = launcher.get_config()
        assert saved['SEGMENTATION_CHANNEL'] == 'MAP2'
        assert saved['Z_CORRECTIONS']['MAP2']['measurement'] is False
        assert saved['CELLPOSE_NORMALIZE'] == cfg['CELLPOSE_NORMALIZE']
        ns = dict(channel_volumes=session.ns['channel_volumes'], **saved)
        prepare_volumes(ns)
        np.testing.assert_array_equal(ns['model_input_volume'], first)
        np.testing.assert_array_equal(ns['channel_volumes']['MAP2'], raw)
        panel.measure.setChecked(True)
        with pytest.raises(ValueError): panel.apply()
        panel.correct(); panel.apply()
        assert launcher.get_config()['Z_CORRECTIONS']['MAP2']['measurement'] is True
    finally: viewer.close()


@pytest.mark.parametrize('measure', [False, True])
def test_segment_export_resume_map2_without_shells(sample, monkeypatch, tmp_path, measure):
    cfg, image, previous, masks = sample
    cfg.update(RUN_MODE='segment', SEGMENTATION_CHANNEL='MAP2', PERINUCLEAR_MARKERS=[],
               Z_CORRECTIONS={'MAP2': {'sigma_planes': 0, 'measurement': measure}}, EXPORT_CORRECTED_IMAGES=True)
    # Also exercise one configured channel while reading a multichannel TIFF.
    cfg['CHANNELS'] = [c for c in cfg['CHANNELS'] if c['name'] == 'MAP2']
    cfg['CHANNELS'][0]['role'] = 'segmentation'
    class Device:
        type = 'cpu'
        def __init__(self, *a): pass
        def __str__(self): return 'cpu'
    torch = ModuleType('torch'); torch.device = Device
    torch.cuda = SimpleNamespace(is_available=lambda: False)
    torch.backends = SimpleNamespace(mps=SimpleNamespace(is_built=lambda: False))
    cp = ModuleType('cellpose'); cp.io = SimpleNamespace(logger_setup=lambda: None)
    calls = {}
    class Model:
        def __init__(self, **kw): pass
        def eval(self, volume, **kw):
            calls['input'] = volume.copy(); calls['normalize'] = kw['normalize']
            volume[:] = 0
            return masks, [None, None, masks * 0], None
    cp.models = SimpleNamespace(CellposeModel=Model)
    monkeypatch.setitem(sys.modules, 'torch', torch); monkeypatch.setitem(sys.modules, 'cellpose', cp)
    session = AnalysisSession(cfg, image, log=lambda *_: None).prepare()
    original = session.ns['original_channel_volumes']['MAP2'].copy()
    expected, _ = correct_z(original, cfg['Z_CORRECTIONS']['MAP2'])
    np.testing.assert_array_equal(calls['input'], expected)
    np.testing.assert_array_equal(session.ns['channel_volumes']['MAP2'], expected if measure else original)
    assert calls['normalize'] == cfg['CELLPOSE_NORMALIZE']
    assert session.ns['perinuclear_metrics'].empty
    viewer = model_viewer()
    try:
        session.attach(viewer)
        session.ns['accept_mntb_roi']()
        session.ns['QFileDialog'] = SimpleNamespace(getExistingDirectory=lambda *a: str(tmp_path))
        session.export()
        folder = session.ns['sample_export_directory']
        saved = json.loads((folder / 'analysis_config.json').read_text())
        assert saved['z_corrections']['MAP2']['measurement'] is measure
        assert (folder / 'z_correction_profiles.csv').exists()
        import tifffile
        np.testing.assert_array_equal(tifffile.imread(folder / 'corrected_channels.ome.tif'), expected)
        assert (folder / 'corrected_channels.ome.tif.json').exists()
        cfg['RUN_MODE'] = 'resume'
        resumed = AnalysisSession(cfg, image, folder, log=lambda *_: None).prepare()
        np.testing.assert_array_equal(resumed.ns['channel_volumes']['MAP2'], session.ns['channel_volumes']['MAP2'])
        np.testing.assert_array_equal(resumed.ns['masks'], masks)
    finally: viewer.close()
