"""Independent NuCytoSeg QMainWindow composition."""
from __future__ import annotations

from qtpy.QtCore import Qt
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
from .napari_embedding import DISPLAY_NAME, EmbeddedNapariWidget
from .viewer_toolbar import ViewerToolbar
from .workflow_navigation import WorkflowNavigation
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
        self.setMinimumSize(960, 640)
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

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("workspaceSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.navigation)
        self.splitter.addWidget(viewer_workspace)
        self.splitter.addWidget(self.context_container)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([210, 950, 280])
        self.setCentralWidget(self.splitter)

        self.navigation.workflow_changed.connect(self.set_workflow)
        self.embedding.status_changed.connect(self.statusBar().showMessage)
        self.viewer_toolbar.status_changed.connect(self.statusBar().showMessage)
        self.layer_panel.active_layer_changed.connect(self._update_active_layer_status)
        self.embedding.viewer.dims.events.current_step.connect(self._update_z_status)
        self._create_actions()
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
        container.setMinimumWidth(220)
        container.setMaximumWidth(480)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 8, 4)
        title = QLabel("Parameters")
        title.setObjectName("panelTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        self.context_collapse_button = QToolButton()
        self.context_collapse_button.setText("×")
        self.context_collapse_button.setToolTip("Hide contextual parameters")
        self.context_collapse_button.clicked.connect(self.hide_context_panel)
        header_layout.addWidget(self.context_collapse_button)
        layout.addWidget(header)
        layout.addWidget(self.context_panel, 1)
        layer_title = QLabel("Layers")
        layer_title.setObjectName("panelTitle")
        layer_title.setContentsMargins(12, 6, 0, 0)
        layout.addWidget(layer_title)
        self.layer_panel = LayerPanel(self.embedding.viewer)
        layout.addWidget(self.layer_panel, 1)
        return container

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
        self.focus_action.setCheckable(True)
        self.focus_action.setShortcut("Ctrl+Shift+F")
        self.focus_action.toggled.connect(self.set_focus_mode)

        self.restore_layout_action = QAction("Restore Previous Layout", self)
        self.restore_layout_action.triggered.connect(self.restore_previous_layout)

        self.reset_layout_action = QAction("Reset Layout", self)
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
        self.splitter.setSizes([210, 950, 280])
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
            QMainWindow, QWidget { background: #111821; color: #d9e3ee; }
            QMenuBar, QMenu, QStatusBar { background: #151e29; color: #d9e3ee; }
            QMenuBar::item:selected, QMenu::item:selected { background: #263b50; }
            #workflowNavigation { background: #151e29; border-right: 1px solid #253342; }
            #navigationBrand, #panelTitle { font-size: 15px; font-weight: 700; color: #f2f7fb; }
            QToolButton { background: transparent; border: 0; border-radius: 6px; padding: 6px; }
            QToolButton:hover { background: #223142; }
            QToolButton:checked { background: #234963; color: #a9e2ff; }
            #contextContainer { background: #151e29; border-left: 1px solid #253342; }
            #layerTree { background: #111821; border: 0; alternate-background-color: #151e29; }
            #layerTree::item { min-height: 25px; }
            #layerTree::item:selected { background: #234963; }
            #contextSection { background: #1c2734; border-radius: 6px; padding: 10px; font-weight: 600; }
            #placeholderNote { color: #8fa1b3; padding: 8px 2px; }
            QSplitter::handle { background: #253342; width: 2px; }
            QStatusBar { border-top: 1px solid #253342; }
            """
        )

    def closeEvent(self, event) -> None:
        if not self._closed:
            self._closed = True
            self.save_layout()
            self.embedding.viewer.dims.events.current_step.disconnect(self._update_z_status)
            self.layer_panel.dispose()
            self.viewer_toolbar.dispose()
            self.embedding.shutdown()
        event.accept()
