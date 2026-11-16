"""Extensible contextual placeholders for Phase 1B workflows."""
from __future__ import annotations

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QFrame, QLabel, QStackedWidget, QVBoxLayout, QWidget

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
        self.mask_selection_card = QFrame()
        self.mask_selection_card.setObjectName("maskSelectionCard")
        selection_layout = QVBoxLayout(self.mask_selection_card)
        selection_layout.setContentsMargins(12, 9, 12, 9)
        selection_layout.setSpacing(2)
        selection_title = QLabel("SELECTED MASK")
        selection_title.setObjectName("selectionCardTitle")
        self.mask_id_label = QLabel("Mask ID: —")
        self.mask_id_label.setObjectName("selectedMaskId")
        self.mask_layer_label = QLabel("No Labels layer active")
        self.mask_layer_label.setObjectName("selectedMaskLayer")
        self.mask_layer_label.setWordWrap(True)
        selection_layout.addWidget(selection_title)
        selection_layout.addWidget(self.mask_id_label)
        selection_layout.addWidget(self.mask_layer_label)
        layout.addWidget(self.mask_selection_card)
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

    def set_selected_mask(self, layer, mask_id: int | None) -> None:
        """Present application mask selection without performing analysis."""
        if layer is None:
            self.mask_id_label.setText("Mask ID: —")
            self.mask_layer_label.setText("No Labels layer active")
        elif mask_id is None:
            self.mask_id_label.setText("Mask ID: —")
            self.mask_layer_label.setText(f"{layer.name} · no mask selected")
        else:
            self.mask_id_label.setText(f"Mask ID: {int(mask_id)}")
            self.mask_layer_label.setText(layer.name)
