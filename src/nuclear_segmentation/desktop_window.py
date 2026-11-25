"""Independent NuCytoSeg QMainWindow composition."""
from __future__ import annotations

from dataclasses import replace
from functools import partial
from pathlib import Path
import logging

from qtpy.QtCore import QSignalBlocker, QSize, Qt, QThread, QTimer
from qtpy.QtGui import QAction, QKeySequence
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .collapsible_section import CollapsibleSection
from .context_panel import ContextPanel
from .design_system import THEMES, apply_theme
from .crop_workspace import CropWorkspaceController
from .image_import import TiffImportWorker, inspect_tiff
from .image_display_panel import ImageDisplayPanel
from .image_preparation_workspace import ImagePreparationController
from .image_export_workspace import ImageExportController
from .import_loading import ImportLoadingDialog
from .layer_panel import LayerPanel
from .legacy_import import import_legacy_analysis
from .lif_import import LifEnumerationWorker, LifImportWorker
from .mask_context_menu import MaskContextMenu
from .mask_selection import MaskSelectionController
from .native_project import VERSION, ProjectWorker, open_project, save_project, snapshot_state
from .napari_embedding import DISPLAY_NAME, EmbeddedNapariWidget
from .normalization_workspace import NormalizationController
from .selected_mask_panel import SelectedMaskPanel
from .project_state import ProjectState
from .series_browser import ImageSeriesBrowser
from . import association_checkpoint
from .association_panel import AssociationPanel
from .matching_panel import MatchingDisplay
from .session_display import SessionMaskDisplay
from .segmentation_execution import SegmentationController
from .filtering_workspace import FilteringController
from .viewer_toolbar import ViewerToolbar
from .geometry_panel import GeometryPanel
from .results_workspace import GraphWindow, ResultsWorkspace
from .workflow_progress import statuses
from .workflow_navigation import WorkflowNavigation
from .ui_icons import icon
from .workspace_settings import (
    WorkspaceSettings,
    WorkspaceSnapshot,
    geometry_intersects_screen,
    move_to_available_screen,
)
from .z_correction_workspace import ZCorrectionController


class DesktopWindow(QMainWindow):
    """Independent application shell with Phase 2A project import."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        load_demo: bool = False,
        qt_viewer_factory=None,
        settings=None,
        restore_workspace: bool = True,
        bioimage_factory=None,
    ):
        super().__init__(parent)
        self.setObjectName("nucytosegDesktopWindow")
        self.setWindowTitle(f"{DISPLAY_NAME} — Scientific Image Analysis")
        self.setMinimumSize(1024, 680)
        self.resize(1440, 900)
        self._closed = False
        self._focus_mode = False
        self._focus_snapshot = None
        self._import_thread = None
        self._import_worker = None
        self._project_job = None
        self.project_directory = None
        self._project_revision = None
        self._project_version = None
        self.project_dirty = False
        self._pending_import_path: Path | None = None
        self._pending_lif_series = None
        self._loading_dialog = None
        self.import_diagnostics = []
        self._load_after_worker_finished = False
        self._close_when_import_finishes = False
        self._bioimage_factory = bioimage_factory
        self._syncing_channels = False
        self._channel_layer_callbacks = {}
        self.workspace_settings = WorkspaceSettings(settings)
        self.project_state = ProjectState(self)

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
        viewer_layout.addWidget(self.embedding, 1)
        self.context_panel = ContextPanel(self.project_state)
        self._default_session_settings = self.context_panel.segmentation_page.pending_config()
        self.graph_window = GraphWindow(self)
        self.graph_window.setWindowTitle("NuCytoSeg — Quality Control")
        self.graph_window.resize(780, 620)
        QVBoxLayout(self.graph_window)
        self.context_panel.show_graphs_button.clicked.connect(lambda: self.results.detach_graph())
        self.context_panel.matching_page.graphs.clicked.connect(self._show_matching_graphs)
        self.context_container = self._build_context_container()
        self.mask_selection = MaskSelectionController(self.embedding.viewer, self)
        self.crop_workspace = CropWorkspaceController(
            self.embedding.viewer,
            self.project_state,
            self.context_panel.preprocessing_page,
            self,
        )
        self.crop_workspace.working_dataset_changed.connect(
            self._display_working_dataset
        )
        self.crop_workspace.working_dataset_changed.connect(
            self.context_panel.segmentation_page.refresh
        )
        self.crop_workspace.status_changed.connect(self.statusBar().showMessage)
        self.normalization = NormalizationController(
            self.embedding.viewer,
            self.project_state,
            self.context_panel.preprocessing_page,
            self,
        )
        self.normalization.status_changed.connect(self.statusBar().showMessage)
        self.z_correction = ZCorrectionController(
            self.embedding.viewer,
            self.project_state,
            self.context_panel.preprocessing_page.z_correction_panel,
            self,
        )
        self.z_correction.status_changed.connect(self.statusBar().showMessage)
        self.image_preparation = ImagePreparationController(
            self.embedding.viewer,
            self.project_state,
            self.context_panel.export_page.image_preparation_panel,
            self,
        )
        self.image_preparation.status_changed.connect(self.statusBar().showMessage)
        self.image_export = ImageExportController(
            self.project_state,
            self.image_preparation,
            self.context_panel.export_page,
            self.context_panel.preprocessing_page,
            self,
        )
        self.image_export.status_changed.connect(self.statusBar().showMessage)
        self.segmentation = SegmentationController(
            self.embedding.viewer,
            self.project_state,
            self.context_panel.segmentation_page,
            conflict_widgets=(
                self.context_panel.project_page,
                self.context_panel.preprocessing_page,
                self.context_panel.export_page,
            ),
            conflict_operations=(
                self.crop_workspace,
                self.normalization,
                self.z_correction,
                self.image_preparation,
                self.image_export,
            ),
            parent=self,
        )
        self.segmentation.status_changed.connect(self.statusBar().showMessage)
        self.context_panel.segmentation_page.mask_review_requested.connect(
            self._show_mask_review
        )
        self.filtering = FilteringController(
            self.embedding.viewer,
            self.project_state,
            self.context_panel.mask_review_page,
            self.context_panel.segmentation_page.scientific_config,
            self,
            conflict_operations=(self.segmentation,),
        )
        self.segmentation.conflict_operations += (self.filtering,)
        self.filtering.conflict_operations += (self.image_export,)
        self.image_export.bind_filtering(self.filtering)
        self.filtering.status_changed.connect(self.statusBar().showMessage)
        self.project_state.scientific_channels_changed.connect(self.normalization.invalidate)
        self.project_state.calibration_changed.connect(
            lambda spacing, source: self.normalization.invalidate()
        )
        self.project_state.calibration_changed.connect(
            lambda spacing, source: self.z_correction.invalidate_all()
        )
        self.crop_workspace.working_dataset_changed.connect(
            lambda dataset: self.normalization.invalidate()
        )
        self.crop_workspace.working_dataset_changed.connect(
            lambda dataset: self.z_correction.invalidate_all()
        )
        self.crop_workspace.working_dataset_changed.connect(
            lambda dataset: self.image_preparation.invalidate()
        )
        self.project_state.z_correction_changed.connect(self.image_preparation.invalidate)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("workspaceSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.navigation)
        self.splitter.addWidget(viewer_workspace)
        self.splitter.addWidget(self.context_container)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([255, 835, 350])
        self.navigation.workflow_changed.connect(self.set_workflow)
        self.embedding.status_changed.connect(self.statusBar().showMessage)
        self.viewer_toolbar.status_changed.connect(self.statusBar().showMessage)
        self.viewer_toolbar.tool_changed.connect(self.mask_selection.set_tool)
        self.viewer_toolbar.display_about_to_change.connect(
            self.mask_selection.prepare_display_change
        )
        self.viewer_toolbar.display_about_to_change.connect(
            self.crop_workspace.prepare_display_change
        )
        self.viewer_toolbar.display_about_to_change.connect(
            self.normalization.prepare_display_change
        )
        self.mask_selection.status_changed.connect(self.statusBar().showMessage)
        review_panel = self.context_panel.mask_review_page
        self.mask_display = SessionMaskDisplay(self.embedding.viewer, self.project_state, self.mask_selection, self)
        self.matching_display = MatchingDisplay(
            self.embedding.viewer, self.project_state, self.context_panel.matching_page, self.mask_display, self,
        )
        self.matching_display.legend_changed.connect(lambda text: self.navigation.set_legend("matching", text))
        self.mask_display.status.connect(review_panel.set_visualization_status)
        self.mask_display.changed.connect(self._mark_project_dirty)
        self.mask_display.changed.connect(self._release_comparisons)
        self.mask_selection.selection_changed.connect(self.mask_display.selection_changed)
        self.mask_selection.selection_cleared.connect(self.mask_display.selection_cleared)
        self.filtering.display_updated.connect(self._sync_mask_display)
        self.project_state.segmentation_result_changed.connect(self._sync_mask_display)
        review_panel.show_raw_requested.connect(lambda: self.mask_display.show("raw"))
        review_panel.show_all_requested.connect(lambda: self.mask_display.show("filtered"))
        review_panel.show_selected_requested.connect(lambda: self.mask_display.show("selected"))
        review_panel.clear_selection_requested.connect(self.mask_selection.clear_selection)
        self.layer_panel.active_layer_changed.connect(self._update_active_layer_status)
        self.context_panel.project_page.open_requested.connect(self.open_image_dialog)
        self.context_panel.project_page.load_pending_requested.connect(
            self.load_pending_image
        )
        self.context_panel.project_page.channel_panel.change_requested.connect(
            self._update_channel_configuration
        )
        self.context_panel.project_page.calibration_requested.connect(
            self._apply_manual_calibration
        )
        self.context_panel.project_page.close_requested.connect(self.close_project)
        self.embedding.viewer.dims.events.current_step.connect(self._update_z_status)
        self._create_actions()
        self.geometry_panel = GeometryPanel(self, review_panel)
        self.association_panel = AssociationPanel(self)
        review_panel.proofreading_page.layout().insertWidget(1, self.association_panel)
        self.project_state.association_review_changed.connect(self._mark_project_dirty)
        self.project_state.matching_result_changed.connect(self._mark_project_dirty)
        self.context_panel.matching_page.candidate_explorer.review.clicked.connect(self.geometry_panel.handoff_merge)
        self.context_panel.matching_page.explorer.review.clicked.connect(self.geometry_panel.guided.handoff)
        self.mask_context_menu = MaskContextMenu(self)
        self.mask_selection.selection_changed.connect(self._mask_selection_changed)
        self.segmentation.conflict_operations += (self.geometry_panel,)
        self.filtering.conflict_operations += (self.geometry_panel,)
        self._connect_project_actions()
        project_panel = self.context_panel.project_page
        project_panel.session_selected.connect(self._select_session)
        project_panel.session_creation_requested.connect(self._create_session)
        state = self.project_state
        state.active_session_about_to_change.connect(self._capture_session_controls)
        state.active_session_changed.connect(self._display_active_session)
        state.changed.connect(self._update_session_title)
        state.sessions_changed.connect(self._update_session_title)
        state.active_session_changed.connect(self._update_session_title)
        self.context_panel.project_page.availability_changed.connect(
            self.close_project_action.setEnabled
        )
        root = QWidget()
        root.setObjectName("desktopRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_application_header())
        root_layout.addWidget(self.navigation.stage_bar)
        self.results = ResultsWorkspace(self)
        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter.addWidget(self.splitter)
        self.vertical_splitter.addWidget(self.results)
        self.vertical_splitter.setChildrenCollapsible(False)
        self.vertical_splitter.setStretchFactor(0, 1)
        self.results_height = 210
        self.results.hide()
        root_layout.addWidget(self.vertical_splitter, 1)
        self.setCentralWidget(root)
        self._create_menus()
        self._create_status_widgets()
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.timeout.connect(self._refresh_progress)
        # Changes to validity and running operations refresh the navigator in place.
        for signal in (state.changed, state.sessions_changed, state.active_session_changed,
                       state.filtering_result_changed, state.segmentation_result_changed,
                       state.quantification_result_changed, state.calibration_changed,
                       state.normalization_changed, state.z_correction_changed,
                       state.matching_result_changed, state.matching_selection_changed,
                       state.inactive_session_validity_changed, state.import_status_changed,
                       self.matching_display.execution.changed, self.segmentation.status_changed,
                       self.filtering.status_changed, self.image_export.status_changed,
                       self.normalization.status_changed, self.z_correction.status_changed,
                       self.crop_workspace.status_changed):
            signal.connect(self._refresh_progress)
        self.set_workflow("project")
        self.statusBar().showMessage("NuCytoSeg ready — open an image to begin")
        self.set_theme(self.workspace_settings.settings.value("appearance/theme", "Dark"))
        if restore_workspace:
            self.restore_layout()

    def _show_matching_graphs(self):
        self.context_panel.quality_control_page.category.setCurrentText("Matching")
        self.results.detach_graph()

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
        self.context_title = QLabel()
        self.context_title.setObjectName("panelTitle")
        self.context_title.setWordWrap(True)
        titles.addWidget(self.context_title)
        header_layout.addLayout(titles)
        header_layout.addStretch(1)
        self.context_collapse_button = QToolButton()
        self.context_collapse_button.setIcon(icon("collapse"))
        self.context_collapse_button.setIconSize(QSize(22, 22))
        self.context_collapse_button.setToolTip("Hide contextual parameters")
        self.context_collapse_button.clicked.connect(self.hide_context_panel)
        header_layout.addWidget(self.context_collapse_button)
        layout.addWidget(header)
        self.review_mode = QComboBox()
        self.review_mode.setAccessibleName("Review and proofreading mode")
        self.review_mode.addItems(["Associations", "Geometry Editing"])
        self.review_mode.activated.connect(lambda i: self.set_workflow("proofreading" if i else "matching"))
        layout.addWidget(self.review_mode)
        layout.addWidget(self.navigation.subpages)
        layout.addWidget(self.context_panel, 1)
        tools = self.navigation.tools
        self.tool_section = CollapsibleSection("viewer_tools", "Viewer tools")
        tool_layout = QVBoxLayout()
        tool_layout.setContentsMargins(2, 2, 2, 2)
        tool_layout.addWidget(self.viewer_toolbar)
        self.tool_section.set_content_layout(tool_layout)
        tools.insertWidget(0, self.tool_section)
        self.layers_section = CollapsibleSection("layers", "Layers")
        self.layer_panel = LayerPanel(self.embedding.viewer)
        layers_layout = QVBoxLayout()
        layers_layout.setContentsMargins(0, 0, 0, 0)
        self.working_mask_status = QLabel()
        self.working_mask_status.setWordWrap(True)
        self.working_mask_status.setAccessibleName("Working mask scientific status")
        layers_layout.addWidget(self.working_mask_status)
        layers_layout.addWidget(self.layer_panel)
        self.layers_section.set_content_layout(layers_layout)
        tools.insertWidget(1, self.layers_section, 1)
        self.image_display_panel = ImageDisplayPanel(
            self.embedding.viewer, self.project_state
        )
        tools.insertWidget(2, self.image_display_panel)
        self.selected_mask_panel = SelectedMaskPanel()
        self.pair_appearance = next(section for section in self.context_panel.matching_page.findChildren(CollapsibleSection)
                                    if section.key == "visualization")
        self.pair_appearance.header.setText("Association display")
        self.pair_appearance.set_expanded(False)
        tools.insertWidget(3, self.pair_appearance)
        tools.addWidget(self.selected_mask_panel)
        tools.addStretch(1)
        return container

    def _build_application_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("applicationHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        name = QLabel("NuCytoSeg")
        name.setObjectName("applicationName")
        subtitle = self.header_project = QLabel("No project open")
        subtitle.setTextFormat(Qt.TextFormat.PlainText)
        subtitle.setMaximumWidth(200)
        subtitle.setObjectName("applicationSubtitle")
        brand.addWidget(name)
        brand.addWidget(subtitle)
        layout.addLayout(brand)


        self.header_workflow = QLabel("PROJECT")
        self.header_workflow.setObjectName("headerWorkflow")
        self.header_workflow.hide()
        self.header_workflow.setParent(header)
        layout.addStretch(1)

        self.session_selector = QComboBox()
        self.session_selector.setAccessibleName("Active segmentation session and scientific role")
        self.session_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.session_selector.setMinimumContentsLength(12)
        self.session_selector.setMaximumWidth(240)
        self.session_selector.activated.connect(self._activate_header_session)
        layout.addWidget(self.session_selector)
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("Application appearance")
        self.theme_combo.setToolTip("Application appearance")
        self.theme_combo.addItems(THEMES)
        self.theme_combo.currentTextChanged.connect(self.set_theme)
        layout.addWidget(self.theme_combo)
        self.parameters_button = QToolButton()
        self.parameters_button.setObjectName("headerActionButton")
        self.parameters_button.setDefaultAction(self.toggle_context_action)
        self.parameters_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        layout.addWidget(self.parameters_button)

        for action, icon_name, tooltip in (
            (self.focus_action, "focus", "Toggle Focus Mode"),
            (self.toggle_navigation_action, "sliders", "Show or hide viewer tools"),
            (self.toggle_results_action, "bars", "Show or hide results"),
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
        self.open_image_button.setDefaultAction(self.open_image_action)
        self.open_image_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.open_image_button.setToolTip("Open TIFF, OME-TIFF, or Leica LIF")
        layout.addWidget(self.open_image_button)
        return header

    def _create_actions(self) -> None:
        self.open_image_action = QAction("Open Image…", self)
        self.open_image_action.setShortcut("Ctrl+Shift+O")
        self.open_image_action.setIcon(icon("open", accent=True))
        self.open_image_action.triggered.connect(self.open_image_dialog)

        self.close_project_action = QAction("Close Project", self)
        self.close_project_action.setIcon(icon("close"))
        self.close_project_action.setToolTip(
            "Close the current project and return to an empty workspace."
        )
        self.close_project_action.setEnabled(False)
        self.close_project_action.triggered.connect(
            lambda checked=False: self.close_project()
        )

        self.quit_action = QAction("Quit NuCytoSeg", self)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.quit_action.triggered.connect(self.close)

        self.toggle_navigation_action = QAction("Viewer tools", self)
        self.toggle_navigation_action.setCheckable(True)
        self.toggle_navigation_action.setChecked(True)
        self.toggle_navigation_action.setShortcut("Ctrl+Shift+L")
        self.toggle_navigation_action.toggled.connect(self._toggle_navigation)

        self.toggle_context_action = QAction("Hide Parameters", self)
        self.toggle_context_action.setCheckable(True)
        self.toggle_context_action.setChecked(True)
        self.toggle_context_action.setShortcut("Ctrl+Shift+R")
        self.toggle_context_action.toggled.connect(self._toggle_context)

        self.toggle_results_action = QAction("Results", self)
        self.toggle_results_action.setCheckable(True)
        self.toggle_results_action.setShortcut("Ctrl+Shift+B")
        self.toggle_results_action.toggled.connect(self._toggle_results)

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
        file_menu.addActions(self.project_actions)
        file_menu.addAction(self.import_legacy_action)
        file_menu.addAction(self.open_image_action)
        file_menu.addAction(self.close_project_action)
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.toggle_navigation_action)
        view_menu.addAction(self.toggle_context_action)
        view_menu.addAction(self.toggle_results_action)
        self.workspace_menu = self.menuBar().addMenu("Workspace")
        self.workspace_menu.addAction(self.focus_action)
        for name in ("Default Workspace", "Image Focus", "Proofreading Workspace", "Data Inspection"):
            self.workspace_menu.addAction(name, partial(self.set_configuration, name))
        self.workspace_menu.addSeparator()
        self.workspace_menu.addAction("Save Custom Layout", self.save_custom_layout)
        self.workspace_menu.addAction("Restore Custom Layout", self.restore_custom_layout)
        self.workspace_menu.addAction(self.restore_layout_action)
        self.workspace_menu.addAction(self.reset_layout_action)
        self.menuBar().addMenu("Tools")
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction(self.about_action)

    def _create_status_widgets(self) -> None:
        self.workflow_status = QLabel()
        self.active_session_title = QLabel()
        self.active_session_title.setTextFormat(Qt.TextFormat.PlainText)
        self.active_session_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.active_session_title.setMaximumWidth(240)
        self.statusBar().addPermanentWidget(self.active_session_title, 1)
        self._update_session_title()
        self.layer_status = QLabel("Layer: none")
        self.z_status = QLabel("Z: —")
        for widget in (self.workflow_status, self.layer_status, self.z_status):
            widget.setContentsMargins(8, 0, 8, 0)
            self.statusBar().addPermanentWidget(widget)
        self._update_active_layer_status(self.embedding.viewer.layers.selection.active)
        self._update_z_status()

    def _update_active_layer_status(self, layer) -> None:
        self.layer_status.setText(f"Layer: {layer.name}" if layer is not None else "Layer: none")

    def _apply_manual_calibration(self, spacing) -> None:
        if not self.project_state.dataset_ready:
            self.statusBar().showMessage("Load a dataset before applying calibration", 5000)
            return
        try:
            self.project_state.set_calibration(spacing)
        except (TypeError, ValueError) as error:
            self.context_panel.project_page.expand_section("calibration")
            self.statusBar().showMessage(f"Calibration: {error}", 6000)
            return
        calibrated_spacing = self.project_state.voxel_spacing_zyx_um
        offset_y, offset_x = self.project_state.working_offset_yx
        translation = (0.0, offset_y * calibrated_spacing[1], offset_x * calibrated_spacing[2])
        for layer in self._channel_layer_callbacks:
            if layer in self.embedding.viewer.layers:
                layer.scale = calibrated_spacing
                layer.translate = translation
        self.context_panel.project_page.calibration_source.setText("manual override")
        self.statusBar().showMessage("Manual Z/Y/X calibration applied", 5000)

    def open_image_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open microscopy image",
            "",
            "Microscopy images (*.tif *.tiff *.ome.tif *.ome.tiff *.lif);;"
            "TIFF images (*.tif *.tiff *.ome.tif *.ome.tiff);;Leica LIF (*.lif)",
        )
        if path:
            self.prepare_image_import(path)

    def _project_has_user_data(self) -> bool:
        if self.project_state.dataset_ready:
            return True
        return any(
            not (getattr(layer, "metadata", {}) or {}).get("nucytoseg_demo", False)
            for layer in self.embedding.viewer.layers
        )

    def _connect_project_actions(self):
        panel = self.context_panel.project_page
        self.project_actions = []
        for name, shortcut, handler in (
            ("New Project", QKeySequence.StandardKey.New, self.new_project),
            ("Open Project…", QKeySequence.StandardKey.Open, self.open_project),
            ("Save Project", QKeySequence.StandardKey.Save, self.save_project),
            ("Save Project As…", QKeySequence.StandardKey.SaveAs, partial(self.save_project, save_as=True)),
        ):
            action = QAction(name, self)
            action.setShortcut(shortcut)
            action.triggered.connect(lambda checked=False, callback=handler: callback())
            button = QToolButton()
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            panel.project_actions.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
            self.project_actions.append(action)
        self.import_legacy_action = QAction("Import Legacy Analysis…", self)
        self.import_legacy_action.triggered.connect(lambda: self.import_legacy_project())
        legacy_button = QToolButton()
        legacy_button.setDefaultAction(self.import_legacy_action)
        panel.project_actions.addWidget(legacy_button, alignment=Qt.AlignmentFlag.AlignLeft)
        state = self.project_state
        prep = self.context_panel.preprocessing_page
        for signal in (
            state.changed, state.channels_changed, state.calibration_changed,
            state.sessions_changed, state.active_session_changed,
            state.normalization_changed, state.normalization_roi_changed,
            state.z_correction_changed, state.segmentation_result_changed,
            state.filtering_result_changed, state.scientific_roi_changed,
            state.perinuclear_markers_changed, self.crop_workspace.working_dataset_changed,
            prep.normalization_settings_changed, prep.z_correction_panel.settings_changed,
            prep.z_correction_panel.include_zeros.toggled,
            prep.z_correction_panel.channel_list.itemSelectionChanged,
            prep.z_correction_panel.inspected_channel_changed,
            prep.channel_combo.currentIndexChanged, prep.method_combo.currentIndexChanged,
            self.context_panel.segmentation_page.configuration_changed,
            self.context_panel.mask_review_page.filters_edited,
        ):
            signal.connect(self._mark_project_dirty)
        self._update_project_actions()

    def _mark_project_dirty(self, *args):
        if self.project_state.dataset_ready and not self.project_state.restoring_project:
            self.project_dirty = True
        self._update_project_actions()

    def _update_project_actions(self):
        available = self._import_thread is None and not self.geometry_panel.busy
        self.import_legacy_action.setEnabled(available)
        for action in self.project_actions[:2]:
            action.setEnabled(available)
        for action in self.project_actions[2:]:
            action.setEnabled(available and self.project_state.dataset_ready)
        label = self.context_panel.project_page.project_description
        path = self.project_directory
        title = path.name if path else ("Unsaved project" if self.project_state.dataset_ready else "No native project directory")
        label.setText(title + (" · Unsaved changes" if path and self.project_dirty else ""))
        label.setToolTip(str(path or ""))
        if hasattr(self, "header_project"):
            project = path.name if path else (self.project_state.dataset.source_filename if self.project_state.dataset_ready else "No project open")
            self.header_project.setText(self.header_project.fontMetrics().elidedText(project, Qt.TextElideMode.ElideMiddle, 195))
            self.header_project.setToolTip(project)

    def _project_idle(self):
        if self.geometry_panel.guided.worker is not None:
            self.statusBar().showMessage("Wait for the watershed calculation to finish.", 5000)
            return False
        if self.geometry_panel.busy:
            self.statusBar().showMessage("Save or discard the active mask preview first.", 5000)
            return False
        if self._import_thread is not None or any(controller.busy for controller in (
            self.crop_workspace, self.normalization, self.z_correction, self.image_preparation,
            self.image_export, self.segmentation, self.filtering,
        )):
            self.statusBar().showMessage("Wait for the active operation to finish.", 5000)
            return False
        return True

    def new_project(self):
        if not self._project_idle():
            return False
        if self._project_has_user_data() or self._pending_import_path is not None:
            if not self._confirm_close_project(self.new_project):
                return False
            self.close_project(confirm=False)
        self.set_workflow("project")
        return True

    def _ask_project_changes(self):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Unsaved Project")
        dialog.setText("Save the current analysis before closing or replacing it?")
        save = dialog.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        save.setEnabled(self.project_state.dataset_ready)
        discard = dialog.addButton("Discard Changes", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        return {save: "save", discard: "discard"}.get(dialog.clickedButton(), "cancel")

    def _confirm_close_project(self, after=None) -> bool:
        if not self.project_dirty:
            return True
        choice = self._ask_project_changes()
        if choice == "save":
            self.save_project(after=after or partial(self.close_project, confirm=False))
        return choice == "discard"

    def save_project(self, directory=None, *, save_as=False, after=None):
        if not self._project_idle() or not self.project_state.dataset_ready:
            return False
        state = self.project_state
        review = self.context_panel.mask_review_page
        if state.filtering_result is not None and review._filter_timer.isActive():
            self.statusBar().showMessage("Wait for the filter settings to settle before saving.", 5000)
            return False
        active = self._project_revision if not save_as and self._project_version == VERSION and (
            directory is None or Path(directory).expanduser().absolute() == self.project_directory
        ) else None
        directory = directory or (self.project_directory if active else None)
        if directory is None:
            suggested = state.original_dataset.source_path.with_suffix(".ncs-project")
            if self._project_version == 1 and self.project_directory is not None:
                suggested = self.project_directory.with_name(f"{self.project_directory.name}-v2")
            directory, _ = QFileDialog.getSaveFileName(self, "Name the New Project Folder", str(suggested))
        if not directory:
            return False
        folder = Path(directory).expanduser().absolute()
        prep = self.context_panel.preprocessing_page
        z_panel = prep.z_correction_panel
        self._capture_session_controls()
        snapshot = snapshot_state(state)
        snapshot.update(
            projection_settings=(prep.channel_combo.currentData(), prep.method_combo.currentData()),
            z_settings={channel.index: z_panel.options_for(channel.index) for channel in state.channels},
            z_inspected=z_panel.current_index(),
            z_selected=z_panel.selected_indexes(),
            z_include_zeros=z_panel.include_zeros.isChecked(),
            z_previews={index: layer.data for index, layer in self.z_correction.preview_layers.items()
                        if index in state.z_correction_reports},
            normalization_settings=prep.normalization_settings(),
            normalization_pixels=(self.normalization.preview_layer.data
                                  if self.normalization.preview_layer is not None else None),
        )
        return self._start_project_job("save", folder, partial(save_project, folder, snapshot, active), after)

    def open_project(self, directory=None, *, replacement=None, confirm=True):
        if not self._project_idle():
            return False
        if directory is None:
            directory = QFileDialog.getExistingDirectory(self, "Open NuCytoSeg Project Folder")
        if not directory:
            return False
        folder = Path(directory).expanduser().absolute()
        if confirm and not self._confirm_close_project(partial(self.open_project, folder, confirm=False)):
            return False
        return self._start_project_job(
            "open", folder, partial(open_project, folder, replacement, self._bioimage_factory),
            relocated=replacement is not None,
        )

    def import_legacy_project(self, directory=None, *, replacement=None):
        if not self._project_idle():
            return False
        if directory is None:
            directory = QFileDialog.getExistingDirectory(self, "Import Legacy Analysis Folder")
        if not directory:
            return False
        folder = Path(directory).expanduser().absolute()
        return self._start_project_job(
            "legacy", folder, partial(import_legacy_analysis, folder, replacement, self._bioimage_factory)
        )

    def _confirm_legacy_import(self, summary):
        return QMessageBox.question(
            self, "Legacy Analysis Compatibility", summary + "\n\nImport this analysis?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes

    def _complete_legacy_import(self, dataset, restored, payload):
        if self._restore_native_project(dataset, restored, payload):
            self.project_dirty = True
            self._update_project_actions()
            self.statusBar().showMessage("Legacy analysis imported. Use Save Project As to convert it.", 7000)

    def _start_project_job(self, kind, folder, operation, after=None, *, relocated=False):
        worker = ProjectWorker(operation)
        controls = (self.centralWidget(), self.menuBar(), self.graph_window)
        self._project_job = {
            "kind": kind, "folder": folder, "after": after, "relocated": relocated,
            "controls": [(widget, widget.isEnabled()) for widget in controls],
        }
        for widget in controls:
            widget.setEnabled(False)
        worker.succeeded.connect(self._project_io_succeeded)
        worker.failed.connect(self._project_io_failed)
        worker.missing_source.connect(self._project_source_missing)
        progress = QProgressDialog({
            "save": "Saving project…", "open": "Opening project…", "legacy": "Verifying legacy analysis…",
        }[kind], "", 0, 0, self)
        progress.setCancelButton(None)
        progress.setWindowTitle("NuCytoSeg Project")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._project_job["progress"] = progress
        self._start_worker(worker, project=True)
        progress.show()
        return True

    def _project_io_succeeded(self, result):
        self._project_job["result"] = result

    def _project_io_failed(self, message):
        self._project_job["error"] = message

    def _project_source_missing(self, path):
        self._project_job["missing"] = path

    def _finish_project_job(self, job):
        job["progress"].close()
        job["progress"].deleteLater()
        for widget, enabled in job["controls"]:
            widget.setEnabled(enabled)
        folder = job["folder"]
        if "missing" in job:
            path, _ = QFileDialog.getOpenFileName(
                self, "Locate Original Image", job["missing"], "Microscopy images (*.tif *.tiff *.lif)"
            )
            if path:
                if job["kind"] == "legacy":
                    self.import_legacy_project(folder, replacement=path)
                else:
                    self.open_project(folder, replacement=path, confirm=False)
            return
        if "error" in job:
            QMessageBox.warning(self, "NuCytoSeg Project", job["error"])
            return
        result = job.get("result")
        if result is None:
            return
        if job["kind"] == "legacy":
            dataset, restored, payload, summary = result
            if self._confirm_legacy_import(summary):
                complete = partial(self._complete_legacy_import, dataset, restored, payload)
                if self._confirm_close_project(complete):
                    complete()
            return
        if job["kind"] == "open":
            revision, dataset, restored, payload = result
            if not self._restore_native_project(dataset, restored, payload):
                return
        else:
            revision, digest = result
            state = self.project_state
            state.original_dataset = replace(state.original_dataset, source_sha256=digest)
            state.working_dataset = replace(state.working_dataset, source_sha256=digest)
            state.dataset = state.working_dataset
        self.project_directory = folder
        self._project_revision = revision
        self._project_version = payload["checkpoint_version"] if job["kind"] == "open" else VERSION
        self.project_dirty = job["relocated"]
        self._update_project_actions()
        self.statusBar().showMessage(f"Project {'opened' if job['kind'] == 'open' else 'saved'} · {folder.name}", 5000)
        if job["after"] is not None:
            job["after"]()

    def _restore_native_project(self, dataset, restored, payload):
        state = self.project_state
        state.restoring_project = True
        try:
            if not self._apply_imported_dataset(dataset, restored):
                return False
            state.channels = restored["channels"]
            state.segmentation_channel_index = restored["segmentation_channel_index"]
            state.channels_changed.emit(tuple(state.channels))
            # Channel/calibration notifications must precede restored preprocessing.
            state.set_calibration(restored["voxel_spacing_zyx_um"], restored["calibration_source"])
            normalization_keys = ("normalization_configuration", "normalization_preview", "normalization_is_current")
            session_keys = ("sessions", "active_session_id", "association_state")
            for key, value in restored.items():
                if key not in normalization_keys + session_keys:
                    setattr(state, key, value)
            state.dataset = state.working_dataset
            state.z_correction_changed.emit(None)
            for key in normalization_keys:
                setattr(state, key, restored[key])
            project = self.context_panel.project_page
            project.time_index.setValue(dataset.time_index)
            project.axes_override.setText(dataset.axes_override or "")
            for field, spacing in zip(project.spacing_fields, state.voxel_spacing_zyx_um, strict=True):
                field.setText(f"{spacing:g}")
            project.calibration_source.setText(state.calibration_source)
            prep = self.context_panel.preprocessing_page
            channel, method = payload["projection_settings"]
            prep.channel_combo.setCurrentIndex(prep.channel_combo.findData(channel))
            prep.method_combo.setCurrentIndex(prep.method_combo.findData(method))
            z_panel = prep.z_correction_panel
            z_panel._settings.update(payload["z_settings"])
            for row in range(z_panel.channel_list.count()):
                item = z_panel.channel_list.item(row)
                item.setSelected(item.data(Qt.ItemDataRole.UserRole) in payload["z_selected"])
            z_panel.include_zeros.setChecked(payload["z_include_zeros"])
            z_index = payload["z_inspected"]
            position = z_panel.inspect_combo.findData(z_index)
            if position >= 0:
                z_panel.inspect_combo.setCurrentIndex(position)
            z_panel._load_current_settings()
            for index in state.z_correction_reports:
                self.z_correction.invalidate_channel(index)
            if payload["z_previews"]:
                self.z_correction._set_previews(payload["z_previews"], z_panel.current_index())
                for index, layer in self.z_correction.preview_layers.items():
                    if index in state.z_correction_applied:
                        layer.metadata["z_correction_applied"] = True
                        layer.name = f"Z-corrected applied · {state.channels[index].name}"
                    self.z_correction.invalidate_channel(index)
            self.z_correction._refresh_results()
            prep.restore_normalization_settings(payload["normalization_settings"])
            if state.normalization_roi_mask_yx is not None:
                self.normalization._show_existing_roi_overlay()
                prep.set_normalization_roi_status("Restored normalization reference ROI.")
            if state.normalization_preview is not None and payload["normalization_pixels"] is not None:
                self.normalization._show_preview(
                    state.normalization_preview, payload["normalization_pixels"], 0.0, restoring=True
                )
            if state.normalization_is_current:
                prep.set_normalization_status(
                    "Applied normalization restored.", preview_ready=self.normalization.preview_layer is not None
                )
            state.normalization_changed.emit(state.normalization_configuration, state.normalization_is_current)
            state.restore_sessions(restored["sessions"], restored["active_session_id"])
            self._display_active_session()
            association_checkpoint.install(state, restored.get("association_state"))
            self.context_panel.matching_page.sync_thresholds(state.matching_pair.pending_settings)
            self.set_workflow("project")
            return True
        finally:
            state.restoring_project = False

    def _activate_header_session(self, index):
        session_id = self.session_selector.itemData(index)
        if session_id is not None:
            self._select_session(session_id)
            self._update_session_title()

    def _update_session_title(self, *args):
        state = self.project_state
        with QSignalBlocker(self.session_selector):
            self.session_selector.clear()
            for session in state.sessions.values():
                self.session_selector.addItem(f"{session.display_name} · {session.scientific_role}", session.session_id)
            self.session_selector.setCurrentIndex(self.session_selector.findData(state.active_session_id))
            self.session_selector.setToolTip(self.session_selector.currentText())
            self.session_selector.setEnabled(state.dataset_ready)
        name = state.active_session.display_name
        label = self.active_session_title
        text = f"Session · {name}"
        label.setText(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, label.maximumWidth()))
        label.setToolTip(text)
        self.active_session_title.setVisible(state.dataset_ready)

    def _select_session(self, session_id):
        review = self.context_panel.mask_review_page
        if not self._project_idle():
            self.context_panel.project_page.refresh_sessions()
            return
        if review._filter_timer.isActive():
            self.statusBar().showMessage("Wait for the filter settings to settle before switching sessions.", 5000)
            self.context_panel.project_page.refresh_sessions()
            return
        self.project_state.select_session(session_id)

    def _create_session(self, name, role, channel_index):
        if not self._project_idle():
            return
        session = self.project_state.create_session(
            name, scientific_role=role, channel_index=channel_index
        )
        self._select_session(session.session_id)

    def _capture_session_controls(self):
        session = self.project_state.active_session
        review = self.context_panel.mask_review_page
        session.display_state["channels"] = {c.index: c.visible for c in self.project_state.channels}
        session.segmentation_settings = self.context_panel.segmentation_page.pending_config()
        session.normalization_settings = self.context_panel.preprocessing_page.normalization_settings()
        if session.filtering_result is not None:
            session.filter_controls = review.filter_settings()
            session.intensity_controls = review.intensity_control_settings()
            if self.filtering.roi_layer is not None:
                try:
                    self.filtering.validate_roi_layer()
                except ValueError:
                    session.roi_grid_compatible = False
                else:
                    session.roi_grid_compatible = True
        review._filter_timer.stop()

    def _display_active_session(self):
        state = self.project_state
        session = state.active_session
        was_restoring = state.restoring_project
        state.restoring_project = True
        try:
            with QSignalBlocker(self.context_panel.preprocessing_page):
                for index, visible in session.display_state.get("channels", {}).items():
                    if self._channel_for_index(index).visible != visible:
                        self._update_channel_configuration(index, "visible", visible)
            if not was_restoring:
                self.normalization.display_session()
            self.mask_selection.clear_all()
            self.segmentation.clear_project()
            self.filtering.clear_project()
            self.context_panel.export_page.clear_scientific_history()
            segmentation_panel = self.context_panel.segmentation_page
            with QSignalBlocker(segmentation_panel):
                settings = session.segmentation_settings or self._default_session_settings
                if session.scientific_role == "cytoplasmic" and session.segmentation_settings is None:
                    settings = dict(settings, MODEL_NAME="")
                segmentation_panel.restore_settings(settings)
            segmentation_panel.execution_log.clear()
            state.perinuclear_markers_changed.emit(tuple(state.perinuclear_markers))
            state.segmentation_result_changed.emit(state.segmentation_result, state.segmentation_is_current)
            filtering = state.filtering_result
            review = self.context_panel.mask_review_page
            review.set_visualization_status("")
            state.filtering_result_changed.emit(filtering, state.filtering_is_current)
            if filtering is not None:
                if state.segmentation_is_current and session.roi_grid_compatible:
                    self.filtering._create_layers(filtering)
                    if not state.filtering_is_current:
                        self.filtering.filtered_layer.name = "Live filtered masks — outdated"
                controls = session.filter_controls
                review.configure_filtering(
                    filtering if controls is None else dict(filtering, settings=controls),
                    state.filtering_is_current,
                )
                if session.intensity_controls is not None:
                    review.restore_intensity_controls(session.intensity_controls)
                if not state.filtering_is_current:
                    review.measurement_status.setText(self.filtering.working_status)
                if not session.roi_grid_compatible:
                    review.accept_roi_button.setEnabled(False)
                    review.roi_status.setText("ROI geometry was incompatible. Recalculate measurements to review a new ROI.")
            elif state.scientific_roi is not None and state.segmentation_is_current:
                self.filtering._create_roi_layer(state.scientific_roi, state.scientific_roi_reviewed)
            self.mask_display.sync(restore_selection=True)
            review._filter_timer.stop()
            state.scientific_roi_changed.emit(state.scientific_roi, state.scientific_roi_reviewed)
            state.quantification_result_changed.emit(state.quantification_result, state.quantification_is_current)
            self.context_panel.quantification_page.show_restored_result()
        finally:
            state.restoring_project = was_restoring

    def close_project(self, *, confirm: bool = True) -> bool:
        if self.geometry_panel.busy:
            return False
        if self._import_thread is not None:
            self.statusBar().showMessage(
                "Wait for the active image import to finish before closing the project.",
                6000,
            )
            return False
        if (
            self.crop_workspace.busy
            or self.normalization.busy
            or self.z_correction.busy
            or self.image_preparation.busy
            or self.image_export.busy
            or self.segmentation.busy
            or self.filtering.busy
        ):
            self.statusBar().showMessage(
                "Wait for the active preprocessing calculation to finish before "
                "closing the project.",
                6000,
            )
            return False
        if not self._project_has_user_data() and self._pending_import_path is None:
            return False
        if confirm and not self._confirm_close_project():
            return False
        self.graph_window.hide()
        self.mask_selection.clear_selection()
        self.selected_mask_panel.set_selection(None, None)
        self.context_panel.mask_review_page.set_selection(None, None)
        self.normalization.clear_project()
        self.z_correction.clear_project()
        self.image_preparation.clear_project()
        self.image_export.clear_project()
        self.segmentation.clear_project()
        self.filtering.clear_project()
        self.crop_workspace.clear_workspace_for_new_project()
        self._disconnect_channel_layers()
        self.embedding.viewer.layers.clear()
        self._pending_import_path = None
        self._pending_lif_series = None
        self._load_after_worker_finished = False
        self._finish_loading_dialog()
        self.project_state.clear_project()
        self.project_directory = self._project_revision = self._project_version = None
        self.project_dirty = False
        self._update_project_actions()
        self.context_panel.project_page.clear_project()
        self.close_project_action.setEnabled(False)
        self.set_workflow("project")
        self.layer_status.setText("Layer: none")
        self.statusBar().showMessage("Project closed — ready to open another image", 5000)
        return True

    def prepare_image_import(self, path, *, confirm_replacement: bool = True) -> bool:
        if not self._project_idle():
            return False
        source = Path(path).expanduser().resolve()
        if confirm_replacement and self._project_has_user_data():
            if not self._confirm_close_project(partial(self.prepare_image_import, source, confirm_replacement=False)):
                return False
        if source.suffix.lower() == ".lif":
            if not source.is_file():
                self._show_import_error(f"Image file does not exist: {source}")
                return False
            self.project_state.set_import_status(
                f"Reading series catalog from {source.name}…"
            )
            worker = LifEnumerationWorker(source, self._bioimage_factory)
            worker.succeeded.connect(
                lambda series, selected=source: self._show_series_browser(
                    selected, series
                )
            )
            self._start_worker(worker)
            return True
        try:
            inspection = inspect_tiff(source)
        except Exception as error:
            self._show_import_error(str(error))
            return False
        self._pending_import_path = source
        panel = self.context_panel.project_page
        panel.set_inspection(inspection)
        panel.set_pending(True)
        self.set_workflow("project")
        if inspection.voxel_spacing_zyx_um is None:
            self.project_state.set_import_status(
                "Calibration missing — enter positive Z, Y, and X spacing in µm"
            )
            return True
        return self.load_pending_image()

    def load_pending_image(self) -> bool:
        if self._pending_import_path is None or not self._project_idle():
            return False
        panel = self.context_panel.project_page
        try:
            options = panel.import_options()
        except ValueError as error:
            self._show_import_error(str(error))
            return False
        self.project_state.set_import_status(
            f"Loading {self._pending_import_path.name}…"
        )
        panel.set_loading(True)
        self.open_image_action.setEnabled(False)
        if self._pending_lif_series is None:
            worker = TiffImportWorker(self._pending_import_path, options)
        else:
            options.pop("axes_override", None)
            self._show_lif_loading_dialog()
            worker = LifImportWorker(
                self._pending_import_path,
                self._pending_lif_series.index,
                options,
                self._bioimage_factory,
            )
        worker.succeeded.connect(self._apply_imported_dataset)
        self._start_worker(worker)
        return True

    def _start_worker(self, worker, *, project=False) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        if not project:
            worker.failed.connect(self._show_import_error)
        diagnostic = getattr(worker, "diagnostic", None)
        if diagnostic is not None:
            diagnostic.connect(self._record_import_diagnostic)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._import_finished)
        thread.finished.connect(thread.deleteLater)
        self._import_thread = thread
        self._import_worker = worker
        self.open_image_action.setEnabled(False)
        self.context_panel.project_page.set_loading(True)
        self._update_project_actions()
        self._refresh_progress()
        thread.start()

    def _show_lif_loading_dialog(self) -> None:
        self._finish_loading_dialog()
        series = self._pending_lif_series
        self._loading_dialog = ImportLoadingDialog(
            self._pending_import_path.name,
            series.name,
            self,
        )
        self._loading_dialog.show()

    def _record_import_diagnostic(self, message: str) -> None:
        text = str(message)
        self.import_diagnostics.append(text)
        self.import_diagnostics[:] = self.import_diagnostics[-500:]
        logging.getLogger("nuclear_segmentation.import").info(text)
        if self._loading_dialog is not None:
            self._loading_dialog.update_stage(text)

    def _finish_loading_dialog(self) -> None:
        if self._loading_dialog is not None:
            self._loading_dialog.finish()
            self._loading_dialog = None

    def _show_series_browser(self, source: Path, series) -> None:
        if self._close_when_import_finishes:
            return
        if not series:
            self._show_import_error("The LIF file contains no readable image series.")
            return
        browser = ImageSeriesBrowser(source, series, self)
        if browser.exec() != ImageSeriesBrowser.DialogCode.Accepted:
            self.project_state.set_import_status("LIF series selection cancelled")
            return
        selected = browser.selected_series
        self._pending_import_path = source
        self._pending_lif_series = selected
        panel = self.context_panel.project_page
        panel.set_lif_series(source, selected)
        panel.set_pending(True)
        self.set_workflow("project")
        if selected.voxel_spacing_zyx_um is None:
            self.project_state.set_import_status(
                "Calibration missing — enter positive Z, Y, and X spacing in µm"
            )
            return
        if self._import_thread is None:
            self.load_pending_image()
        else:
            self._load_after_worker_finished = True

    def _import_finished(self) -> None:
        self._import_thread = None
        self._import_worker = None
        self._refresh_progress()
        self.open_image_action.setEnabled(True)
        self.context_panel.project_page.set_loading(False)
        self._update_project_actions()
        if self._project_job is not None:
            job, self._project_job = self._project_job, None
            self._finish_project_job(job)
            return
        if self._close_when_import_finishes:
            self._close_when_import_finishes = False
            self.close()
            return
        if self._load_after_worker_finished:
            self._load_after_worker_finished = False
            self.load_pending_image()
            return

    def _apply_imported_dataset(self, dataset, restored=None) -> bool:
        self._record_import_diagnostic(
            f"layer_creation_started: file={dataset.source_filename} "
            f"series={dataset.series_index}"
        )
        viewer = self.embedding.viewer
        old_layers = [
            layer
            for layer in list(viewer.layers)
            if not (getattr(layer, "metadata", {}) or {}).get(
                "nucytoseg_selection_highlight", False
            )
        ]
        grid = restored["working_dataset"] if restored else dataset
        channels = restored["channels"] if restored else dataset.channels
        spacing = restored["voxel_spacing_zyx_um"] if restored else dataset.voxel_spacing_zyx_um
        oy, ox = restored["working_offset_yx"] if restored else (0, 0)
        added = []
        try:
            for channel in channels:
                added.append(
                    viewer.add_image(
                        grid.data_czyx[channel.index],
                        name=channel.name,
                        colormap=channel.colormap,
                        blending="additive",
                        scale=spacing,
                        translate=(0.0, oy * spacing[1], ox * spacing[2]),
                        units=("µm", "µm", "µm"),
                        visible=channel.visible,
                        metadata={
                            "nucytoseg_imported": True,
                            "channel_index": channel.index,
                            "source_path": str(dataset.source_path),
                            "working_crop": bool(restored and restored["crop_provenance"]),
                        },
                    )
                )
        except Exception as error:
            for layer in added:
                if layer in viewer.layers:
                    viewer.layers.remove(layer)
            self._show_import_error(f"Could not create image layers: {error}")
            return False
        self.crop_workspace.clear_workspace_for_new_project()
        self.normalization.clear_project()
        self.z_correction.clear_project()
        self.image_preparation.clear_project()
        self.image_export.clear_project()
        for layer in old_layers:
            if layer in viewer.layers:
                viewer.layers.remove(layer)
        for layer, channel in zip(added, channels):
            layer.name = channel.name
        viewer.dims.axis_labels = ("Z", "Y", "X")
        viewer.layers.selection.active = added[0] if added else None
        viewer.canvas.overlays.scale_bar.visible = True
        self.project_directory = self._project_revision = self._project_version = None
        self.project_state.set_dataset(dataset)
        self._connect_channel_layers(added)
        self._pending_import_path = None
        self._pending_lif_series = None
        self.statusBar().showMessage(
            f"Loaded {dataset.source_filename}: {len(dataset.channels)} channel(s)",
            5000,
        )
        self._record_import_diagnostic(
            f"import_completed: file={dataset.source_filename} "
            f"series={dataset.series_index} CZYX={dataset.standardized_shape}"
        )
        self._finish_loading_dialog()
        return True

    def _display_working_dataset(self, dataset) -> None:
        viewer = self.embedding.viewer
        self._disconnect_channel_layers()
        old_layers = [
            layer
            for layer in list(viewer.layers)
            if (getattr(layer, "metadata", {}) or {}).get("nucytoseg_imported")
        ]
        spacing = self.project_state.voxel_spacing_zyx_um
        offset_y, offset_x = self.project_state.working_offset_yx
        added = []
        try:
            for channel in self.project_state.channels:
                added.append(
                    viewer.add_image(
                        dataset.data_czyx[channel.index],
                        name=channel.name,
                        colormap=channel.colormap,
                        blending="additive",
                        scale=spacing,
                        translate=(0.0, offset_y * spacing[1], offset_x * spacing[2]),
                        units=("µm", "µm", "µm"),
                        visible=channel.visible,
                        metadata={
                            "nucytoseg_imported": True,
                            "channel_index": channel.index,
                            "source_path": str(dataset.source_path),
                            "working_crop": self.project_state.crop_provenance is not None,
                        },
                    )
                )
        except Exception as error:
            for layer in added:
                if layer in viewer.layers:
                    viewer.layers.remove(layer)
            self._connect_channel_layers(old_layers)
            self.statusBar().showMessage(f"Could not display working crop: {error}", 8000)
            return
        for layer in old_layers:
            if layer in viewer.layers:
                viewer.layers.remove(layer)
        for layer, channel in zip(added, self.project_state.channels):
            layer.name = channel.name
        self._connect_channel_layers(added)
        if added:
            viewer.layers.selection.active = added[0]
        viewer.dims.axis_labels = ("Z", "Y", "X")
        viewer.canvas.overlays.scale_bar.visible = True

    def _channel_for_index(self, index: int):
        return next(
            (channel for channel in self.project_state.channels if channel.index == index),
            None,
        )

    def _layer_for_channel(self, index: int):
        return next(
            (
                layer
                for layer in self.embedding.viewer.layers
                if (getattr(layer, "metadata", {}) or {}).get("nucytoseg_imported")
                and (getattr(layer, "metadata", {}) or {}).get("channel_index") == index
            ),
            None,
        )

    def _update_channel_configuration(self, index: int, field: str, value) -> None:
        if self._syncing_channels:
            return
        try:
            self.project_state.update_channel(index, **{field: value})
        except ValueError as error:
            self.context_panel.project_page.expand_section("channels")
            self.project_state.channels_changed.emit(tuple(self.project_state.channels))
            self.statusBar().showMessage(f"Channel configuration: {error}", 6000)
            return
        layer = self._layer_for_channel(index)
        if layer is None or field == "role":
            return
        self._syncing_channels = True
        try:
            if field == "name":
                layer.name = str(value).strip()
            elif field == "visible":
                layer.visible = bool(value)
            elif field == "colormap":
                layer.colormap = str(value)
        finally:
            self._syncing_channels = False

    def _connect_channel_layers(self, layers) -> None:
        self._disconnect_channel_layers()
        for layer in layers:
            index = int(layer.metadata["channel_index"])
            name_callback = lambda event=None, item=layer, channel=index: self._layer_channel_changed(
                item, channel, "name"
            )
            visible_callback = lambda event=None, item=layer, channel=index: self._layer_channel_changed(
                item, channel, "visible"
            )
            layer.events.name.connect(name_callback)
            layer.events.visible.connect(visible_callback)
            self._channel_layer_callbacks[layer] = (name_callback, visible_callback)

    def _layer_channel_changed(self, layer, index: int, field: str) -> None:
        if self._syncing_channels or layer not in self.embedding.viewer.layers:
            return
        value = layer.name if field == "name" else bool(layer.visible)
        self._syncing_channels = True
        try:
            self.project_state.update_channel(index, **{field: value})
        except ValueError as error:
            channel = self._channel_for_index(index)
            if channel is not None:
                if field == "name":
                    layer.name = channel.name
                else:
                    layer.visible = channel.visible
            self.statusBar().showMessage(f"Channel configuration: {error}", 6000)
        finally:
            self._syncing_channels = False

    def _disconnect_channel_layers(self) -> None:
        for layer, callbacks in self._channel_layer_callbacks.items():
            name_callback, visible_callback = callbacks
            layer.events.name.disconnect(name_callback)
            layer.events.visible.disconnect(visible_callback)
        self._channel_layer_callbacks.clear()

    def _show_import_error(self, message: str) -> None:
        self._record_import_diagnostic(f"import_failed: {message}")
        self._finish_loading_dialog()
        self.project_state.set_import_status(f"Import failed: {message}")
        self.statusBar().showMessage(f"Image import failed: {message}", 8000)

    def _update_z_status(self, event=None) -> None:
        steps = self.embedding.viewer.dims.current_step
        self.z_status.setText(f"Z: {steps[0]}" if steps else "Z: —")

    def _mask_selection_changed(self, layer, mask_id) -> None:
        self.selected_mask_panel.set_selection(layer, mask_id)
        if layer is None or layer not in (
            self.segmentation.result_layer, self.filtering.filtered_layer,
            self.mask_display.layers.get("edited"), self.geometry_panel.layer,
        ):
            layer = mask_id = None
        self.context_panel.mask_review_page.set_selection(layer, mask_id)

    def _release_comparisons(self):
        if not self.mask_display.suspended and not self.project_state.restoring_project:
            self.segmentation.release_hidden_comparisons()

    def _sync_mask_display(self, *args):
        if not self.project_state.restoring_project:
            self.mask_display.sync(restore_selection=True)
            self._release_comparisons()

    def set_workflow(self, key: str, *, transfer_inspection=True) -> None:
        if key == "matching" and self.geometry_panel.busy:
            return
        target = self.matching_display.proofreading_target() if key == "proofreading" and transfer_inspection else None
        self.navigation.set_workflow(key)
        key = self.navigation.current_workflow
        if key != "matching":
            self.matching_display.set_active(False)
        if target:
            self.geometry_panel.inspect_handoff(*target)
        self.association_panel.active = key == "proofreading"
        self.association_panel.refresh()
        if key != "proofreading":
            self.geometry_panel.clear_handoff()
            self.geometry_panel.guided.clear_context()
        self.context_panel.set_workflow(key, self.navigation.current_subpage)
        self.matching_display.set_active(key == "matching")
        if target and self.matching_display.location():
            self.matching_display.location().view.restore(self.embedding.viewer)
        reviewing = key in {"matching", "proofreading"}
        self.review_mode.setVisible(reviewing)
        self.review_mode.setCurrentIndex(int(key == "proofreading"))
        self.pair_appearance.setVisible(key == "matching")
        self.context_title.setText("Review & Proofreading" if reviewing else self.context_panel.workflow_title)
        if self.graph_window.isVisible() and key not in {"quality_control", "matching"}:
            self.graph_window.close()
        first_visit = key not in self.results.last_tab
        self.results.set_stage(key)
        if key == "quality_control" or (key == "matching" and first_visit):
            self.toggle_results_action.setChecked(True)
        self._refresh_progress()
        label = self.context_panel.workflow_label
        if hasattr(self, "workflow_status"):
            self.workflow_status.setText(f"Workflow: {label}")
        if hasattr(self, "header_workflow"):
            self.header_workflow.setText(label.upper())
        self.statusBar().showMessage(f"{label} workspace selected")

    def _refresh_progress(self, *args):
        values = statuses(self)
        self.navigation.set_statuses(values)
        if "Running" in values.values():
            self._progress_timer.start(350)

    def _show_mask_review(self) -> None:
        self.set_workflow("mask_review")

    def _toggle_navigation(self, visible: bool) -> None:
        self.navigation.setVisible(visible)

    def _toggle_context(self, visible: bool) -> None:
        self.context_container.setVisible(visible)
        self.toggle_context_action.setText("Hide Parameters" if visible else "Show Parameters")

    def hide_context_panel(self) -> None:
        self.toggle_context_action.setChecked(False)

    def _toggle_results(self, visible):
        if not visible and self.results.height() > 0:
            self.results_height = self.results.height()
        self.results.setVisible(visible)
        if visible:
            height = min(self.results_height, max(80, self.vertical_splitter.height() - 180))
            self.vertical_splitter.setSizes([self.vertical_splitter.height() - height, height])

    def set_configuration(self, name):
        self.focus_action.setChecked(False)
        show_left, show_right, height = {
            "Default Workspace": (True, True, 0), "Image Focus": (False, False, 0),
            "Proofreading Workspace": (True, True, 180), "Data Inspection": (False, True, 330),
        }[name]
        self.navigation.set_collapsed(False)
        self.toggle_navigation_action.setChecked(show_left)
        self.toggle_context_action.setChecked(show_right)
        self.splitter.setSizes([235, max(320, self.width() - 590), 350])
        self.toggle_results_action.setChecked(bool(height))
        if height:
            self.results_height = height
            self._toggle_results(True)

    def save_custom_layout(self):
        self.workspace_settings.write(self._focus_snapshot or self.workspace_snapshot(), "custom")
        self.statusBar().showMessage("Custom layout saved", 4000)

    def restore_custom_layout(self):
        snapshot = self.workspace_settings.read("custom")
        if snapshot:
            self.focus_action.setChecked(False)
            self.apply_snapshot(snapshot)
        else:
            self.statusBar().showMessage("Save a custom layout first.", 4000)

    def workspace_snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            geometry=self.saveGeometry(),
            window_state=self.saveState(),
            splitter_sizes=tuple(self.splitter.sizes()),
            navigation_collapsed=self.navigation.collapsed,
            navigation_visible=self.toggle_navigation_action.isChecked(),
            context_visible=self.toggle_context_action.isChecked(),
            results_visible=self.toggle_results_action.isChecked(),
            results_height=self.results_height if self.results.isHidden() else self.results.height(),
            results_tab=self.results.tabText(self.results.currentIndex()),
            workflow=self.navigation.current_workflow,
            workflow_subpages=self.navigation.subpage_selections(),
            proofreading_sections=tuple((section.key, section.expanded) for section in
                                        self.context_panel.mask_review_page.proofreading_page.findChildren(CollapsibleSection)),
            project_sections=tuple(
                self.context_panel.project_page.section_states().items()
            ),
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
        self.navigation.restore_subpages(snapshot.workflow_subpages)
        self.set_workflow(snapshot.workflow)
        self._restore_results(snapshot)
        self.context_panel.project_page.set_section_states(snapshot.project_sections)
        states = dict(snapshot.proofreading_sections)
        for section in self.context_panel.mask_review_page.proofreading_page.findChildren(CollapsibleSection):
            if section.key in states:
                section.set_expanded(states[section.key], emit=False)
        return restored

    def _restore_results(self, snapshot):
        self.toggle_results_action.setChecked(snapshot.results_visible)
        self.results_height = max(80, snapshot.results_height)
        if snapshot.results_visible:
            self._toggle_results(True)
        self.results.set_stage(self.navigation.current_workflow, snapshot.results_tab)

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
        self.toggle_results_action.setChecked(False)
        self.showNormal()
        self.resize(1440, 900)
        self.navigation.set_collapsed(False)
        self.toggle_navigation_action.setChecked(True)
        self.toggle_context_action.setChecked(True)
        self.splitter.setSizes([255, 835, 350])
        self.set_workflow("project")
        self.context_panel.project_page.set_section_states(
            {key: True for key in self.context_panel.project_page.sections}
        )
        self.statusBar().showMessage("Default workspace layout restored", 4000)

    def set_focus_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._focus_mode:
            return
        if enabled:
            self._focus_snapshot = self.workspace_snapshot()
            self._focus_mode = True
            self.navigation.set_collapsed(True)
            self.toggle_navigation_action.setChecked(False)
            self.toggle_results_action.setChecked(False)
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
                self._restore_results(snapshot)
            self._focus_snapshot = None
            self.statusBar().showMessage("Workspace layout restored")

    def set_theme(self, name):
        if name not in THEMES:
            name = "Dark"
        with QSignalBlocker(self.theme_combo):
            self.theme_combo.setCurrentText(name)
        self.workspace_settings.settings.setValue("appearance/theme", name)
        apply_theme(self, name)

    def showEvent(self, event) -> None:
        super().showEvent(event)


    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "image_display_panel"):
            # Reserve sidebar space for the workflow at the minimum window height.
            extra = max(0, event.size().height() - self.minimumHeight())
            controls = self.image_display_panel.controls_scroll
            controls.setFixedHeight(min(72 + extra // 2, controls.widget().sizeHint().height()))

    def closeEvent(self, event) -> None:
        if (self.matching_display.execution.busy or self.geometry_panel.busy
                or self.geometry_panel.guided.worker is not None or self._project_job is not None):
            self.statusBar().showMessage("Finish the active operation before closing.", 5000)
            event.ignore()
            return
        if self._import_thread is not None and self._import_thread.isRunning():
            self._close_when_import_finishes = True
            self.statusBar().showMessage(
                "Waiting for the active image read to finish before closing…"
            )
            event.ignore()
            return
        if (
            self.normalization.busy
            or self.crop_workspace.busy
            or self.z_correction.busy
            or self.image_preparation.busy
            or self.image_export.busy
            or self.segmentation.busy
            or self.filtering.busy
        ):
            self.statusBar().showMessage(
                "Wait for the active preprocessing calculation to finish before exiting."
            )
            event.ignore()
            return
        if not self._closed:
            if not self._confirm_close_project(self.close):
                event.ignore()
                return
            self._closed = True
            self._finish_loading_dialog()
            self.save_layout()
            self.embedding.viewer.dims.events.current_step.disconnect(self._update_z_status)
            self.association_panel.active = False
            self.association_panel.refresh()
            self.geometry_panel.dispose()
            self.mask_context_menu.dispose()
            self.matching_display.dispose()
            self.mask_display.dispose()
            self.mask_selection.dispose()
            self.normalization.dispose()
            self.z_correction.dispose()
            self.image_preparation.dispose()
            self.image_export.dispose()
            self.segmentation.dispose()
            self.filtering.dispose()
            self.crop_workspace.dispose()
            self._disconnect_channel_layers()
            self.context_panel.project_page.channel_panel.dispose()
            self.context_panel.preprocessing_page.dispose()
            self.context_panel.export_page.image_preparation_panel.dispose()
            self.context_panel.segmentation_page.dispose()
            self.context_panel.mask_review_page.dispose()
            self.context_panel.quantification_page.dispose()
            self.graph_window.close()
            self.context_panel.quality_control_page.dispose()
            self.layer_panel.dispose()
            self.image_display_panel.dispose()
            self.viewer_toolbar.dispose()
            self.embedding.shutdown()
        event.accept()
