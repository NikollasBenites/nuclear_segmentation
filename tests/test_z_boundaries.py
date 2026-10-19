"""Boundary cases independent of image shape, intensity and morphology filters."""
import numpy as np
import pandas as pd
import pytest
from nuclear_segmentation import core


def members(mode, bounds, first=2, last=7, guard=0, dz=1):
    props = pd.DataFrame(bounds, columns=['min_z', 'max_z'])
    props['centroid_z_px'] = (props.min_z + props.max_z) / 2
    props['centroid_y_px'] = props['centroid_x_px'] = 1
    settings = dict(first_tissue_z=first, last_tissue_z=last,
                    z_guard_mode=mode, z_guard_um=guard)
    roi = np.ones((12, 3, 3), bool)
    inside, in_z, entry = core.sampling_membership(props, roi, settings, dz)
    guards = core.calculate_z_guard_properties(props, first, last, guard, mode, dz, 11)
    return (inside & in_z & ~guards.excluded_by_z_guard).tolist(), entry


def test_first_boundary_zero_guard_and_inclusion_crossing():
    # Touch first / cross first / enter on last / enter after last /
    # valid entry but centroid beyond last / fully inside.
    keep, entry = members('first', [(2,4),(1,4),(7,10),(8,10),(6,11),(3,5)])
    assert keep == [False, False, True, False, True, True]
    assert entry.tolist() == [2,1,7,8,6,3]


def test_last_is_exact_mirror_of_first():
    bounds = [(2,4),(1,4),(7,10),(8,10),(6,11),(3,5)]
    forward, _ = members('first', bounds)
    mirrored = [(11-hi, 11-lo) for lo,hi in bounds]
    reverse, _ = members('last', mirrored, first=4, last=9)
    assert reverse == forward


def test_both_preserves_whole_object_exclusion():
    assert members('both', [(2,4),(3,7),(3,6),(1,9)])[0] == [False,False,True,False]


def test_none_preserves_centroid_rule():
    assert members('none', [(1,3),(7,11),(0,1)])[0] == [True,False,False]


def test_fractional_guard_matches_effective_planes():
    assert members('first', [(3,3),(4,4),(7,9)], guard=.7, dz=.5)[0] == [False,True,True]
    assert members('last', [(6,6),(5,5),(0,2)], guard=.7, dz=.5)[0] == [False,True,True]


@pytest.mark.parametrize('spacing', [0, -1, float('nan'), float('inf')])
def test_invalid_spacing_rejected(spacing):
    with pytest.raises(ValueError):
        members('first', [(3,5)], dz=spacing)


def test_utf8_stage_loading_on_windows_locale(tmp_path, monkeypatch):
    from pathlib import Path
    from nuclear_segmentation import runtime
    from nuclear_segmentation.config import defaults
    folder = tmp_path / 'stages'
    folder.mkdir()
    (folder / 'probe.py').write_text("label = 'µm³ σ ≥ →'\n", encoding='utf-8')
    original_read = Path.read_text
    def windows_read(path, encoding=None, **kwargs):
        return original_read(path, encoding=encoding or 'cp1252', **kwargs)
    monkeypatch.setattr(runtime, 'files', lambda _: tmp_path)
    session = runtime.AnalysisSession(defaults(), tmp_path / 'unused.tif')
    monkeypatch.setattr(Path, 'read_text', windows_read)
    session.stage('probe')
    assert session.ns['label'] == 'µm³ σ ≥ →'


from test_workflow import sample, model_viewer
from nuclear_segmentation.runtime import AnalysisSession


def test_live_directional_export_density_roundtrip(sample, tmp_path, monkeypatch):
    cfg, image, previous, _ = sample
    session = AnalysisSession(cfg, image, previous, log=lambda *_: None).prepare()
    viewer = model_viewer()
    try:
        session.attach(viewer); ns = session.ns
        ns['guard_mode_widget'].value = 'first'
        ns['guard_widget'].value = 0
        ns['last_z_widget'].value = 3
        ns['accept_mntb_roi']()
        assert ns['current_keep'].loc[2]
        assert not ns['filter_properties'].loc[2, 'centroid_in_sampling_z']
        assert not ns['current_keep'].loc[7]
        ns['first_z_widget'].value = 4
        assert ns['current_keep'] is None
        assert not np.any(viewer.layers['live filtered masks'].data)
        ns['first_z_widget'].value = 0
        assert ns['current_keep'].loc[2]
        from qtpy.QtWidgets import QFileDialog
        out = tmp_path / 'out'; out.mkdir()
        monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **k: str(out))
        session.export()
        exported = next(out.rglob('analysis_config.json')).parent
    finally:
        viewer.close()
    cfg['RUN_MODE'] = 'density_only'
    restored = AnalysisSession(cfg, image, exported, log=lambda *_: None).prepare()
    viewer = model_viewer()
    try:
        restored.attach(viewer)
        restored.ns['calculate_retrospective_density']()
        assert restored.ns['retrospective_table']['included_in_density'].sum() == 1
    finally:
        viewer.close()
