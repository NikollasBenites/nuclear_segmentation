"""Application-owned workflow navigation for the NuCytoSeg desktop shell."""
from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtCore import QSize, Qt, Signal
from qtpy.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from .collapsible_section import CollapsibleSection
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
    """Viewer sidebar with a separately mounted workflow bar and context subpages."""

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
        layout.setContentsMargins(4, 4, 4, 4)
        self.brand = QLabel("Viewer")
        self.brand.setObjectName("navigationBrand")
        layout.addWidget(self.brand)
        self.tools_scroll = QScrollArea()
        self.tools_scroll.setWidgetResizable(True)
        self.tools_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.tools_body = QWidget()
        self.tools = QVBoxLayout(self.tools_body)
        self.tools.setContentsMargins(0, 0, 0, 0)
        self.tools_scroll.setWidget(self.tools_body)
        layout.addWidget(self.tools_scroll, 1)

        self.stage_bar = QWidget()
        self.stage_bar.setAccessibleName("Scientific workflow")
        stages = QHBoxLayout(self.stage_bar)
        stages.setContentsMargins(6, 3, 6, 3)
        stages.setSpacing(3)
        self.subpages = QWidget()
        branches = QVBoxLayout(self.subpages)
        branches.setContentsMargins(4, 0, 4, 0)
        self._current = "project"
        self.group = QButtonGroup(self)
        for workflow in WORKFLOWS:
            if workflow.key == "proofreading":
                continue
            button = QToolButton()
            button.setObjectName(f"workflow_{workflow.key}")
            title = "Review & Proofreading" if workflow.key == "matching" else workflow.label
            button.setAccessibleName(title)
            button.setText(title.replace("&", "&&"))
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.clicked.connect(lambda checked=False, key=workflow.key: self.workflow_changed.emit(key))
            self.group.addButton(button)
            self._buttons[workflow.key] = button
            stages.addWidget(button, 2 if workflow.key == "matching" else 1)
            if workflow.subpages:
                submenu = QWidget()
                sublayout = QVBoxLayout(submenu)
                sublayout.setContentsMargins(0, 0, 0, 0)
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
                branches.addWidget(submenu)
        self.legend = CollapsibleSection("association_legend", "Inspection legend")
        legend_layout = QVBoxLayout()
        self.legend.set_content_layout(legend_layout)
        for color, description, title, glyph in (
            ("#00ff00", "Green", "MATCH", "■"), ("#ffff00", "Yellow", "AMBIGUOUS", "■"),
            ("#ff0000", "Red", "NO_MATCH", "■"), ("#ffffff", "White outline", "Selected object", "□"),
            ("#00ffff", "Cyan outline", "Accepted association", "□"),
            ("#be70ff", "Violet outline", "Other overlapping candidate", "□"),
        ):
            item = QLabel(f'<span style="color:{color}; background-color:#363636">{glyph}</span> {title}')
            item.setAccessibleName(f"{description} · {title}")
            item.setToolTip(item.accessibleName())
            item.setWordWrap(True)
            legend_layout.addWidget(item)
        self.legend_selection = QLabel()
        self.legend_selection.setWordWrap(True)
        legend_layout.addWidget(self.legend_selection)
        self._legend_context = None
        self.tools.addWidget(self.legend)
        self.legend.hide()


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
        return self._current

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
            submenu.setVisible(key == self.current_workflow)

    def set_legend(self, workflow, message):
        if workflow != self.current_workflow:
            return
        self._legend_context = workflow if message is not None else None
        self.legend_selection.setText(message or "")
        self.legend_selection.setVisible(bool(message))
        self.legend.setVisible(message is not None and not self.collapsed)

    def set_workflow(self, key: str, *, emit: bool = False) -> None:
        if key not in self._buttons and key != "proofreading":
            key = "project"
        if self._legend_context != key:
            self._legend_context = None
            self.legend.hide()
        self._current = key
        self._buttons["matching" if key == "proofreading" else key].setChecked(True)
        self._update_submenus()
        if emit:
            self.workflow_changed.emit(key)

    def set_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        changed = self._collapsed != collapsed
        self._collapsed = collapsed
        self.legend.setVisible(not collapsed and self._legend_context == self.current_workflow)
        self.tools_scroll.setVisible(not collapsed)
        self.brand.setVisible(not collapsed)
        self.collapse_button.setIcon(icon("expand" if collapsed else "collapse"))
        self.collapse_button.setText("" if collapsed else "Collapse viewer tools")
        self.collapse_button.setAccessibleName("Expand viewer tools" if collapsed else "Collapse viewer tools")
        self.collapse_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        if collapsed:
            self.setFixedWidth(44)
        else:
            self.setMinimumWidth(160)
            self.setMaximumWidth(480)
        if changed:
            self.collapsed_changed.emit(collapsed)

    def set_statuses(self, statuses):
        for key, button in self._buttons.items():
            title = button.accessibleName()
            status = statuses[key]
            button.setText(title.replace("&", "&&") + "\n" + status)
            button.setToolTip(f"{title} · {status}")
            button.setAccessibleDescription(status)
