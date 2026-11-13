"""Application-owned workflow navigation for the NuCytoSeg desktop shell."""
from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtCore import QSize, Qt, Signal
from qtpy.QtGui import QColor, QIcon, QPainter, QPixmap
from qtpy.QtWidgets import QButtonGroup, QLabel, QToolButton, QVBoxLayout, QWidget


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    label: str
    symbol: str


WORKFLOWS = (
    WorkflowDefinition("project", "Project", "P"),
    WorkflowDefinition("preprocessing", "Preprocessing", "Pr"),
    WorkflowDefinition("segmentation", "Segmentation", "S"),
    WorkflowDefinition("mask_review", "Mask Review", "R"),
    WorkflowDefinition("matching", "Matching", "M"),
    WorkflowDefinition("quantification", "Quantification", "Q"),
    WorkflowDefinition("quality_control", "Quality Control", "QC"),
    WorkflowDefinition("export", "Export", "E"),
)


def _symbol_icon(symbol: str) -> QIcon:
    pixmap = QPixmap(28, 28)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#26354a"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(1, 1, 26, 26, 7, 7)
    painter.setPen(QColor("#8dd6ff"))
    font = painter.font()
    font.setBold(True)
    font.setPointSize(7 if len(symbol) > 1 else 9)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.end()
    return QIcon(pixmap)


class WorkflowNavigation(QWidget):
    """Collapsible navigation that remains usable as an icon-only rail."""

    workflow_changed = Signal(str)
    collapsed_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("workflowNavigation")
        self._collapsed = False
        self._buttons: dict[str, QToolButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(5)

        self.brand = QLabel("NuCytoSeg")
        self.brand.setObjectName("navigationBrand")
        layout.addWidget(self.brand)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for workflow in WORKFLOWS:
            button = QToolButton()
            button.setObjectName(f"workflow_{workflow.key}")
            button.setIcon(_symbol_icon(workflow.symbol))
            button.setIconSize(QSize(28, 28))
            button.setText(workflow.label)
            button.setToolTip(workflow.label)
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setMinimumHeight(40)
            button.clicked.connect(
                lambda checked=False, key=workflow.key: self.workflow_changed.emit(key)
            )
            self.group.addButton(button)
            self._buttons[workflow.key] = button
            layout.addWidget(button)
        layout.addStretch(1)

        self.collapse_button = QToolButton()
        self.collapse_button.setObjectName("collapseNavigation")
        self.collapse_button.setText("Collapse")
        self.collapse_button.setToolTip("Collapse workflow navigation")
        self.collapse_button.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
        layout.addWidget(self.collapse_button)
        self.set_workflow("project")
        self.set_collapsed(False)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    @property
    def current_workflow(self) -> str:
        checked = self.group.checkedButton()
        for key, button in self._buttons.items():
            if button is checked:
                return key
        return "project"

    def set_workflow(self, key: str, *, emit: bool = False) -> None:
        if key not in self._buttons:
            key = "project"
        changed = self.current_workflow != key
        self._buttons[key].setChecked(True)
        if emit and changed:
            self.workflow_changed.emit(key)

    def set_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        changed = self._collapsed != collapsed
        self._collapsed = collapsed
        self.brand.setText("NCS" if collapsed else "NuCytoSeg")
        for button in self._buttons.values():
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonIconOnly
                if collapsed
                else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            )
        self.collapse_button.setText("›" if collapsed else "Collapse")
        self.collapse_button.setToolTip(
            "Expand workflow navigation" if collapsed else "Collapse workflow navigation"
        )
        self.setFixedWidth(64 if collapsed else 210)
        if changed:
            self.collapsed_changed.emit(collapsed)
