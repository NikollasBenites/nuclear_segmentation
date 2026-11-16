"""Reusable accessible collapsible section for contextual workflow panels."""
from __future__ import annotations

from qtpy.QtCore import QSize, Qt, Signal
from qtpy.QtWidgets import QFrame, QToolButton, QVBoxLayout, QWidget

from .ui_icons import icon


class CollapsibleSection(QFrame):
    """Keep section widgets alive while independently hiding their content."""

    expanded_changed = Signal(str, bool)

    def __init__(self, key: str, title: str, parent=None, *, expanded: bool = True):
        super().__init__(parent)
        self.key = str(key)
        self.setObjectName("projectSection")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = QToolButton()
        self.header.setObjectName("collapsibleSectionHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setIconSize(QSize(18, 18))
        self.header.setAccessibleName(f"{title} section")
        self.header.toggled.connect(self._set_expanded_from_button)
        root.addWidget(self.header)
        self.content = QWidget()
        self.content.setObjectName("collapsibleSectionContent")
        root.addWidget(self.content)
        self.set_expanded(expanded, emit=False)

    @property
    def expanded(self) -> bool:
        return self.header.isChecked()

    def set_content_layout(self, layout) -> None:
        self.content.setLayout(layout)

    def set_expanded(self, expanded: bool, *, emit: bool = True) -> None:
        expanded = bool(expanded)
        self.header.blockSignals(True)
        self.header.setChecked(expanded)
        self.header.blockSignals(False)
        self.content.setVisible(expanded)
        self.header.setIcon(icon("chevron_down" if expanded else "expand"))
        self.header.setToolTip("Collapse section" if expanded else "Expand section")
        if emit:
            self.expanded_changed.emit(self.key, expanded)

    def _set_expanded_from_button(self, expanded: bool) -> None:
        self.content.setVisible(bool(expanded))
        self.header.setIcon(icon("chevron_down" if expanded else "expand"))
        self.header.setToolTip("Collapse section" if expanded else "Expand section")
        self.expanded_changed.emit(self.key, bool(expanded))
