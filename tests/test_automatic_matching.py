import copy
import json
import numpy as np
import pytest
from nuclear_segmentation.automatic_matching import classify, overlap_pairs, COLORS
from nuclear_segmentation.matching import Matching
from nuclear_segmentation.core import calibrated_label_tiff
from test_workflow import model_viewer


@pytest.fixture
def auto_model(tmp_path):
    # Five nuclei with 10 voxels each; exact 50/20% boundaries included.
    d=np.zeros((2,5,10),np.uint32);m=np.zeros_like(d)
    for i in range(5):d[0,i,:]=[2,7,99,300,1000000][i]
    m[0,0,:5]=10;m[0,0,5:7]=20   # MATCH 0.50, runner 0.20
    m[0,1,:5]=10;m[0,1,5:8]=20   # AMBIGUOUS runner 0.30
    m[0,2,:4]=30                 # AMBIGUOUS best 0.40
    # dapi300 no overlap
    m[0,4,:]=40                  # MATCH full coverage
    paths=[tmp_path/'dapi.tif',tmp_path/'map2.tif']
    # ImageJ does not support uint32; plain OME TIFF supports sparse labels.
    import tifffile
    for path,a in zip(paths,[d,m]):
        tifffile.imwrite(path,a,ome=True,metadata={'axes':'ZYX','PhysicalSizeZ':2.,'PhysicalSizeY':.5,'PhysicalSizeX':.5,
           'PhysicalSizeZUnit':'µm','PhysicalSizeYUnit':'µm','PhysicalSizeXUnit':'µm'},photometric='minisblack')
    return Matching(*paths)


def test_console_rules_and_physical_volumes(auto_model):
    model=auto_model;d=model.dapi.copy();m=model.map2.copy();rows=model.run_automatic()
    assert [r['auto_class'] for r in rows]==['MATCH','AMBIGUOUS','AMBIGUOUS','NO_MATCH','MATCH']
    assert all(r['dapi_volume_um3']==5 for r in rows)
    assert rows[0]['second_dapi_fraction']==.2 and rows[0]['overlap_volume_um3']==2.5
    assert model.records[2]['status']=='Matched' and model.records[7]['status']=='Uncertain'
    assert model.records[300]['status']=='Unmatched'
    assert rows[3]['best_map2_id'] is None
    assert rows[-1]['iou']==1 and rows[-1]['dice']==1
    np.testing.assert_array_equal(model.dapi,d);np.testing.assert_array_equal(model.map2,m)
    with pytest.raises(ValueError):model.run_automatic(0,.2)
    with pytest.raises(ValueError):model.run_automatic(.5,float('nan'))


def test_manual_preservation_batch_undo_and_restore(auto_model,tmp_path):
    model=auto_model;model.set(2,'Unmatched',comment='manual rejection');original=copy.deepcopy(model.records)
    model.run_automatic();assert model.records[2]==original[2]
    before=copy.deepcopy(model.records);model.run_automatic(.8,.2)
    assert model.records[2]['source']=='manual'
    model.undo();assert model.records==before and model.auto_settings['match_min']==.5
    folder=model.save(tmp_path);saved=json.loads((folder/'dapi_matching.json').read_text())
    restored=Matching(*model.paths);restored.restore(saved)
    assert restored.records==model.records and restored.auto_rows==model.auto_rows
    restored.run_automatic(.9,.1);assert restored.records[2]['source']=='manual'
    assert (folder/'automatic_matching.csv').exists() and (folder/'automatic_matching.pdf').exists()
    model.undo();assert model.records==original and model.auto_rows==[]


def test_ties_and_no_overlap():
    rows,_=classify({1:10},{3:5,2:5},1,{(1,3):5,(1,2):5})
    assert rows[0]['best_map2_id']==2 and rows[0]['auto_class']=='AMBIGUOUS'
    assert overlap_pairs(np.ones((2,2,2),dtype=int),np.zeros((2,2,2),dtype=int))=={}


def test_qc_selection_and_export(auto_model,tmp_path,monkeypatch):
    from nuclear_segmentation.matching_ui import MatchingPanel
    from qtpy.QtWidgets import QFileDialog
    viewer=model_viewer()
    try:
        panel=MatchingPanel(viewer);panel.attach(auto_model)
        with pytest.raises(ValueError):panel.run_automatic()
        panel.aligned.setChecked(True);panel.run_automatic()
        qc=panel.qc_layer;assert qc.name=='DAPI_MATCH_QC' and qc.editable is False
        np.testing.assert_array_equal(qc.data,auto_model.dapi)
        np.testing.assert_allclose(qc.colormap.color_dict[2],COLORS['MATCH'])
        np.testing.assert_allclose(qc.colormap.color_dict[7],COLORS['AMBIGUOUS'])
        np.testing.assert_allclose(qc.colormap.color_dict[300],COLORS['NO_MATCH'])
        panel.auto_group.setCurrentText('MATCH')
        assert set(np.unique(qc.data))=={0,2,1000000}
        panel.dapi.setValue(99);panel.best_candidate();assert panel.map2.value()==30
        panel.decide('Matched');assert auto_model.records[99]['source']=='manual'
        panel.run_automatic();assert auto_model.records[99]['status']=='Matched'
        panel.plot_automatic()
        monkeypatch.setattr(QFileDialog,'getExistingDirectory',lambda *a:str(tmp_path))
        panel.save()
        path=next(tmp_path.glob('dapi_matching_*/dapi_matching.json'))
        monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a:(str(path),''))
        panel.open_saved();assert panel.qc_layer is not None and panel.model.auto_settings['match_min']==.5
        assert not panel.aligned.isChecked()
    finally:viewer.close()


def test_live_multiple_associations(auto_model,tmp_path):
    from nuclear_segmentation.matching_ui import MatchingPanel
    viewer=model_viewer()
    try:
        panel=MatchingPanel(viewer);panel.attach(auto_model);panel.aligned.setChecked(True)
        panel.run_automatic()
        panel.dapi.setValue(7);panel.map2.setValue(10);panel.decide('Matched')
        panel.only.setChecked(True);panel.multiple.setChecked(True)
        assert not panel.only.isChecked()
        d,m=panel.multiple_layers
        assert set(np.unique(d.data))=={0,2,7}
        assert set(np.unique(m.data))=={0,10}
        assert not d.editable and not d.show_selected_label
        assert not panel.qc_layer.visible and not any(l.visible for l in panel.layers)
        # Click/selection on a filtered layer still controls the original ID selectors.
        d.selected_label=7;assert panel.dapi.value()==7
        panel.decide('Unmatched')
        assert not d.data.any() and not m.data.any()
        assert 'No multiple associations' in panel.multiple_note.text()
        panel.undo();assert set(np.unique(d.data))=={0,2,7}
        panel.run_automatic();assert set(np.unique(d.data))=={0,2,7}
        # Group switches must not reveal unrelated masks while this mode is on.
        panel.auto_group.setCurrentText('NO_MATCH')
        assert not panel.qc_layer.visible and d.visible
        panel.multiple.setChecked(False);assert not d.visible and panel.qc_layer.visible
        panel.multiple.setChecked(True)
        panel.only.setChecked(True);assert not panel.multiple.isChecked()
        panel.multiple.setChecked(True)
        # A manual change of partner resolves the multiple association immediately.
        panel.dapi.setValue(7);panel.map2.setValue(30);panel.decide('Matched')
        assert not d.data.any()
        panel.undo()
        folder=panel.model.save(tmp_path)
        restored=Matching(*auto_model.paths)
        restored.restore(json.loads((folder/'dapi_matching.json').read_text()))
        panel.attach(restored)
        assert d not in viewer.layers and not panel.multiple.isChecked()
        panel.multiple.setChecked(True)
        assert set(np.unique(panel.multiple_layers[0].data))=={0,2,7}
        # Reset review removes the link and the resolved pair from inspection.
        panel.aligned.setChecked(True);panel.dapi.setValue(7);panel.decide('Not reviewed')
        assert not panel.multiple_layers[0].data.any()
    finally:viewer.close()
