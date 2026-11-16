"""Independent NuCytoSeg QMainWindow composition."""
from __future__ import annotations

from qtpy.QtCore import QSize, Qt
from qtpy.QtGui import QAction, QKeySequence
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .context_panel import ContextPanel
from .layer_panel import LayerPanel
from .mask_selection import MaskSelectionController
from .napari_embedding import DISPLAY_NAME, EmbeddedNapariWidget
from .selected_mask_panel import SelectedMaskPanel
from .viewer_toolbar import ViewerToolbar
from .workflow_navigation import WorkflowNavigation
from .ui_icons import icon
from .workspace_settings import (
    WorkspaceSettings,
    WorkspaceSnapshot,
    geometry_intersects_screen,
    move_to_available_screen,
)


class DesktopWindow(QMainWindow):
    """Phase 1B application shell; scientific workflows are intentionally absent."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        load_demo: bool = False,
        qt_viewer_factory=None,
        settings=None,
        restore_workspace: bool = True,
    ):
        super().__init__(parent)
        self.setObjectName("nucytosegDesktopWindow")
        self.setWindowTitle(f"{DISPLAY_NAME} — Scientific Image Analysis")
        self.setMinimumSize(1024, 680)
        self.resize(1440, 900)
        self._closed = False
        self._focus_mode = False
        self._focus_snapshot = None
        self.workspace_settings = WorkspaceSettings(settings)

        self.navigation = WorkflowNavigation()
        self.embedding = EmbeddedNapariWidget(
            load_demo=load_demo,
            qt_viewer_factory=qt_viewer_factory,
            show_controls=False,
        )
        self.viewer_toolbar = ViewerToolbar(self.embedding.viewer)
        viewer_workspace = QWidget()
        viewer_layout = QHBoxLayout(viewer_workspace)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(0)
        viewer_layout.addWidget(self.viewer_toolbar)
        viewer_layout.addWidget(self.embedding, 1)
        self.context_panel = ContextPanel()
        self.context_container = self._build_context_container()
        self.mask_selection = MaskSelectionController(self.embedding.viewer, self)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("workspaceSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.navigation)
        self.splitter.addWidget(viewer_workspace)
        self.splitter.addWidget(self.context_container)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([210, 890, 340])
        self.navigation.workflow_changed.connect(self.set_workflow)
        self.embedding.status_changed.connect(self.statusBar().showMessage)
        self.viewer_toolbar.status_changed.connect(self.statusBar().showMessage)
        self.viewer_toolbar.tool_changed.connect(self.mask_selection.set_tool)
        self.viewer_toolbar.display_about_to_change.connect(
            self.mask_selection.prepare_display_change
        )
        self.mask_selection.status_changed.connect(self.statusBar().showMessage)
        self.mask_selection.selection_changed.connect(
            self.selected_mask_panel.set_selection
        )
        self.layer_panel.active_layer_changed.connect(self._update_active_layer_status)
        self.embedding.viewer.dims.events.current_step.connect(self._update_z_status)
        self._create_actions()
        root = QWidget()
        root.setObjectName("desktopRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_application_header())
        root_layout.addWidget(self.splitter, 1)
        self.setCentralWidget(root)
        self._create_menus()
        self._create_status_widgets()
        self.set_workflow("project")
        self.statusBar().showMessage("Phase 1B shell ready — no analysis loaded")
        self._apply_theme()
        if restore_workspace:
            self.restore_layout()

    def _build_context_container(self) -> QWidget:
        container = QWidget()
        container.setObjectName("contextContainer")
        container.setMinimumWidth(320)
        container.setMaximumWidth(540)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QWidget()
        header.setObjectName("contextPanelHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 10, 8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        kicker = QLabel("CONTEXT PANEL")
        kicker.setObjectName("panelKicker")
        titles.addWidget(kicker)
        title = QLabel("Parameters")
        title.setObjectName("panelTitle")
        titles.addWidget(title)
        header_layout.addLayout(titles)
        header_layout.addStretch(1)
        self.context_collapse_button = QToolButton()
        self.context_collapse_button.setIcon(icon("collapse"))
        self.context_collapse_button.setIconSize(QSize(22, 22))
        self.context_collapse_button.setToolTip("Hide contextual parameters")
        self.context_collapse_button.clicked.connect(self.hide_context_panel)
        header_layout.addWidget(self.context_collapse_button)
        layout.addWidget(header)
        layout.addWidget(self.context_panel, 1)
        layer_title = QLabel("Layers")
        layer_title.setObjectName("panelTitle")
        layer_title.setContentsMargins(16, 10, 0, 2)
        layout.addWidget(layer_title)
        self.layer_panel = LayerPanel(self.embedding.viewer)
        layout.addWidget(self.layer_panel, 1)
        self.selected_mask_panel = SelectedMaskPanel()
        layout.addWidget(self.selected_mask_panel)
        return container

    def _build_application_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("applicationHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 10, 14, 10)
        layout.setSpacing(12)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        name = QLabel("NuCytoSeg")
        name.setObjectName("applicationName")
        subtitle = QLabel("3D microscopy segmentation and quantitative analysis")
        subtitle.setObjectName("applicationSubtitle")
        brand.addWidget(name)
        brand.addWidget(subtitle)
        layout.addLayout(brand)
        layout.addSpacing(20)

        self.header_workflow = QLabel("PROJECT")
        self.header_workflow.setObjectName("headerWorkflow")
        layout.addWidget(self.header_workflow)
        layout.addStretch(1)

        for action, icon_name, tooltip in (
            (self.focus_action, "focus", "Toggle Focus Mode"),
            (self.restore_layout_action, "restore", "Restore previous layout"),
            (self.reset_layout_action, "reset", "Reset workspace layout"),
        ):
            action.setIcon(icon(icon_name))
            button = QToolButton()
            button.setObjectName("headerActionButton")
            button.setDefaultAction(action)
            button.setIconSize(QSize(22, 22))
            button.setToolTip(tooltip)
            layout.addWidget(button)

        self.open_image_button = QToolButton()
        self.open_image_button.setObjectName("openImageButton")
        self.open_image_button.setText("Open Image")
        self.open_image_button.setIcon(icon("open", accent=True))
        self.open_image_button.setIconSize(QSize(22, 22))
        self.open_image_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.open_image_button.setEnabled(False)
        self.open_image_button.setToolTip(
            "Image import will be connected during scientific workflow integration"
        )
        layout.addWidget(self.open_image_button)
        return header

    def _create_actions(self) -> None:
        self.quit_action = QAction("Quit NuCytoSeg", self)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.quit_action.triggered.connect(self.close)

        self.toggle_navigation_action = QAction("Workflow Navigation", self)
        self.toggle_navigation_action.setCheckable(True)
        self.toggle_navigation_action.setChecked(True)
        self.toggle_navigation_action.setShortcut("Ctrl+Shift+L")
        self.toggle_navigation_action.toggled.connect(self._toggle_navigation)

        self.toggle_context_action = QAction("Contextual Parameters", self)
        self.toggle_context_action.setCheckable(True)
        self.toggle_context_action.setChecked(True)
        self.toggle_context_action.setShortcut("Ctrl+Shift+R")
        self.toggle_context_action.toggled.connect(self._toggle_context)

        self.about_action = QAction("About NuCytoSeg", self)
        self.about_action.triggered.connect(
            lambda: self.statusBar().showMessage(
                "NuCytoSeg Phase 1B — independent Qt6 interface", 5000
            )
        )

        self.focus_action = QAction("Focus Mode", self)
        self.focus_action.setIcon(icon("focus"))
        self.focus_action.setCheckable(True)
        self.focus_action.setShortcut("Ctrl+Shift+F")
        self.focus_action.toggled.connect(self.set_focus_mode)

        self.restore_layout_action = QAction("Restore Previous Layout", self)
        self.restore_layout_action.setIcon(icon("restore"))
        self.restore_layout_action.triggered.connect(self.restore_previous_layout)

        self.reset_layout_action = QAction("Reset Layout", self)
        self.reset_layout_action.setIcon(icon("reset"))
        self.reset_layout_action.triggered.connect(self.reset_layout)

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.quit_action)
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.toggle_navigation_action)
        view_menu.addAction(self.toggle_context_action)
        self.workspace_menu = self.menuBar().addMenu("Workspace")
        self.workspace_menu.addAction(self.focus_action)
        self.workspace_menu.addAction(self.restore_layout_action)
        self.workspace_menu.addAction(self.reset_layout_action)
        self.menuBar().addMenu("Tools")
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction(self.about_action)

    def _create_status_widgets(self) -> None:
        self.workflow_status = QLabel()
        self.layer_status = QLabel("Layer: none")
        self.z_status = QLabel("Z: —")
        for widget in (self.workflow_status, self.layer_status, self.z_status):
            widget.setContentsMargins(8, 0, 8, 0)
            self.statusBar().addPermanentWidget(widget)
        self._update_active_layer_status(self.embedding.viewer.layers.selection.active)
        self._update_z_status()

    def _update_active_layer_status(self, layer) -> None:
        self.layer_status.setText(f"Layer: {layer.name}" if layer is not None else "Layer: none")

    def _update_z_status(self, event=None) -> None:
        steps = self.embedding.viewer.dims.current_step
        self.z_status.setText(f"Z: {steps[0]}" if steps else "Z: —")

    def set_workflow(self, key: str) -> None:
        self.navigation.set_workflow(key)
        key = self.navigation.current_workflow
        self.context_panel.set_workflow(key)
        label = self.context_panel.heading.text()
        if hasattr(self, "workflow_status"):
            self.workflow_status.setText(f"Workflow: {label}")
        if hasattr(self, "header_workflow"):
            self.header_workflow.setText(label.upper())
        self.statusBar().showMessage(f"{label} workspace selected")

    def _toggle_navigation(self, visible: bool) -> None:
        self.navigation.setVisible(visible)

    def _toggle_context(self, visible: bool) -> None:
        self.context_container.setVisible(visible)

    def hide_context_panel(self) -> None:
        self.toggle_context_action.setChecked(False)

    def workspace_snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            geometry=self.saveGeometry(),
            window_state=self.saveState(),
            splitter_sizes=tuple(self.splitter.sizes()),
            navigation_collapsed=self.navigation.collapsed,
            navigation_visible=self.toggle_navigation_action.isChecked(),
            context_visible=self.toggle_context_action.isChecked(),
            workflow=self.navigation.current_workflow,
        )

    def apply_snapshot(self, snapshot: WorkspaceSnapshot) -> bool:
        restored = bool(self.restoreGeometry(snapshot.geometry))
        if restored and not geometry_intersects_screen(self):
            restored = False
        if not restored:
            self.resize(1440, 900)
            move_to_available_screen(self)
        if not snapshot.window_state.isEmpty():
            self.restoreState(snapshot.window_state)
        self.navigation.set_collapsed(snapshot.navigation_collapsed)
        self.toggle_navigation_action.setChecked(snapshot.navigation_visible)
        self.toggle_context_action.setChecked(snapshot.context_visible)
        if len(snapshot.splitter_sizes) == 3 and sum(snapshot.splitter_sizes) > 0:
            self.splitter.setSizes(list(snapshot.splitter_sizes))
        self.set_workflow(snapshot.workflow)
        return restored

    def save_layout(self) -> None:
        if not self._focus_mode:
            self.workspace_settings.save_with_history(self.workspace_snapshot())

    def restore_layout(self) -> bool:
        snapshot = self.workspace_settings.read("current")
        return False if snapshot is None else self.apply_snapshot(snapshot)

    def restore_previous_layout(self) -> bool:
        if self._focus_mode:
            self.focus_action.setChecked(False)
        snapshot = self.workspace_settings.read("previous")
        if snapshot is None:
            self.statusBar().showMessage("No previous workspace layout is available", 4000)
            return False
        self.apply_snapshot(snapshot)
        self.statusBar().showMessage("Previous workspace layout restored", 4000)
        return True

    def reset_layout(self) -> None:
        if self._focus_mode:
            self.focus_action.setChecked(False)
        self.workspace_settings.clear()
        self.showNormal()
        self.resize(1440, 900)
        self.navigation.set_collapsed(False)
        self.toggle_navigation_action.setChecked(True)
        self.toggle_context_action.setChecked(True)
        self.splitter.setSizes([210, 890, 340])
        self.set_workflow("project")
        self.statusBar().showMessage("Default workspace layout restored", 4000)

    def set_focus_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._focus_mode:
            return
        if enabled:
            self._focus_snapshot = self.workspace_snapshot()
            self._focus_mode = True
            self.navigation.set_collapsed(True)
            self.toggle_navigation_action.setChecked(True)
            self.toggle_context_action.setChecked(False)
            self.statusBar().showMessage("Focus Mode active — press Ctrl+Shift+F to exit")
        else:
            self._focus_mode = False
            if self._focus_snapshot is not None:
                snapshot = self._focus_snapshot
                self.navigation.set_collapsed(snapshot.navigation_collapsed)
                self.toggle_navigation_action.setChecked(snapshot.navigation_visible)
                self.toggle_context_action.setChecked(snapshot.context_visible)
                self.splitter.setSizes(list(snapshot.splitter_sizes))
                self.set_workflow(snapshot.workflow)
            self._focus_snapshot = None
            self.statusBar().showMessage("Workspace layout restored")

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, #desktopRoot { background: #0d151e; }
            QWidget { color: #dce7f1; font-size: 13px; }
            QMenuBar, QMenu, QStatusBar { background: #121d29; color: #d9e3ee; }
            QMenuBar { padding: 4px 6px; }
            QMenuBar::item:selected, QMenu::item:selected { background: #263b50; }
            #applicationHeader { background: #121d29; border-bottom: 1px solid #263748; }
            #applicationName { font-size: 20px; font-weight: 700; color: #f4f9fc; }
            #applicationSubtitle { font-size: 11px; color: #8296a9; }
            #headerWorkflow { color: #66d9d0; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
            #openImageButton { background: #17677a; color: #dffcff; border-radius: 7px; padding: 8px 13px; }
            #openImageButton:disabled { background: #20313d; color: #708391; }
            #workflowNavigation { background: #121d29; border-right: 1px solid #263748; }
            #navigationBrand, #panelTitle { font-size: 16px; font-weight: 700; color: #f2f7fb; }
            #panelKicker { font-size: 10px; font-weight: 600; color: #8298aa; letter-spacing: 1px; }
            QToolButton { background: transparent; border: 0; border-radius: 7px; padding: 7px; }
            QToolButton:hover { background: #203343; }
            QToolButton:checked { background: #15546b; color: #bff7f2; }
            QToolButton:disabled { color: #526171; }
            #headerActionButton { background: #192a38; }
            #contextContainer { background: #121d29; border-left: 1px solid #263748; }
            #contextPanelHeader { border-bottom: 1px solid #263748; }
            #contextHeading { font-size: 17px; font-weight: 650; color: #f1f6fa; }
            #selectedMaskPanel { background: #162735; border-top: 1px solid #263748; }
            #selectedMaskId { color: #f2f8fb; font-size: 16px; font-weight: 700; }
            #selectedMaskLayer { color: #94a9bb; font-size: 11px; }
            #layerTree { background: #0f1923; border: 0; alternate-background-color: #151e29; }
            #layerTree QHeaderView::section { background: #162432; color: #94a9bb; border: 0; padding: 7px; font-weight: 600; }
            #layerTree::item { min-height: 34px; padding: 2px; }
            #layerTree::item:hover { background: #1b3040; }
            #layerTree::item:selected { background: #15546b; color: #f2fbfc; }
            #contextSection { background: #1a2a38; border-radius: 7px; padding: 12px; font-weight: 600; }
            #placeholderNote { color: #8fa1b3; padding: 10px 3px; }
            QSplitter::handle { background: #253342; width: 2px; }
            QStatusBar { border-top: 1px solid #263748; font-size: 12px; min-height: 25px; }
            """
        )

    def closeEvent(self, event) -> None:
        if not self._closed:
            self._closed = True
            self.save_layout()
            self.embedding.viewer.dims.events.current_step.disconnect(self._update_z_status)
            self.mask_selection.dispose()
            self.layer_panel.dispose()
            self.viewer_toolbar.dispose()
            self.embedding.shutdown()
        event.accept()
