"""Compact desktop presentation. Scientific calculations remain in their modules."""
from qtpy.QtCore import Qt, QTimer, QObject, QEvent
from qtpy.QtGui import QAction
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QScrollArea, QSizePolicy, QLabel,
    QPushButton, QAbstractButton, QTabWidget, QMenu, QStyle, QComboBox, QListView, QLineEdit, QToolBar,
)


class _LabelHeight(QObject):
    """Keep wrapped QLabel height in sync with text, font and available width."""
    def __init__(self, label):
        super().__init__(label)
        self.label = label
        self.parent_widget = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.update_height)
        label.installEventFilter(self)
        self.watch_parent()
        self.timer.start(0)

    def watch_parent(self):
        if self.parent_widget is not None:
            try:
                self.parent_widget.removeEventFilter(self)
            except RuntimeError:
                pass  # Qt may already have destroyed a toolbar/container.
        self.parent_widget = self.label.parentWidget()
        if self.parent_widget is not None:
            self.parent_widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        kind = event.type()
        if obj is self.label and kind == QEvent.ParentChange:
            self.watch_parent()
        if kind in (QEvent.Resize, QEvent.LayoutRequest, QEvent.Show,
                    QEvent.FontChange, QEvent.StyleChange, QEvent.ParentChange):
            self.timer.start(0)
        return False

    def update_height(self):
        label = self.label
        if label.wordWrap() and label.width() > 1:
            height = max(0, label.heightForWidth(max(1, label.width())))
            if label.minimumHeight() != height:
                label.setMinimumHeight(height)
                label.updateGeometry()


class _FormWrap(QObject):
    def __init__(self, layout):
        super().__init__(layout)
        self.layout = layout
        self.host = layout.parentWidget()
        if self.host is not None:
            self.host.installEventFilter(self)
        self.update_policy()

    def update_policy(self):
        policy = (QFormLayout.WrapAllRows if self.host is not None and self.host.width() < 440
                  else QFormLayout.WrapLongRows)
        if self.layout.rowWrapPolicy() != policy:
            self.layout.setRowWrapPolicy(policy)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.update_policy()
        return False


def _in_toolbar(widget):
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QToolBar):
            return True
        parent = parent.parentWidget()
    return False


def _compact_layout(layout):
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(4)
    if isinstance(layout, QFormLayout):
        layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        if not hasattr(layout, '_nuclear_wrap'):
            layout._nuclear_wrap = _FormWrap(layout)
        else:
            layout._nuclear_wrap.update_policy()
    for i in range(layout.count()):
        child = layout.itemAt(i).layout()
        if child is not None:
            _compact_layout(child)


def compact(widget):
    """Reduce spacing without imposing a fixed font or display scale."""
    # Replace popup views before enumerating descendants (setView deletes the old view).
    combos = ([widget] if isinstance(widget, QComboBox) else []) + widget.findChildren(QComboBox)
    for combo in combos:
        if not combo.property("nuclear_popup_configured"):
            combo.setView(QListView(combo))
            combo.setStyleSheet(combo.styleSheet() + "\nQComboBox { combobox-popup: 0; }")
            combo.setMaxVisibleItems(16)
            combo.setProperty("nuclear_popup_configured", True)
    for obj in [widget] + widget.findChildren(QWidget):
        layout = obj.layout()
        if layout is not None:
            _compact_layout(layout)
        if isinstance(obj, (QLineEdit, QComboBox)):
            obj.setMinimumWidth(0)
            obj.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        if isinstance(obj, QAbstractButton):
            short = {
                'Use optional OME-Zarr cache': 'Use OME-Zarr cache',
                'Include corrected images in analysis export': 'Export corrected images',
                'Run automatic matching': 'Run matching',
                'Automatic proportions and volumes': 'Class proportions / volumes',
                'Select best overlap candidate': 'Select best overlap',
                'Use corrected channel for segmentation': 'Segment corrected channel',
                'Measure this channel on the corrected image': 'Measure corrected channel',
                'Apply selected correction to analysis': 'Apply correction to analysis',
                'Correct Z of selected layer': 'Correct selected layer in Z',
                'Intensity profiles: include zero pixels': 'Profiles: include zero pixels',
                'Accept reviewed MNTB ROI': 'Accept reviewed ROI',
                'Compare one-to-one matches only': 'One-to-one matches only',
                'Histogram: fraction of each group': 'Histogram: group fractions',
            }
            if obj.text() in short:
                obj.setToolTip(obj.toolTip() or obj.text())
                obj.setText(short[obj.text()])
            if not obj.toolTip():
                obj.setToolTip(obj.text().replace('&', ''))
            obj.setToolTipDuration(15000)
        if isinstance(obj, QLabel) and not _in_toolbar(obj):
            obj.setTextFormat(Qt.PlainText)
            obj.setWordWrap(True)
            obj.setMinimumWidth(0)
            obj.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            if not hasattr(obj, '_nuclear_height'):
                obj._nuclear_height = _LabelHeight(obj)
    # Editors remain resizable; the outer scroll area controls minimum height.
    widget.setMinimumWidth(0)


def scroll(widget):
    if widget.layout() is not None:
        widget.layout().setAlignment(Qt.AlignTop)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    area.setMinimumSize(180, 90)
    area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return area


def fit_window(viewer):
    window = viewer.window._qt_window
    screen = window.screen()
    if screen is not None:
        rect = screen.availableGeometry()
        window.resize(min(1440, rect.width()), min(900, rect.height()))
    window.showMaximized()


def show_dock(dock):
    dock.show()
    dock.raise_()


def menu_for(viewer, title):
    menu = QMenu(title, viewer.window._qt_window)
    menu.setToolTipsVisible(True)
    menu.menuAction().setMenuRole(QAction.MenuRole.NoRole)
    viewer.window._qt_window.menuBar().addMenu(menu)
    return menu


def button_action(menu, button, title=None, icon=None):
    """Reuse the original callback and enabled state, including busy/invalid states."""
    action = QAction(title or button.text(), menu)
    action.setMenuRole(QAction.MenuRole.NoRole)
    menu.addAction(action)
    action.setToolTip(button.toolTip() or button.text())
    if icon is not None:
        action.setIcon(button.style().standardIcon(icon))
        button.setIcon(button.style().standardIcon(icon))
    def refresh():
        action.setEnabled(button.isEnabled())
    menu.aboutToShow.connect(refresh)
    action.triggered.connect(lambda checked=False: button.click())
    refresh()
    return action


def short_button(button, text, help_text=None, icon=None):
    button.setToolTip(help_text or button.toolTip() or button.text())
    button.setText(text)
    button.setToolTipDuration(15000)
    if icon is not None:
        button.setIcon(button.style().standardIcon(icon))


def setup_launcher(launcher):
    compact(launcher)
    # Move static instructions to contextual hover help.
    for label in launcher.findChildren(QLabel):
        text = label.text()
        if text.startswith(('Open a TIFF,', 'Resume/density-only', '3D segmentation uses')):
            if text.startswith('Open a TIFF,'):
                launcher.start.setToolTip(text)
            elif text.startswith('Resume/density-only'):
                launcher.roi_path.setToolTip(text)
                launcher.mode.setToolTip(text)
            else:
                launcher.nominal.setToolTip(text)
                launcher.device.setToolTip(text)
            label.hide()
    launcher.tabs.setUsesScrollButtons(True)
    # Each setup tab scrolls independently.
    for i in range(launcher.tabs.count()):
        widget = launcher.tabs.widget(i)
        if isinstance(widget, QScrollArea):
            continue
        title = launcher.tabs.tabText(i)
        launcher.tabs.removeTab(i)
        wrapped = scroll(widget)
        launcher.tabs.insertTab(i, wrapped, title)
    launcher.tabs.setCurrentIndex(0)
    short_button(launcher.preview_button, 'Load / preview',
                 'Load original layers and the ROI. Open Z correction and ROI normalization before segmentation.',
                 QStyle.SP_DialogOpenButton)
    short_button(launcher.start, 'Start analysis', icon=QStyle.SP_MediaPlay)
    menu = menu_for(launcher.viewer, 'Nuclear Segmentation')
    launcher.app_menu = menu
    from .roi_smoothing_ui import RoiSmoothingMenu
    launcher.roi_smoothing_menu = RoiSmoothingMenu(launcher.viewer, menu.addMenu('ROI'))
    analysis = menu.addMenu('Analysis'); analysis.setToolTipsVisible(True)
    for label, mode in [('New analysis…','segment'),('Resume analysis…','resume'),('Density only…','density_only')]:
        action = analysis.addAction(label)
        def select(checked=False, value=mode):
            launcher.mode.setCurrentIndex(launcher.mode.findData(value))
            launcher.tabs.setCurrentIndex(0)
            if hasattr(launcher, 'setup_dock'): show_dock(launcher.setup_dock)
        action.triggered.connect(select)
    button_action(analysis, launcher.preview_button, 'Load layers / preprocessing…')
    button_action(analysis, launcher.start)
    settings = QMenu('Configuration', menu)
    settings.menuAction().setMenuRole(QAction.MenuRole.NoRole)
    menu.addMenu(settings)
    settings.setToolTipsVisible(True)
    tools = menu.addMenu('Measure'); tools.setToolTipsVisible(True)
    export = menu.addMenu('Export'); export.setToolTipsVisible(True)
    window = menu.addMenu('Panels')
    for b in launcher.findChildren(QPushButton):
        text = b.text()
        if text in ['Load preset','Save preset','Advanced settings']:
            button_action(settings,b,text+'…')
            if text == 'Advanced settings':
                b.show()
                b.setToolTip('Edit the full configuration, including CELLPOSE_DO_3D and CELLPOSE_STITCH_THRESHOLD.')
            else:
                b.hide()
        elif text == 'Open DAPI matching':
            button_action(tools,b,'DAPI matching…');b.hide()
    button_action(tools,launcher.qc_button,'Quality-control plots…')
    button_action(export,launcher.export_button,'Export results…',QStyle.SP_DialogSaveButton)
    button_action(export,launcher.roi_button,'Save ROI…',QStyle.SP_DialogSaveButton)
    for b in [launcher.qc_button,launcher.export_button,launcher.roi_button]: b.hide()
    window.aboutToShow.connect(lambda: populate_panels(window,launcher.viewer))


def populate_panels(menu, viewer):
    from qtpy.QtWidgets import QDockWidget
    menu.clear()
    for dock in viewer.window._qt_window.findChildren(QDockWidget):
        menu.addAction(dock.toggleViewAction())


def setup_preview(preview):
    compact(preview)
    root = menu_for(preview.viewer, 'Image Processing')
    preview.processing_menu = root
    for title, panel in [('Filter',preview.z_panel),('ROI normalization',preview.roi_widget)]:
        menu = root.addMenu(title); menu.setToolTipsVisible(True)
        for b in panel.findChildren(QPushButton):
            text = b.text()
            if b is not preview.apply_button and text.startswith(('Show selected Z profile','Compare P99','Save ', 'Export ', 'Reset ')):
                group = menu
                button_action(group,b)
                b.hide()
    # Keep calculation/apply controls in the panel and diagnostic outputs visible.
    for panel in [preview.z_panel,preview.roi_widget]:
        for label in panel.findChildren(QLabel):
            if label.text().startswith(('Select an original image layer,','Review the MNTB ROI across Z before')):
                panel.setToolTip(label.text());label.hide()
    tips = {
        'percentile':'Percentile of positive pixels in each Z plane used as the reference.',
        'sigma_planes':'Gaussian smoothing of the reference profile, in Z planes. Zero disables smoothing.',
        'min_gain':'Smallest multiplier allowed for a plane.',
        'max_gain':'Largest multiplier allowed for a plane. Limits amplification of dim planes.',
    }
    for key,field in preview.z_panel.fields.items(): field.setToolTip(tips[key])
    short_button(preview.apply_button, 'Apply ROI limits', 'Save the reviewed ROI and apply its intensity limits to segmentation.')
    preview.lower.setToolTip('Lower percentile estimated from all voxels inside the reviewed 3D ROI.')
    preview.upper.setToolTip('Upper percentile estimated from all voxels inside the reviewed 3D ROI.')
    for i in range(preview.tabs.count()):
        w=preview.tabs.widget(i);title=preview.tabs.tabText(i)
        preview.tabs.removeTab(i);preview.tabs.insertTab(i,scroll(w),title)
    preview.tabs.setCurrentIndex(0)


def pin_live_summary(viewer, ns):
    """Keep density/counts above independently scrollable filter controls."""
    from magicgui.widgets import Container
    panel=ns['filter_panel']
    pinned=[]
    for key in ['count_label','guard_count_label','roi_volume_label','density_label','status_label','roi_review_button']:
        w=ns[key];panel.remove(w);pinned.append(w)
    # Keep counts and primary density visible; long details have their own tab.
    summary=Container(widgets=[ns[key] for key in ('count_label', 'density_label', 'roi_review_button')], labels=False)
    root=QWidget();layout=QVBoxLayout(root)
    compact(summary.native);compact(panel.native)
    panel.native.layout().setAlignment(Qt.AlignTop)
    for i in range(panel.native.layout().count()):
        row = panel.native.layout().itemAt(i).widget()
        if row is not None:
            row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    summary_area = scroll(summary.native)
    summary_area.setMinimumHeight(130)
    summary_area.setMaximumHeight(170)
    layout.addWidget(summary_area)
    tabs = QTabWidget(); tabs.setUsesScrollButtons(True)
    tabs.addTab(scroll(panel.native), 'Filters')
    details = QWidget(); details_layout = QVBoxLayout(details)
    for key in ('guard_count_label', 'status_label', 'roi_volume_label'):
        details_layout.addWidget(ns[key].native)
    details_layout.addStretch()
    compact(details)
    tabs.addTab(scroll(details), 'Density')
    # The reusable plot widget reads the current table, never reruns segmentation.
    from .live_plots import LivePlots
    plots = LivePlots(ns)
    tabs.addTab(plots, 'Graphs')
    layout.addWidget(tabs, 1)
    viewer.window.remove_dock_widget(ns['filter_dock_widget'])
    ns['filter_summary']=summary
    ns['analysis_tabs']=tabs
    ns['live_plots']=plots
    ns['filter_dock_widget']=viewer.window.add_dock_widget(root,area='right',name='Filters and density')
    ns['filter_dock_widget'].setMinimumWidth(230)
    ns['roi_review_button'].native.setToolTip('Accept the reviewed ROI for the current density calculation. Editing the ROI requires accepting it again.')
    plots.schedule()


def setup_matching(panel):
    """Rehouse existing controls; keep original callbacks and state objects."""
    old=panel.layout()
    items=[]
    while old.count(): items.append(old.takeAt(0))
    tabs=QTabWidget(); tabs.setUsesScrollButtons(True)
    pages={};layouts={}
    for name in ['Files','Review','Automatic','Split','Merge','Compare']:
        page=QWidget();pages[name]=page;layouts[name]=QVBoxLayout(page)
        tabs.addTab(scroll(page),name)
    panel.review_tabs=tabs
    menus=menu_for(panel.viewer,'DAPI Matching');panel.workspace_menu=menus
    filemenu=menus.addMenu('File'); plots=menus.addMenu('Measure'); views=menus.addMenu('Visualize')
    splitmenu=menus.addMenu('Segment')
    def has_widget(layout, target):
        for i in range(layout.count()):
            item=layout.itemAt(i)
            if item.widget() is target:return True
            if item.layout() is not None and has_widget(item.layout(),target):return True
        return False
    file_buttons=['Load masks','Open saved matching…','Add reference image TIFF…']
    auto_buttons=['Run automatic matching','Automatic proportions and volumes','Select best overlap candidate']
    split_buttons=['Preview MAP2 split from DAPI','Accept MAP2 split','Cancel split preview']
    compare_buttons=['Compare DAPI volumes','Save matching and comparison…']
    for item in items:
        w=item.widget(); lay=item.layout(); group='Review'
        if lay is not None:
            if has_widget(lay,panel.paths[0]):group='Files'
            elif has_widget(lay,panel.match_min):group='Automatic'
        elif w is not None:
            text=w.text() if hasattr(w,'text') else ''
            if w is panel.aligned or text in file_buttons:group='Files'
            elif w is panel.auto_note or text in auto_buttons:group='Automatic'
            elif w in [panel.seed_fraction,panel.split_note] or text in split_buttons or text.startswith('Minimum DAPI fraction inside'):group='Split'
            elif w in panel.merge_widgets:group='Merge'
            elif w in [panel.one,panel.proportions] or text in compare_buttons:group='Compare'
            if w in [panel.details,panel.message,panel.multiple_note]:
                continue # Pinned below tabs, including error and review status.
            if isinstance(w,QPushButton):
                w.setToolTip(text)
                if text in file_buttons:button_action(filemenu,w)
                elif text in split_buttons or w in panel.merge_widgets or text=='Delete selected MAP2 object':button_action(splitmenu,w)
                elif text in ['Automatic proportions and volumes','Compare DAPI volumes']:button_action(plots,w)
                elif text=='Save matching and comparison…':button_action(filemenu,w,icon=QStyle.SP_DialogSaveButton)
                mapping={'Link selected objects':'Link','Mark DAPI as unmatched':'Unmatched',
                    'Mark DAPI as uncertain':'Uncertain','Remove link / Reset review':'Reset review',
                    'Delete selected MAP2 object':'Delete MAP2',
                    'Undo last decision':'Undo','Preview MAP2 split from DAPI':'Preview split',
                    'Accept MAP2 split':'Accept split','Cancel split preview':'Cancel preview'}
                if text in mapping: short_button(w,mapping[text])
                if text=='Undo last decision':w.setIcon(w.style().standardIcon(QStyle.SP_ArrowBack))
            if w is panel.aligned:
                w.setToolTip(text);w.setText('Alignment verified')
        if lay is not None:layouts[group].addLayout(lay)
        elif w is not None:layouts[group].addWidget(w)
    for name,page in pages.items():
        layouts[name].addStretch();compact(page)
    old.addWidget(tabs,1)
    details = QWidget(); details_layout = QVBoxLayout(details)
    for w in [panel.details,panel.multiple_note,panel.message]:
        details_layout.addWidget(w)
    details_layout.addStretch()
    compact(details)
    panel.details_scroll = scroll(details)
    panel.details_scroll.setMaximumHeight(180)
    old.addWidget(panel.details_scroll)
    for name in ['All','MATCH','AMBIGUOUS','NO_MATCH']:
        a=views.addAction('Show '+name)
        a.triggered.connect(lambda checked=False,n=name: panel.auto_group.setCurrentText(n))
    a=views.addAction('Multiple associations');a.setCheckable(True)
    a.setChecked(panel.multiple.isChecked())
    a.toggled.connect(panel.multiple.setChecked);panel.multiple.toggled.connect(a.setChecked)
    panel.auto_note.setToolTip(panel.auto_note.text())
    panel.match_min.setToolTip('Minimum fraction of a DAPI object overlapping its best MAP2 candidate.')
    panel.second_max.setToolTip('Maximum allowed fraction overlapping the runner-up MAP2 candidate.')
    panel.seed_fraction.setToolTip('Minimum fraction of a DAPI mask inside the selected MAP2 required to seed a split.')
    compact(panel)
