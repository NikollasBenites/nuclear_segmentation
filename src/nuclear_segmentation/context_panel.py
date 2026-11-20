"""Workflow context pages for the independent NuCytoSeg interface."""
from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QLabel, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from .workflow_navigation import WORKFLOWS
from .project_panel import ProjectPanel
from .preprocessing_panel import PreprocessingPanel
from .image_export_panel import ImageExportPanel
from .segmentation_panel import SegmentationPanel
from .mask_review_panel import MaskReviewPanel
from .quantification_panel import QuantificationPanel
from .live_plots import ProjectLivePlots


CONTEXT_TITLES = {
    "preprocessing": "Preprocessing Parameters",
    "segmentation": "Segmentation Parameters",
    "quantification": "Quantification Results",
    "export": "Export Options",
}


class ContextPanel(QWidget):
    """Display the selected workflow and subpage using persistent widgets."""

    def __init__(self, project_state, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("contextPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 10)
        self.heading = QLabel()
        self.heading.setObjectName("contextHeading")
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.stack = QStackedWidget()
        self._indexes = {}
        self._subpage_stacks = {}
        for workflow in WORKFLOWS:
            key = workflow.key
            if key == "project":
                self.project_page = ProjectPanel(project_state)
                page = self._scroll(self.project_page)
            elif key == "preprocessing":
                self.preprocessing_page = PreprocessingPanel(project_state)
                page = self.preprocessing_page
            elif key == "segmentation":
                self.segmentation_page = SegmentationPanel(project_state)
                page = self._scroll(self.segmentation_page)
            elif key == "mask_review":
                self.mask_review_page = MaskReviewPanel(project_state)
                page = self.mask_review_page
            elif key == "quantification":
                self.quantification_page = QuantificationPanel(project_state)
                page = self._scroll(self.quantification_page)
            elif key == "quality_control":
                self.quality_control_page = ProjectLivePlots(project_state)
                page = QWidget()
                controls = QVBoxLayout(page)
                self.show_graphs_button = QPushButton("Show Graphs")
                controls.addWidget(self.show_graphs_button)
                controls.addStretch(1)
            elif key == "export":
                self.export_page = ImageExportPanel(project_state=project_state)
                page = self.export_page
            else:
                page = self._matching_placeholder()

            if workflow.subpages:
                pages = QStackedWidget(page)
                group_layout = QVBoxLayout(page)
                group_layout.setContentsMargins(0, 0, 0, 0)
                group_layout.addWidget(pages)
                for subpage, _ in workflow.subpages:
                    pages.addWidget(self._scroll(page.pages[subpage], horizontal=True))
                self._subpage_stacks[key] = pages
            self._indexes[key] = self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)
        self.set_workflow("project")

    @staticmethod
    def _scroll(widget, *, horizontal=False):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.viewport().setObjectName("contextViewport")
        if not horizontal:
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(widget)
        widget.setAutoFillBackground(False)
        return area

    @staticmethod
    def _matching_placeholder():
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        for section in ("Nuclear Masks", "Cytoplasmic Masks", "Associations"):
            label = QLabel(section)
            label.setObjectName("contextSection")
            label.setToolTip("Scientific workflow integration begins in Phase 2")
            layout.addWidget(label)
        note = QLabel("Interface placeholder — no analysis is executed here yet.")
        note.setObjectName("placeholderNote")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def set_workflow(self, key: str, subpage: str | None = None) -> None:
        if key not in self._indexes:
            key = "project"
        workflow = next(item for item in WORKFLOWS if item.key == key)
        self.workflow_label = workflow.label
        self.workflow_title = CONTEXT_TITLES.get(key, workflow.label)
        self.stack.setCurrentIndex(self._indexes[key])
        self.heading.setVisible(bool(workflow.subpages))
        if workflow.subpages:
            titles = dict(workflow.subpages)
            if subpage not in titles:
                subpage = next(iter(titles))
            self._subpage_stacks[key].setCurrentIndex(list(titles).index(subpage))
            heading = (
                "Segmentation Input Normalization" if subpage == "normalization"
                else titles[subpage]
            )
            self.heading.setText(heading)
        else:
            self.heading.setText(workflow.label)
