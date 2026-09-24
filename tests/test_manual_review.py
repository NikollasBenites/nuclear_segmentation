import json
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
import tifffile
from nuclear_segmentation.manual_review import ManualReview
from nuclear_segmentation.runtime import AnalysisSession
from test_workflow import sample, model_viewer


def test_decisions_and_mask_identity():
    masks=np.array([[[0,2,7]]],dtype=np.uint32)
    review=ManualReview(masks,[2,7]);automatic=pd.Series([False,True],index=[2,7]);eligible=pd.Series([True,False],index=[2,7])
    review.set(2,'keep','valid cell');review.set(7,'keep')
    assert review.apply(automatic,eligible).tolist()==[True,False]
    review.set(2,'exclude');assert not review.apply(automatic,eligible).any()
    review.undo();assert review.decisions[2]['decision']=='keep'
    restored=ManualReview(masks.astype('uint16'),[2,7],review.to_record())
    assert restored.decisions==review.decisions
    with pytest.raises(ValueError):ManualReview(masks*2,[2,7],review.to_record())
    with pytest.raises(ValueError):review.set(0,'exclude')
    review.set(2,'automatic');assert 2 not in review.decisions


@pytest.mark.parametrize('zero_retained',[False,True])
def test_review_live_export_resume(sample,tmp_path,zero_retained):
    cfg,image,previous,masks=sample
    session=AnalysisSession(cfg,image,previous,log=lambda *_:None).prepare()
    viewer=model_viewer()
    try:
        session.attach(viewer);ns=session.ns;panel=ns['manual_review_panel']
        ns['accept_mntb_roi']();original=ns['masks'].copy()
        panel.pick();assert viewer.layers['raw Cellpose masks'].editable is False
        panel.on_click(viewer.layers['raw Cellpose masks'],SimpleNamespace(button=1,position=(8,10,10)))
        assert panel.label.value()==2
        viewer.layers['raw Cellpose masks'].selected_label=2
        assert panel.label.value()==2
        panel.reason.setText('artifact');panel.decide('exclude')
        assert ns['current_keep'].sum()==1
        for name in ns['PERINUCLEAR_MARKER_RESULTS']:
            assert 2 not in np.unique(viewer.layers[f'live {name} perinuclear shells'].data)
        ns['sphericity_widget'].value=1
        assert not ns['current_keep'].any()
        panel.decide('keep');assert ns['current_keep'].loc[2]
        ns['sphericity_widget'].value=0
        assert ns['current_keep'].sum()==2
        # Guard remains mandatory even for an existing keep override.
        ns['guard_mode_widget'].value='first';ns['first_z_widget'].value=5
        assert not ns['current_keep'].loc[2]
        with pytest.raises(ValueError):panel.decide('keep')
        ns['first_z_widget'].value=0;ns['guard_mode_widget'].value='none'
        assert ns['current_keep'].loc[2]
        # A newly reviewed ROI can also block a prior Keep.
        roi=ns['mntb_roi_layer'];backup=roi.data.copy();roi.data=np.zeros_like(backup);roi.data[:,20:26,20:26]=1
        ns['accept_mntb_roi']();assert not ns['current_keep'].loc[2]
        with pytest.raises(ValueError):panel.decide('keep')
        roi.data=backup;ns['accept_mntb_roi']()
        panel.decide('exclude');panel.undo();assert ns['current_keep'].loc[2]
        panel.decide('exclude')
        if zero_retained:
            panel.label.setValue(7);panel.decide('exclude')
        assert ns['current_keep'].sum()==(0 if zero_retained else 1)
        np.testing.assert_array_equal(ns['masks'],original)
        ns['QFileDialog']=SimpleNamespace(getExistingDirectory=lambda *a:str(tmp_path))
        session.export();folder=ns['sample_export_directory']
        saved=json.loads((folder/'analysis_config.json').read_text())
        assert saved['manual_review']['decisions']['2']['decision']=='exclude'
        exported=tifffile.imread(folder/'filtered_masks_syglass.tif');assert 2 not in np.unique(exported)
        rows=pd.read_csv(folder/'manual_mask_review.csv').set_index('mask_id')
        assert rows.loc[2,'manual_decision']=='exclude'
        resumed=AnalysisSession(cfg,image,folder,log=lambda *_:None).prepare()
        other=model_viewer()
        try:
            resumed.attach(other);resumed.ns['accept_mntb_roi']()
            assert resumed.ns['current_keep'].equals(ns['current_keep'])
            assert resumed.ns['manual_review'].decisions==ns['manual_review'].decisions
        finally:other.close()
    finally:viewer.close()
