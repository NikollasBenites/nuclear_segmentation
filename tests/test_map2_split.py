import copy
import json
import shutil
import numpy as np
import pytest
from nuclear_segmentation.matching import Matching
from nuclear_segmentation.map2_split import write_mask
from test_workflow import model_viewer


@pytest.fixture
def split_model(tmp_path):
    d=np.zeros((3,7,13),np.uint16);m=np.zeros_like(d)
    m[:,1:6,1:10]=65535;m[:,1:3,11:13]=99
    d[1,2:4,2:4]=2;d[1,2:4,7:9]=7;d[1,1,11]=9
    paths=[tmp_path/'dapi.ome.tif',tmp_path/'map2.ome.tif']
    for p,a in zip(paths,[d,m]):write_mask(p,a,(2.,.5,.5))
    model=Matching(*paths);model.run_automatic()
    return model


def test_split_conservation_edit_undo_and_reopen(split_model,tmp_path):
    model=split_model;old=model.map2.copy();nuclei=model.dapi.copy()
    records=copy.deepcopy(model.records);auto=copy.deepcopy(model.auto_rows)
    proposal=model.preview_split(65535);a,b=proposal['children'].values()
    assert a>65535 and b>a
    np.testing.assert_array_equal(model.map2,old)
    edit=proposal['data'].copy()
    # Reassign a boundary voxel, not a nuclear seed.
    pos=(0,4,5);edit[pos]=b if edit[pos]==a else a
    model.accept_split(proposal,edit)
    assert model.map2.dtype==np.uint32
    np.testing.assert_array_equal(model.dapi,nuclei)
    np.testing.assert_array_equal(model.map2[old!=65535],old[old!=65535])
    assert np.count_nonzero(model.map2==a)+np.count_nonzero(model.map2==b)==np.count_nonzero(old==65535)
    assert 65535 not in model.volumes[1]
    assert model.records[2]['map2_id']==a and model.records[7]['map2_id']==b
    assert model.records[9]==records[9]
    assert model.records[2]['source']=='manual' and model.split_log[-1]['manually_edited']
    assert sum(model.volumes[1][c] for c in [a,b])==np.count_nonzero(old==65535)*.5
    assert all(r['best_map2_id']!=65535 for r in model.auto_rows)
    # Self-contained export survives moving the entire folder.
    folder=model.save(tmp_path);moved=tmp_path/'moved';shutil.move(folder,moved)
    record=json.loads((moved/'dapi_matching.json').read_text())
    restored=Matching(*[moved/n for n in record['relative_mask_paths']])
    restored.restore(record,base_dir=moved)
    np.testing.assert_array_equal(restored.map2,model.map2)
    np.testing.assert_array_equal(restored.split_original,old)
    assert restored.records==model.records and restored.split_log==model.split_log
    second=restored.save(tmp_path)
    assert (second/'map2_before_splits.ome.tif').exists()
    model.undo()
    np.testing.assert_array_equal(model.map2,old)
    assert model.records==records and model.auto_rows==auto
    assert model.split_log==[] and model.split_original is None


def test_split_rejects_invalid_preview_and_stale_links(split_model):
    model=split_model;proposal=model.preview_split(65535)
    original=model.map2.copy()
    for variant in ['erase','outside','unknown','seed']:
        data=proposal['data'].copy()
        if variant=='erase':data[0,1,1]=0
        elif variant=='outside':data[0,0,0]=65536
        elif variant=='unknown':data[0,1,1]=123
        else:data[1,2,2]=proposal['children'][7]
        with pytest.raises(ValueError):model.accept_split(proposal,data)
        np.testing.assert_array_equal(model.map2,original)
    model.set(7,'Unmatched')
    with pytest.raises(ValueError,match='changed'):model.accept_split(proposal,proposal['data'])
    model.set(9,'Matched',65535)
    fresh=model.preview_split(65535)
    model.accept_split(fresh,fresh['data'])
    assert model.records[9]['status']=='Uncertain' and model.records[9]['map2_id'] is None
    assert model.records[7]['status']=='Unmatched'  # manual rejection is preserved


def test_split_unseeded_component(split_model):
    model=split_model;model.map2[0,6,12]=65535
    with pytest.raises(ValueError,match='disconnected'):model.preview_split(65535)


def test_split_matches_console_watershed_and_fraction(split_model):
    from scipy import ndimage as ndi
    from skimage.segmentation import watershed
    model=split_model;model.records={}  # console seeds need not be manually linked
    p=model.preview_split(65535)
    target=model.map2==65535;locations=np.where(target)
    box=tuple(slice(v.min(),v.max()+1) for v in locations)
    markers=np.zeros(target[box].shape,np.uint32)
    for d,child in p['children'].items():markers[(model.dapi[box]==d)&target[box]]=child
    expected=watershed(-ndi.distance_transform_edt(target[box],sampling=model.spacing),
                       markers=markers,mask=target[box])
    np.testing.assert_array_equal(p['data'][box],expected)
    assert p['seed_min_fraction']==.5 and len(p['children'])==2
    # The exact 50% boundary is included; smaller fractions are excluded.
    model.volumes[0][7]*=2
    assert len(model.preview_split(65535)['children'])==2
    model.volumes[0][7]*=1.5
    with pytest.raises(ValueError,match='At least two'):model.preview_split(65535)
    assert len(model.preview_split(65535,.3)['children'])==2


def test_split_gui_preview_cancel_accept_undo(split_model,tmp_path,monkeypatch):
    from nuclear_segmentation.matching_ui import MatchingPanel
    from qtpy.QtWidgets import QFileDialog
    viewer=model_viewer()
    try:
        panel=MatchingPanel(viewer);panel.attach(split_model);panel.aligned.setChecked(True)
        panel.dapi.setValue(2);old=split_model.map2.copy()
        panel.preview_map2_split();assert panel.split_layer.editable
        with pytest.raises(ValueError,match='Accept or cancel'):panel.save()
        panel.cancel_split();np.testing.assert_array_equal(split_model.map2,old)
        panel.preview_map2_split();panel.split_layer.translate=(1,0,0)
        with pytest.raises(ValueError,match='transform'):panel.accept_map2_split()
        panel.split_layer.translate=(0,0,0);panel.accept_map2_split()
        assert panel.dirty and panel.split_layer is None
        np.testing.assert_array_equal(panel.layers[1].data,split_model.map2)
        panel.multiple.setChecked(True);assert not panel.multiple_layers[0].data.any()
        panel.undo();np.testing.assert_array_equal(panel.layers[1].data,old)
        assert panel.multiple_layers[0].data.any()
        panel.dapi.setValue(2);panel.preview_map2_split();panel.accept_map2_split()
        monkeypatch.setattr(QFileDialog,'getExistingDirectory',lambda *args:str(tmp_path))
        panel.save();path=next(tmp_path.glob('dapi_matching_*/dapi_matching.json'))
        monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(path),''))
        panel.open_saved();assert panel.model.split_log and not panel.aligned.isChecked()
        assert 65535 not in panel.model.volumes[1]
    finally:viewer.close()
