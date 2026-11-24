"""Application-owned workflow navigation for the NuCytoSeg desktop shell."""
from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtCore import QSize, Qt, Signal
from qtpy.QtWidgets import (
    QButtonGroup, QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from .ui_icons import icon


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    label: str
    icon_name: str
    subpages: tuple[tuple[str, str], ...] = ()


WORKFLOWS = (
    WorkflowDefinition("project", "Project", "folder"),
    WorkflowDefinition("preprocessing", "Preprocessing", "sliders", (
        ("crop_roi", "Crop & ROI"),
        ("z_correction", "Z-Intensity Correction"),
        ("normalization", "Input Normalization"),
    )),
    WorkflowDefinition("segmentation", "Segmentation", "segment"),
    WorkflowDefinition("mask_review", "Mask Review", "review", (
        ("filtering", "Filtering"),
        ("roi_counting", "ROI & Counting"),
    )),
    WorkflowDefinition("proofreading", "Proofreading", "paint"),
    WorkflowDefinition("matching", "Matching", "link"),
    WorkflowDefinition("quantification", "Quantification", "bars"),
    WorkflowDefinition("quality_control", "Quality Control", "quality"),
    WorkflowDefinition("export", "Export", "export", (
        ("scientific_results", "Scientific Results"),
        ("multichannel_images", "Multichannel Images"),
    )),
)


class WorkflowNavigation(QWidget):
    """Collapsible navigation that remains usable as an icon-only rail."""

    workflow_changed = Signal(str)
    collapsed_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("workflowNavigation")
        self._collapsed = False
        self._buttons: dict[str, QToolButton] = {}
        self._submenus = {}
        self._subpage_buttons = {}
        self._selected_subpages = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(5)

        self.brand = QLabel("NuCytoSeg")
        self.brand.setObjectName("navigationBrand")
        layout.addWidget(self.brand)

        rail = QScrollArea()
        rail.setObjectName("workflowRail")
        rail.setFrameShape(QScrollArea.Shape.NoFrame)
        rail.setWidgetResizable(True)
        rail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rail.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        items = QWidget()
        item_layout = QVBoxLayout(items)
        item_layout.setContentsMargins(0, 0, 0, 0)
        item_layout.setSpacing(5)
        rail.setWidget(items)
        layout.addWidget(rail, 1)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for workflow in WORKFLOWS:
            button = QToolButton()
            button.setObjectName(f"workflow_{workflow.key}")
            button.setIcon(icon(workflow.icon_name))
            button.setIconSize(QSize(28, 28))
            button.setText(workflow.label)
            button.setToolTip(workflow.label)
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setMinimumHeight(40)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.clicked.connect(
                lambda checked=False, key=workflow.key: self.set_workflow(key, emit=True)
            )
            self.group.addButton(button)
            self._buttons[workflow.key] = button
            item_layout.addWidget(button)
            if workflow.subpages:
                submenu = QWidget()
                sublayout = QVBoxLayout(submenu)
                sublayout.setContentsMargins(12, 0, 0, 0)
                sublayout.setSpacing(2)
                group = QButtonGroup(submenu)
                buttons = {}
                for key, title in workflow.subpages:
                    child = QToolButton()
                    child.setObjectName("workflowSubpage")
                    child.setText(title.replace("&", "&&"))
                    child.setToolTip(title)
                    child.setAccessibleName(title)
                    child.setCheckable(True)
                    child.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                    child.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                    child.clicked.connect(lambda checked=False, key=key: self.set_subpage(key, emit=True))
                    group.addButton(child)
                    sublayout.addWidget(child)
                    buttons[key] = child
                first = workflow.subpages[0][0]
                self._submenus[workflow.key] = submenu
                self._subpage_buttons[workflow.key] = buttons
                self._selected_subpages[workflow.key] = first
                buttons[first].setChecked(True)
                item_layout.addWidget(submenu)
        item_layout.addStretch(1)

        self.collapse_button = QToolButton()
        self.collapse_button.setObjectName("collapseNavigation")
        self.collapse_button.setIcon(icon("collapse"))
        self.collapse_button.setIconSize(QSize(22, 22))
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

    @property
    def current_subpage(self) -> str | None:
        return self._selected_subpages.get(self.current_workflow)

    def subpage_selections(self):
        return tuple(self._selected_subpages.items())

    def restore_subpages(self, selections) -> None:
        for workflow, key in selections:
            if key in self._subpage_buttons.get(workflow, {}):
                self._selected_subpages[workflow] = key
                self._subpage_buttons[workflow][key].setChecked(True)

    def set_subpage(self, key: str, *, emit: bool = False) -> None:
        workflow = self.current_workflow
        if key not in self._subpage_buttons.get(workflow, {}):
            return
        if self.current_subpage == key:
            return
        self.restore_subpages(((workflow, key),))
        if emit:
            self.workflow_changed.emit(workflow)

    def _update_submenus(self) -> None:
        for key, submenu in self._submenus.items():
            submenu.setVisible(not self._collapsed and key == self.current_workflow)

    def set_workflow(self, key: str, *, emit: bool = False) -> None:
        if key not in self._buttons:
            key = "project"
        self._buttons[key].setChecked(True)
        self._update_submenus()
        if emit:
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
        self.collapse_button.setIcon(icon("expand" if collapsed else "collapse"))
        self.collapse_button.setText("" if collapsed else "Collapse")
        self.collapse_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if collapsed
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.collapse_button.setToolTip(
            "Expand workflow navigation" if collapsed else "Collapse workflow navigation"
        )
        if collapsed:
            self.setFixedWidth(64)
        else:
            self.setMinimumWidth(200)
            self.setMaximumWidth(320)
            self.resize(210, self.height())
        self._update_submenus()
        if changed:
            self.collapsed_changed.emit(collapsed)
