import json
import numpy as np
import pytest
import tifffile
from nuclear_segmentation.core import calibrated_label_tiff
from nuclear_segmentation.matching import Matching
from test_workflow import model_viewer


@pytest.fixture
def matching(tmp_path):
    d=np.zeros((4,8,8),np.uint16);d[1,1:3,1:3]=2;d[2,4:7,4:7]=7;d[2,1,1]=12
    m=np.zeros_like(d);m[:,0:4,0:4]=4;m[:,4:,4:]=8
    paths=[tmp_path/'dapi.tif',tmp_path/'map2.tif']
    for path,a in zip(paths,[d,m]):calibrated_label_tiff(path,a,(2,.5,.5))
    return Matching(*paths)


def test_matching_states_volumes_and_groups(matching,tmp_path):
    m=matching
    assert m.volumes[0]=={2:2.,7:4.5,12:.5}
    assert m.table().status.eq('Not reviewed').all()
    m.set(2,'Matched',4,'clear');m.set(7,'Unmatched');m.set(12,'Uncertain')
    np.testing.assert_array_equal(m.groups()['Matched'],[2])
    np.testing.assert_array_equal(m.groups()['Unmatched'],[4.5])
    m.set(12,'Matched',4)
    assert m.table().multiple_association.sum()==2
    assert len(m.groups(True)['Matched'])==0
    m.undo();assert m.records[12]['status']=='Uncertain'
    m.set(2,'Matched',8);assert m.records[2]['map2_id']==8
    m.set(2,'Not reviewed');assert 2 not in m.records
    m.undo();m.sample_id='sample_A';m.animal_id='mouse_A'
    folder=m.save(tmp_path)
    saved=json.loads((folder/'dapi_matching.json').read_text())
    restored=Matching(*m.paths);restored.restore(saved)
    assert restored.records==m.records
    assert restored.sample_id=='sample_A'
    assert restored.table().equals(m.table())
    assert (folder/'dapi_volume_distributions.pdf').stat().st_size>0
    with pytest.raises(ValueError):m.set(0,'Matched',4)
    with pytest.raises(ValueError):m.set(2,'Matched',99)
    m.dapi[0,0,0]=13
    calibrated_label_tiff(m.paths[0],m.dapi,m.spacing)
    changed=Matching(*m.paths)
    with pytest.raises(ValueError):changed.restore(saved)


def test_invalid_geometry(matching,tmp_path):
    path=tmp_path/'different.tif';calibrated_label_tiff(path,matching.map2[:2],matching.spacing)
    with pytest.raises(ValueError):Matching(matching.paths[0],path)
    calibrated_label_tiff(path,matching.map2,(1,1,1))
    with pytest.raises(ValueError):Matching(matching.paths[0],path)
    tifffile.imwrite(path,matching.map2.astype('float32'))
    with pytest.raises(ValueError):Matching(matching.paths[0],path)


def test_matching_panel_selection_and_reopen(matching,tmp_path,monkeypatch):
    from nuclear_segmentation.matching_ui import MatchingPanel
    from qtpy.QtWidgets import QFileDialog
    from types import SimpleNamespace
    viewer=model_viewer()
    try:
        panel=MatchingPanel(viewer);panel.attach(matching)
        assert not panel.reveal.isChecked()
        panel.on_click(panel.layers[0],SimpleNamespace(button=1,position=(2,.5,.5)))
        panel.on_click(panel.layers[1],SimpleNamespace(button=1,position=(2,.5,.5)))
        assert panel.dapi.value()==2 and panel.map2.value()==4
        with pytest.raises(ValueError):panel.decide('Matched')
        panel.aligned.setChecked(True);panel.decide('Matched')
        assert 'volume:' not in panel.details.text()
        panel.reveal.setChecked(True);assert 'DAPI volume: 2' in panel.details.text()
        panel.dapi.setValue(7);panel.decide('Unmatched')
        assert panel.map2.value()==0
        panel.dapi.setValue(2);assert panel.map2.value()==4
        panel.decide('Not reviewed');panel.undo();assert matching.records[2]['status']=='Matched'
        monkeypatch.setattr(QFileDialog,'getExistingDirectory',lambda *a:str(tmp_path))
        panel.save();assert not panel.dirty
        path=next(tmp_path.glob('dapi_matching_*/dapi_matching.json'))
        monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a:(str(path),''))
        panel.open_saved();assert panel.model.records[2]['map2_id']==4
        assert not panel.aligned.isChecked()
        panel.aligned.setChecked(True);panel.layers[0].scale=(1,1,1)
        with pytest.raises(ValueError):panel.decide('Unmatched')
    finally:viewer.close()
