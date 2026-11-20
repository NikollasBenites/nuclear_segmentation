import numpy as np
import pandas as pd
from qtpy.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from qtpy.QtGui import QFont
from qtpy.QtTest import QTest
from nuclear_segmentation.live_plots import LivePlots, ProjectLivePlots
from nuclear_segmentation.project_state import (
    ChannelDescriptor, ImportedDataset, ProjectState,
)
from nuclear_segmentation.ui_layout import compact, scroll


def flush():
    for _ in range(15): QApplication.processEvents()


def test_wrapped_labels_grow_after_text_and_font_changes():
    app=QApplication.instance() or QApplication([])
    host=QWidget();layout=QVBoxLayout(host)
    label=QLabel('short');layout.addWidget(label);compact(host)
    area=scroll(host);area.resize(300,180);area.show();flush()
    for size in (10,14):
        label.setFont(QFont('DejaVu Sans',size))
        label.setText('\n'.join(['Long density and ROI description with µm³ and several values']*12))
        flush()
        assert label.height() >= label.heightForWidth(label.width())
        assert area.verticalScrollBar().maximum() > 0
    area.close()


def test_live_plot_debounce_empty_selection_and_cached_z():
    app=QApplication.instance() or QApplication([])
    props=pd.DataFrame({'volume_um3':[2.,4.,8.], 'sphericity':[.2,.5,.8],
                        'mean_intensity_DAPI':[10.,20.,30.]},index=[2,7,10])
    ns={'filter_properties':props,'current_keep':pd.Series([True,True,False],index=props.index),
        'segmentation_volume':np.arange(48).reshape(3,4,4), 'INTENSITY_MODE':'raw'}
    widget=LivePlots(ns);widget.resize(400,500);widget.show();QTest.qWait(450)
    assert widget.last_retained_ids==[2,7]
    before=widget.render_count
    for _ in range(5): widget.schedule()
    ns['current_keep'].loc[2]=False
    QTest.qWait(450)
    assert widget.render_count==before+1 and widget.last_retained_ids==[7]
    widget.kind.setCurrentText('Intensity');QTest.qWait(450)
    assert widget.column.currentData()=='mean_intensity_DAPI'
    ns['current_keep'][:]=False;widget.schedule();QTest.qWait(450)
    assert widget.last_retained_ids==[] and 'Retained: 0' in widget.status.text()
    ns['current_keep']=None;widget.schedule();QTest.qWait(450)
    assert 'unavailable' in widget.status.text() and len(widget.axes.patches)==0
    ns['current_keep']=pd.Series(True,index=props.index)
    widget.kind.setCurrentText('Z profile');QTest.qWait(600)
    np.testing.assert_allclose(widget.z_profile, np.percentile(ns['segmentation_volume'],[50,99],axis=(1,2)).T)
    profile=widget.z_profile.copy();widget.schedule();QTest.qWait(450)
    assert len(widget.z_profile)==3
    np.testing.assert_allclose(widget.z_profile,profile)
    widget.hide();before=widget.render_count;widget.schedule();QTest.qWait(400)
    assert widget.render_count==before
    widget.show();QTest.qWait(450);assert widget.render_count>before
    widget.dispose();before=widget.render_count;widget.schedule();QTest.qWait(400)
    assert widget.render_count==before
    widget.close()


def _project_state(tmp_path, *, markers=True):
    data = np.arange(2 * 3 * 4 * 5, dtype=np.uint16).reshape(2, 3, 4, 5)
    channels = (
        ChannelDescriptor(0, 'DNA channel', 'DNA channel', 'segmentation'),
        ChannelDescriptor(1, 'Marker channel', 'Marker channel'),
    )
    dataset = ImportedDataset(
        source_path=tmp_path / 'qc.ome.tif', source_format='OME-TIFF',
        series_index=0, series_identifier='0', series_name='Image',
        reported_axes='CZYX', reported_shape=data.shape,
        standardized_shape=data.shape, dtype=str(data.dtype),
        data_czyx=data, voxel_spacing_zyx_um=(2., .5, .5),
        calibration_source='OME', channels=channels,
    )
    state = ProjectState(); state.set_dataset(dataset)
    masks = np.zeros(data.shape[1:], np.uint16)
    masks[:, :2, :2] = 1; masks[:, 2:, 3:] = 2
    model_input = data[0].view(); model_input.setflags(write=False)
    state.set_segmentation_result({
        'masks': masks, 'mask_shape': masks.shape, 'object_count': 2,
        'segmentation_channel': 'DNA channel', 'model_input_volume': model_input,
        'completed_at_utc': 'qc-test', 'z_corrections': {},
    })
    props = pd.DataFrame({
        'volume_um3': [8., 16.], 'sphericity': [.4, .8],
        'mean_intensity_DNA_channel': [10., 30.],
        'marker_ring_positive_fraction': [.2, .9],
    }, index=[1, 2])
    settings = {
        'minimum_volume_um3': 10., 'minimum_sphericity': .5,
        'first_tissue_z': 0, 'last_tissue_z': 2,
        'perinuclear_filters': ({'Marker': {
            'column': 'marker_ring_positive_fraction',
            'minimum_positive_fraction': .5,
        }} if markers else {}),
        'intensity_filters': {'DNA channel': {
            'column': 'mean_intensity_DNA_channel', 'minimum': 5., 'maximum': 40.,
        }},
    }
    state.set_filtering_result({
        'properties': props, 'retained_ids': (2,), 'settings': settings,
        'filtered_masks': np.where(masks == 2, masks, 0),
        'intensity_definitions': [{
            'index': 0, 'name': 'DNA channel',
            'column': 'mean_intensity_DNA_channel',
        }],
        'perinuclear_definitions': ([{
            'name': 'Marker', 'column': 'marker_ring_positive_fraction',
        }] if markers else []),
    })
    return state, masks, props


def test_project_live_plots_reuses_measurements_and_updates_selection(tmp_path):
    app = QApplication.instance() or QApplication([])
    state, masks, props = _project_state(tmp_path)
    original_masks = state.segmentation_result['masks'].copy()
    widget = ProjectLivePlots(state); widget.resize(500, 520); widget.show()
    QTest.qWait(450)
    assert widget.last_retained_ids == [2]
    assert len(widget.axes.patches) > 0

    widget.kind.setCurrentText('Sphericity'); QTest.qWait(450)
    assert len(widget.axes.collections) == 2
    widget.kind.setCurrentText('Intensity'); QTest.qWait(450)
    assert widget.column.currentText() == 'DNA channel'
    assert widget.column.currentData() == 'mean_intensity_DNA_channel'
    widget.kind.setCurrentText('Perinuclear'); QTest.qWait(450)
    assert widget.column.currentText() == 'Marker'

    before = widget.render_count
    updated = dict(state.filtering_result, retained_ids=(1, 2))
    state.set_filtering_result(updated)
    QTest.qWait(450)
    assert widget.render_count == before + 1
    assert widget.last_retained_ids == [1, 2]
    assert state.filtering_result['properties'] is props
    np.testing.assert_array_equal(state.segmentation_result['masks'], original_masks)

    widget.kind.setCurrentText('Z profile'); QTest.qWait(600)
    expected = np.percentile(
        state.segmentation_result['model_input_volume'], [50, 99], axis=(1, 2)
    ).T
    np.testing.assert_allclose(widget.z_profile, expected)
    cached = np.array(widget.z_profile)
    state.set_filtering_result(dict(state.filtering_result, retained_ids=(2,)))
    QTest.qWait(450)
    np.testing.assert_allclose(widget.z_profile, cached)

    state.invalidate_filtering(); QTest.qWait(450)
    assert 'outdated' in widget.status.text().lower()
    state.clear_project(); QTest.qWait(450)
    assert 'no current segmentation' in widget.status.text().lower()
    widget.dispose()
    assert not widget.timer.isActive() and not widget.z_timer.isActive()
    widget.close()


def test_project_live_plots_handles_missing_markers(tmp_path):
    app = QApplication.instance() or QApplication([])
    state, _, _ = _project_state(tmp_path, markers=False)
    widget = ProjectLivePlots(state); widget.resize(500, 520); widget.show()
    widget.kind.setCurrentText('Perinuclear'); QTest.qWait(450)
    assert widget.column.count() == 0
    assert 'Perinuclear markers: Not configured' in widget.status.text()
    widget.dispose(); widget.close()
