"""Independent NuCytoSeg QMainWindow composition."""
from __future__ import annotations

from pathlib import Path
import logging

from qtpy.QtCore import QSize, Qt, QThread
from qtpy.QtGui import QAction, QKeySequence
from qtpy.QtWidgets import (
    QHBoxLayout,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .context_panel import ContextPanel
from .crop_workspace import CropWorkspaceController
from .image_import import TiffImportWorker, inspect_tiff
from .image_display_panel import ImageDisplayPanel
from .image_preparation_workspace import ImagePreparationController
from .image_export_workspace import ImageExportController
from .import_loading import ImportLoadingDialog
from .layer_panel import LayerPanel
from .lif_import import LifEnumerationWorker, LifImportWorker
from .mask_selection import MaskSelectionController
from .napari_embedding import DISPLAY_NAME, EmbeddedNapariWidget
from .normalization_workspace import NormalizationController
from .selected_mask_panel import SelectedMaskPanel
from .project_state import ProjectState
from .series_browser import ImageSeriesBrowser
from .segmentation_execution import SegmentationController
from .viewer_toolbar import ViewerToolbar
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
        viewer_layout.addWidget(self.viewer_toolbar)
        viewer_layout.addWidget(self.embedding, 1)
        self.context_panel = ContextPanel(self.project_state)
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
            self.context_panel.preprocessing_page.image_preparation_panel,
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
        self.z_correction.correction_applied.connect(self.normalization.invalidate)
        self.project_state.channels_changed.connect(
            lambda channels: self.normalization.invalidate()
        )
        self.project_state.calibration_changed.connect(
            lambda spacing, source: self.normalization.invalidate()
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
        self.splitter.setSizes([210, 890, 340])
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
        self.mask_selection.selection_changed.connect(
            self.selected_mask_panel.set_selection
        )
        self.layer_panel.active_layer_changed.connect(self._update_active_layer_status)
        self.context_panel.project_page.open_requested.connect(self.open_image_dialog)
        self.context_panel.project_page.load_pending_requested.connect(
            self.load_pending_image
        )
        self.context_panel.project_page.analysis_mode_changed.connect(
            self._set_analysis_mode
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
        self.context_panel.project_page.availability_changed.connect(
            self.close_project_action.setEnabled
        )
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
        self.statusBar().showMessage("NuCytoSeg ready — open an image to begin")
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
        self.image_display_panel = ImageDisplayPanel(
            self.embedding.viewer, self.project_state
        )
        layout.addWidget(self.image_display_panel)
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
        self.open_image_button.setDefaultAction(self.open_image_action)
        self.open_image_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.open_image_button.setToolTip("Open TIFF, OME-TIFF, or Leica LIF")
        layout.addWidget(self.open_image_button)
        return header

    def _create_actions(self) -> None:
        self.open_image_action = QAction("Open Image…", self)
        self.open_image_action.setShortcut(QKeySequence.StandardKey.Open)
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
        file_menu.addAction(self.open_image_action)
        file_menu.addAction(self.close_project_action)
        file_menu.addSeparator()
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

    def _set_analysis_mode(self, mode: str) -> None:
        self.project_state.analysis_mode = str(mode)

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
        for layer in self._channel_layer_callbacks:
            if layer in self.embedding.viewer.layers:
                layer.scale = self.project_state.voxel_spacing_zyx_um
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

    def _confirm_close_project(self) -> bool:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Close Project?")
        dialog.setText(
            "Closing this project will discard unsaved changes, including mask "
            "edits and unsaved analysis results.\n\n"
            "The original source image will not be modified."
        )
        close_button = dialog.addButton(
            "Close without Saving", QMessageBox.ButtonRole.DestructiveRole
        )
        dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(dialog.buttons()[-1])
        dialog.exec()
        return dialog.clickedButton() is close_button

    def close_project(self, *, confirm: bool = True) -> bool:
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
        self.mask_selection.clear_selection()
        self.selected_mask_panel.set_selection(None, None)
        self.normalization.clear_project()
        self.z_correction.clear_project()
        self.image_preparation.clear_project()
        self.image_export.clear_project()
        self.segmentation.clear_project()
        self.crop_workspace.clear_workspace_for_new_project()
        self._disconnect_channel_layers()
        self.embedding.viewer.layers.clear()
        self._pending_import_path = None
        self._pending_lif_series = None
        self._load_after_worker_finished = False
        self._finish_loading_dialog()
        self.project_state.clear_project()
        self.context_panel.project_page.clear_project()
        self.close_project_action.setEnabled(False)
        self.set_workflow("project")
        self.layer_status.setText("Layer: none")
        self.statusBar().showMessage("Project closed — ready to open another image", 5000)
        return True

    def prepare_image_import(self, path, *, confirm_replacement: bool = True) -> bool:
        if self._import_thread is not None:
            self.statusBar().showMessage("An image import is already running", 4000)
            return False
        if self.crop_workspace.busy or self.image_export.busy or self.segmentation.busy:
            self.statusBar().showMessage(
                "Wait for active preprocessing or image export to finish before "
                "opening another image",
                5000,
            )
            return False
        source = Path(path).expanduser().resolve()
        if confirm_replacement and self._project_has_user_data():
            response = QMessageBox.question(
                self,
                "Replace current project?",
                "Opening another image will replace the current viewer dataset. "
                "Unsaved masks or edits are not saved by Phase 2A.1. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
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
        if self._pending_import_path is None or self._import_thread is not None:
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

    def _start_worker(self, worker) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
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
        self.open_image_action.setEnabled(True)
        self.context_panel.project_page.set_loading(False)
        if self._close_when_import_finishes:
            self._close_when_import_finishes = False
            self.close()
            return
        if self._load_after_worker_finished:
            self._load_after_worker_finished = False
            self.load_pending_image()
            return

    def _apply_imported_dataset(self, dataset) -> None:
        self.crop_workspace.clear_workspace_for_new_project()
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
        added = []
        try:
            for channel in dataset.channels:
                added.append(
                    viewer.add_image(
                        dataset.data_czyx[channel.index],
                        name=channel.name,
                        colormap=channel.colormap,
                        blending="additive",
                        scale=dataset.voxel_spacing_zyx_um,
                        units=("µm", "µm", "µm"),
                        visible=channel.visible,
                        metadata={
                            "nucytoseg_imported": True,
                            "channel_index": channel.index,
                            "source_path": str(dataset.source_path),
                        },
                    )
                )
        except Exception as error:
            for layer in added:
                if layer in viewer.layers:
                    viewer.layers.remove(layer)
            self._show_import_error(f"Could not create image layers: {error}")
            return
        self.normalization.clear_project()
        self.z_correction.clear_project()
        self.image_preparation.clear_project()
        self.image_export.clear_project()
        for layer in old_layers:
            if layer in viewer.layers:
                viewer.layers.remove(layer)
        for layer, channel in zip(added, dataset.channels):
            layer.name = channel.name
        viewer.dims.axis_labels = ("Z", "Y", "X")
        viewer.layers.selection.active = added[0] if added else None
        viewer.canvas.overlays.scale_bar.visible = True
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
        self.set_workflow(snapshot.workflow)
        self.context_panel.project_page.set_section_states(snapshot.project_sections)
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
            #projectSection { background: #162735; border-radius: 7px; }
            #collapsibleSectionHeader {
                background: #1a3040; color: #eef7fa; border-radius: 6px;
                padding: 8px 10px; font-weight: 700; text-align: left;
            }
            #collapsibleSectionHeader:hover { background: #214052; }
            #collapsibleSectionContent QLabel { color: #c8d7e3; }
            #contextSectionTitle { color: #75dcd4; font-weight: 700; }
            #projectSection QLineEdit, #projectSection QSpinBox, #projectSection QComboBox {
                background: #0f1923; border: 1px solid #314759; border-radius: 4px; padding: 4px;
            }
            #projectSection QPushButton { background: #17677a; border-radius: 5px; padding: 7px; }
            #placeholderNote { color: #8fa1b3; padding: 10px 3px; }
            #importLoadingDialog { background: #101c27; color: #e5edf3; }
            #importLoadingDialog QLabel { color: #c4d1dc; }
            #importLoadingDialog #loadingTitle {
                color: #75dcd4; font-size: 16px; font-weight: 700;
            }
            #importLoadingDialog QProgressBar {
                background: #0a141d; border: 1px solid #314759;
                border-radius: 4px; min-height: 10px;
            }
            #importLoadingDialog QProgressBar::chunk { background: #218ba0; }
            QSplitter::handle { background: #253342; width: 2px; }
            QStatusBar { border-top: 1px solid #263748; font-size: 12px; min-height: 25px; }
            """
        )

    def closeEvent(self, event) -> None:
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
        ):
            self.statusBar().showMessage(
                "Wait for the active preprocessing calculation to finish before exiting."
            )
            event.ignore()
            return
        if not self._closed:
            self._closed = True
            self._finish_loading_dialog()
            self.save_layout()
            self.embedding.viewer.dims.events.current_step.disconnect(self._update_z_status)
            self.mask_selection.dispose()
            self.normalization.dispose()
            self.z_correction.dispose()
            self.image_preparation.dispose()
            self.image_export.dispose()
            self.segmentation.dispose()
            self.crop_workspace.dispose()
            self._disconnect_channel_layers()
            self.context_panel.project_page.channel_panel.dispose()
            self.context_panel.preprocessing_page.dispose()
            self.context_panel.segmentation_page.dispose()
            self.layer_panel.dispose()
            self.image_display_panel.dispose()
            self.viewer_toolbar.dispose()
            self.embedding.shutdown()
        event.accept()
