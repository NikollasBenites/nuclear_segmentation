"""Compact collapsible, scrollable, copyable scientific results widget."""
from __future__ import annotations

from qtpy.QtWidgets import QApplication, QPlainTextEdit, QPushButton, QVBoxLayout

from .collapsible_section import CollapsibleSection


class ScientificResultsSection(CollapsibleSection):
    def __init__(self, key: str, title: str, parent=None):
        super().__init__(key, title, parent)
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(10, 8, 10, 10)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text.setMinimumHeight(150)
        self.copy_button = QPushButton("Copy Results")
        self.copy_button.clicked.connect(self.copy_results)
        self.content_layout.addWidget(self.text)
        self.content_layout.addWidget(self.copy_button)
        self.set_content_layout(self.content_layout)
        self.set_results("No results calculated.")

    def set_results(self, text: str) -> None:
        self.text.setPlainText(str(text))
        self.copy_button.setEnabled(bool(str(text).strip()))

    def copy_results(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())

    def insert_widget(self, index: int, widget) -> None:
        """Insert application-owned results content into the collapsible body."""
        self.content_layout.insertWidget(index, widget)
