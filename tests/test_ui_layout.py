from qtpy.QtWidgets import QPushButton,QMenu,QApplication
from test_workflow import model_viewer
from nuclear_segmentation.ui import Launcher
from nuclear_segmentation.ui_layout import button_action,pin_live_summary
from magicgui.widgets import Container,Label,PushButton,FloatSlider


def test_menu_obeys_button_enabled_state():
    app=QApplication.instance() or QApplication([])
    button=QPushButton('Export'); menu=QMenu(); calls=[]
    button.clicked.connect(lambda: calls.append(1))
    button.setEnabled(False);action=button_action(menu,button)
    assert not action.isEnabled()
    button.setEnabled(True);menu.aboutToShow.emit();assert action.isEnabled()
    action.trigger();assert calls==[1]


def test_launcher_and_pinned_summary():
    viewer=model_viewer()
    launcher=Launcher(viewer)
    assert launcher.tabs.currentIndex()==0
    before=launcher.get_config()
    for i in range(5):launcher.tabs.setCurrentIndex(i)
    assert launcher.get_config()==before
    keys=['count_label','guard_count_label','roi_volume_label','density_label','status_label']
    ns={key:Label(value=key) for key in keys}
    ns['roi_review_button']=PushButton(text='Accept ROI')
    slider=FloatSlider(name='volume')
    ns['filter_panel']=Container(widgets=[slider]+list(ns.values()))
    ns['filter_dock_widget']=viewer.window.add_dock_widget(ns['filter_panel'])
    pin_live_summary(viewer,ns)
    assert len(ns['filter_panel'])==1
    assert ns['filter_panel'][0] is slider
    assert ns['filter_summary'][0] is ns['count_label']
    ns['count_label'].value='Masks kept: 7 / 12'
    assert ns['filter_summary'][0].value=='Masks kept: 7 / 12'
    viewer.close()
