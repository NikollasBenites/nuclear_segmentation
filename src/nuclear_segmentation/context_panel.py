"""Workflow context pages for the independent NuCytoSeg interface."""
from __future__ import annotations

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QLabel, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from .workflow_navigation import WORKFLOWS
from .project_panel import ProjectPanel
from .preprocessing_panel import PreprocessingPanel
from .image_export_panel import ImageExportPanel
from .segmentation_panel import SegmentationPanel
from .mask_review_panel import MaskReviewPanel
from .quantification_panel import QuantificationPanel
from .live_plots import ProjectLivePlots


SECTIONS = {
    "project": ("Image provenance", "Analysis mode", "Recent projects"),
    "preprocessing": ("Input", "Normalization", "Z Correction"),
    "segmentation": ("Model", "Parameters", "Execution"),
    "mask_review": ("Selection", "Properties", "Editing Tools"),
    "matching": ("Nuclear Masks", "Cytoplasmic Masks", "Associations"),
    "quantification": ("Measurements", "Density", "ROI"),
    "quality_control": ("Graphs", "Diagnostics"),
    "export": ("Output Settings", "Export Options"),
}


class ContextPanel(QWidget):
    """Workflow-specific placeholder stack with no scientific side effects."""

    collapse_requested = Signal()

    def __init__(self, project_state, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("contextPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        self.heading = QLabel()
        self.heading.setObjectName("contextHeading")
        layout.addWidget(self.heading)
        self.stack = QStackedWidget()
        self._indexes: dict[str, int] = {}
        for workflow in WORKFLOWS:
            if workflow.key == "project":
                self.project_page = ProjectPanel(project_state)
                project_scroll = QScrollArea()
                project_scroll.setWidgetResizable(True)
                project_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                project_scroll.setWidget(self.project_page)
                self._indexes[workflow.key] = self.stack.addWidget(project_scroll)
                continue
            if workflow.key == "preprocessing":
                self.preprocessing_page = PreprocessingPanel(project_state)
                preprocessing_scroll = QScrollArea()
                preprocessing_scroll.setWidgetResizable(True)
                preprocessing_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                preprocessing_scroll.setWidget(self.preprocessing_page)
                self._indexes[workflow.key] = self.stack.addWidget(
                    preprocessing_scroll
                )
                continue
            if workflow.key == "segmentation":
                self.segmentation_page = SegmentationPanel(project_state)
                segmentation_scroll = QScrollArea()
                segmentation_scroll.setWidgetResizable(True)
                segmentation_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                segmentation_scroll.setHorizontalScrollBarPolicy(
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                )
                segmentation_scroll.setWidget(self.segmentation_page)
                self._indexes[workflow.key] = self.stack.addWidget(
                    segmentation_scroll
                )
                continue
            if workflow.key == "mask_review":
                self.mask_review_page = MaskReviewPanel(project_state)
                review_scroll = QScrollArea()
                review_scroll.setWidgetResizable(True)
                review_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                review_scroll.setHorizontalScrollBarPolicy(
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                )
                review_scroll.setWidget(self.mask_review_page)
                self._indexes[workflow.key] = self.stack.addWidget(review_scroll)
                continue
            if workflow.key == "quantification":
                self.quantification_page = QuantificationPanel(project_state)
                quantification_scroll = QScrollArea()
                quantification_scroll.setWidgetResizable(True)
                quantification_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                quantification_scroll.setHorizontalScrollBarPolicy(
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                )
                quantification_scroll.setWidget(self.quantification_page)
                self._indexes[workflow.key] = self.stack.addWidget(
                    quantification_scroll
                )
                continue
            if workflow.key == "quality_control":
                self.quality_control_page = ProjectLivePlots(project_state)
                quality_scroll = QScrollArea()
                quality_scroll.setWidgetResizable(True)
                quality_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                quality_scroll.setHorizontalScrollBarPolicy(
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                )
                quality_scroll.setWidget(self.quality_control_page)
                self._indexes[workflow.key] = self.stack.addWidget(quality_scroll)
                continue
            if workflow.key == "export":
                self.export_page = ImageExportPanel()
                export_scroll = QScrollArea()
                export_scroll.setWidgetResizable(True)
                export_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                export_scroll.setWidget(self.export_page)
                self._indexes[workflow.key] = self.stack.addWidget(export_scroll)
                continue
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 8, 0, 0)
            for section in SECTIONS[workflow.key]:
                label = QLabel(section)
                label.setObjectName("contextSection")
                label.setToolTip("Scientific workflow integration begins in Phase 2")
                page_layout.addWidget(label)
            note = QLabel("Interface placeholder — no analysis is executed here yet.")
            note.setObjectName("placeholderNote")
            note.setWordWrap(True)
            page_layout.addWidget(note)
            page_layout.addStretch(1)
            self._indexes[workflow.key] = self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)
        self.set_workflow("project")

    def set_workflow(self, key: str) -> None:
        if key not in self._indexes:
            key = "project"
        label = next(item.label for item in WORKFLOWS if item.key == key)
        self.heading.setText(label)
        self.stack.setCurrentIndex(self._indexes[key])
