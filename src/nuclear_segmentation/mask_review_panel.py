"""Application-owned view of existing mask selection and segmentation state."""
from __future__ import annotations

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .collapsible_section import CollapsibleSection


class MaskReviewPanel(QWidget):
    """Present review state without owning selection or scientific decisions."""

    show_all_requested = Signal()
    show_selected_requested = Signal()
    clear_selection_requested = Signal()

    def __init__(self, project_state, parent=None):
        super().__init__(parent)
        self.project_state = project_state
        self._result = None
        self._result_current = False
        self._selected_id = None
        self._selected_is_raw = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        section = CollapsibleSection("review_selection", "Selection")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.mask_id = self._value("Selected Mask ID: —")
        self.active_layer = self._value("Active segmentation layer: —")
        self.segmentation_status = self._value("Segmentation: missing")
        self.mask_count = self._value("Detected masks: —")
        for widget in (
            self.mask_id, self.active_layer, self.segmentation_status,
            self.mask_count,
        ):
            layout.addWidget(widget)
        section.set_content_layout(layout)
        root.addWidget(section)

        section = CollapsibleSection(
            "selected_mask_information", "Selected Mask Information"
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.mask_type = self._value("Mask type: —")
        self.measurements = self._value(
            "Scientific measurements are not available in this milestone."
        )
        layout.addWidget(self.mask_type)
        layout.addWidget(self.measurements)
        section.set_content_layout(layout)
        root.addWidget(section)

        section = CollapsibleSection("review_visualization", "Visualization")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.show_all_button = QPushButton("Show all masks")
        self.show_selected_button = QPushButton("Show selected mask only")
        self.clear_button = QPushButton("Clear mask selection")
        self.show_all_button.clicked.connect(self.show_all_requested)
        self.show_selected_button.clicked.connect(self.show_selected_requested)
        self.clear_button.clicked.connect(self.clear_selection_requested)
        for button in (
            self.show_all_button, self.show_selected_button, self.clear_button
        ):
            layout.addWidget(button)
        self.visualization_status = self._value(
            "Select masks with the existing Review / Select tool."
        )
        layout.addWidget(self.visualization_status)
        section.set_content_layout(layout)
        root.addWidget(section)

        section = CollapsibleSection("manual_review_status", "Review Status")
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 10)
        self.review_status = self._value("No segmentation is available for review.")
        layout.addWidget(self.review_status)
        self.keep_button = QPushButton("Keep selected mask")
        self.exclude_button = QPushButton("Exclude selected mask")
        self.automatic_button = QPushButton("Use automatic filters")
        self.undo_button = QPushButton("Undo last review decision")
        for button in (
            self.keep_button, self.exclude_button, self.automatic_button,
            self.undo_button,
        ):
            button.setEnabled(False)
            button.setToolTip(
                "Requires integrated measurements, automatic filters, and a "
                "reviewed scientific ROI."
            )
            layout.addWidget(button)
        self.review_safeguard = self._value(
            "Manual keep/exclude is disabled until filtering, eligibility, guard zones, "
            "and reviewed ROI state can be supplied to the existing ManualReview logic."
        )
        layout.addWidget(self.review_safeguard)
        section.set_content_layout(layout)
        root.addWidget(section)
        root.addStretch(1)

        project_state.segmentation_result_changed.connect(self.set_result)
        self.set_result(
            project_state.segmentation_result,
            project_state.segmentation_is_current,
        )

    @staticmethod
    def _value(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        return label

    def set_selection(self, layer, mask_id) -> None:
        metadata = {} if layer is None else (getattr(layer, "metadata", {}) or {})
        self._selected_id = None if mask_id is None else int(mask_id)
        self._selected_is_raw = bool(metadata.get("nucytoseg_raw_segmentation"))
        self.mask_id.setText(
            "Selected Mask ID: —"
            if self._selected_id is None
            else f"Selected Mask ID: {self._selected_id}"
        )
        self.active_layer.setText(
            "Active segmentation layer: —"
            if layer is None
            else f"Active segmentation layer: {layer.name}"
        )
        role = str(metadata.get("mask_type", "")).strip()
        self.mask_type.setText(f"Mask type: {role or 'unclassified'}")
        self._refresh_actions()

    def set_result(self, result, current: bool) -> None:
        self._result = result
        self._result_current = bool(current)
        if result is None:
            self.segmentation_status.setText("Segmentation: missing")
            self.mask_count.setText("Detected masks: —")
            self.review_status.setText("No segmentation is available for review.")
        elif current:
            self.segmentation_status.setText("Segmentation: available and current")
            self.mask_count.setText(
                f"Detected masks: {int(result['object_count']):,}"
            )
            self.review_status.setText(
                "Raw masks are ready for visual review. Scientific keep/exclude "
                "decisions remain disabled until their prerequisites are integrated."
            )
        else:
            self.segmentation_status.setText("Segmentation: outdated")
            self.mask_count.setText(
                f"Detected masks: {int(result['object_count']):,}"
            )
            self.review_status.setText(
                "Previous masks remain visible for inspection but are outdated. "
                "Rerun segmentation before further scientific review."
            )
        self._refresh_actions()

    def set_visualization_status(self, message: str) -> None:
        self.visualization_status.setText(str(message))

    def _refresh_actions(self) -> None:
        available = self._result is not None
        selected = available and self._selected_is_raw and self._selected_id is not None
        self.show_all_button.setEnabled(available)
        self.show_selected_button.setEnabled(selected)
        self.clear_button.setEnabled(self._selected_id is not None)

    def dispose(self) -> None:
        self.project_state.segmentation_result_changed.disconnect(self.set_result)
