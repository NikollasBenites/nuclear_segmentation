import numpy as np
import pytest
from nuclear_segmentation.normalization import compare_percentiles, normalize_preview, source_identity, validate_normalization_source
from nuclear_segmentation.config import defaults


def test_padding_does_not_change_roi_percentiles():
    tissue=np.arange(1000,dtype=np.float32).reshape(10,10,10)
    first=compare_percentiles(tissue,np.ones_like(tissue))
    padded=np.pad(tissue,((0,0),(0,10),(0,10)))
    roi=np.pad(np.ones_like(tissue),((0,0),(0,10),(0,10)))
    second=compare_percentiles(padded,roi)
    assert first['roi_lowhigh']==second['roi_lowhigh']
    assert second['full_lowhigh'][0]==0
    assert second['full_lowhigh'][1]<second['roi_lowhigh'][1]
    # Genuine tissue zero remains part of the ROI population.
    assert second['roi_zero_fraction']==.001
    assert second['roi_voxels']==1000
    assert second['roi_lowhigh']==pytest.approx([9.99,989.01])


def test_preview_preserves_original_and_does_not_clip():
    source=np.array([0,10,20,30],dtype=np.float32).reshape(1,2,2)
    original=source.copy()
    result=normalize_preview(source,[10,20])
    np.testing.assert_array_equal(source,original)
    np.testing.assert_allclose(result.ravel(),[-1,0,1,2])
    assert not np.shares_memory(source,result)


@pytest.mark.parametrize('kind',['empty','constant','shape','nan','range'])
def test_invalid_roi_or_data(kind):
    volume=np.arange(24,dtype=float).reshape(2,3,4);roi=np.ones_like(volume)
    if kind=='empty':roi[:]=0
    if kind=='constant':volume[:]=1
    if kind=='shape':roi=roi[:1]
    if kind=='nan':volume[0,0,0]=np.nan
    with pytest.raises(ValueError):compare_percentiles(volume,roi,99 if kind=='range' else 1,99)


def test_source_and_roi_changes_are_rejected(tmp_path):
    path=tmp_path/'image.tif';path.write_bytes(b'image')
    cfg=defaults();volume=np.arange(24).reshape(2,3,4);roi=np.ones_like(volume)
    report=compare_percentiles(volume,roi)
    report['source']=source_identity(path,cfg)
    report['cellpose_normalize']={'normalize':True,'lowhigh':report['roi_lowhigh'],'norm3D':True,'invert':False}
    cfg['CELLPOSE_ROI_NORMALIZATION']=report;cfg['CELLPOSE_NORMALIZE']=report['cellpose_normalize']
    validate_normalization_source(cfg,path,roi)
    roi[0,0,0]=0
    with pytest.raises(ValueError):validate_normalization_source(cfg,path,roi)
    roi[0,0,0]=1;path.write_bytes(b'changed_image')
    with pytest.raises(ValueError):validate_normalization_source(cfg,path,roi)
