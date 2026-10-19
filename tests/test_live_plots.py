import numpy as np
import pandas as pd
from qtpy.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from qtpy.QtGui import QFont
from qtpy.QtTest import QTest
from nuclear_segmentation.live_plots import LivePlots
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
