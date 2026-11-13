"""Extensible contextual placeholders for Phase 1B workflows."""
from __future__ import annotations

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from .workflow_navigation import WORKFLOWS


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

    def __init__(self, parent: QWidget | None = None):
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
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 8, 0, 0)
            for section in SECTIONS[workflow.key]:
                label = QLabel(section)
                label.setObjectName("contextSection")
                label.setToolTip("Scientific workflow integration begins in Phase 2")
                page_layout.addWidget(label)
            note = QLabel("Interface placeholder — no analysis is executed in Phase 1B.")
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
