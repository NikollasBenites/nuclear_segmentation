"""Window-scoped Qt chrome. Scientific layers and plot palettes have separate owners."""
from dataclasses import dataclass

from qtpy.QtGui import QColor, QPalette


@dataclass(frozen=True)
class Colors:
    background: str
    surface: str
    raised: str
    field: str
    text: str
    muted: str
    line: str
    accent: str
    highlight: str
    disabled: str
    destructive: str


THEMES = {
    "Dark": Colors("#121a20", "#19242b", "#24333b", "#10191e", "#e5eef0",
                   "#adbec4", "#42535a", "#156575", "#80d9d5", "#96a5aa", "#ffac9e"),
    "Light": Colors("#eaf0f1", "#fafcfc", "#e1ebed", "#ffffff", "#172d35",
                    "#445d66", "#a8b9bd", "#126070", "#075d68", "#63747b", "#912f24"),
}


def apply_theme(window, name):
    c = THEMES[name]
    palette = QPalette(window.palette())
    roles = QPalette.ColorRole
    for role, value in (
        (roles.Window, c.surface), (roles.WindowText, c.text), (roles.Text, c.text),
        (roles.ButtonText, c.text), (roles.Button, c.raised), (roles.Base, c.field),
        (roles.AlternateBase, c.surface), (roles.ToolTipBase, c.field), (roles.ToolTipText, c.text),
        (roles.Highlight, c.accent), (roles.HighlightedText, "#ffffff"),
        (roles.PlaceholderText, c.muted), (roles.Link, c.highlight),
    ):
        palette.setColor(role, QColor(value))
    for role in (roles.Text, roles.ButtonText, roles.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.disabled))
    window.setPalette(palette)
    window.setStyleSheet(f"""
        QMainWindow, #desktopRoot {{ background: {c.background}; }}
        QWidget {{ color: {c.text}; font-size: 10pt; }}
        QDialog, QMenu, QMenuBar, QStatusBar, #applicationHeader, #workflowNavigation,
        #contextContainer, QScrollArea, #contextViewport, QTabWidget::pane {{ background: {c.surface}; }}
        QMenuBar::item:selected, QMenu::item:selected, QTabBar::tab:selected,
        QToolButton:checked {{ background: {c.accent}; color: white; }}
        #applicationHeader, #contextPanelHeader, QTabBar::tab {{ border-bottom: 1px solid {c.line}; }}
        #applicationName {{ font-size: 15pt; font-weight: bold; }}
        #panelTitle, #contextHeading, #navigationBrand {{ font-size: 12pt; font-weight: bold; }}
        #headerWorkflow, #applicationSubtitle, #panelKicker {{ color: {c.muted}; }}
        #projectSection, #selectedMaskPanel, QHeaderView::section {{ background: {c.raised}; }}
        #collapsibleSectionHeader {{ text-align: left; padding: 4px 6px; font-weight: bold; }}
        QPushButton, QToolButton, QTabBar::tab {{ background: {c.raised}; border: 1px solid transparent;
            border-radius: 4px; padding: 3px 6px; }}
        QPushButton, QComboBox, QAbstractSpinBox, QLineEdit {{ min-height: 19px; }}
        QPushButton:hover, QToolButton:hover {{ border-color: {c.line}; }}
        #openImageButton, QPushButton[priority="primary"] {{ background: {c.accent}; color: white; }}
        QPushButton[priority="destructive"] {{ color: {c.destructive}; }}
        QComboBox, QAbstractSpinBox, QLineEdit, QPlainTextEdit, QAbstractItemView {{
            background: {c.field}; border: 1px solid {c.line}; border-radius: 3px;
            alternate-background-color: {c.surface}; selection-background-color: {c.accent};
            selection-color: white; }}
        QAbstractButton:focus, QComboBox:focus, QLineEdit:focus, QAbstractSpinBox:focus,
        QAbstractItemView:focus {{ border: 1px solid {c.highlight}; }}
        QToolTip {{ background: {c.field}; color: {c.text}; border: 1px solid {c.line}; padding: 4px; }}
        QAbstractButton:disabled, QPushButton[priority]:disabled, #openImageButton:disabled,
        QComboBox:disabled, QLineEdit:disabled {{ background: {c.surface}; color: {c.disabled}; }}
        QSplitter::handle {{ background: {c.line}; width: 3px; height: 3px; }}
        QHeaderView::section {{ color: {c.text}; padding: 3px; border: 0; }}
        QStatusBar {{ color: {c.muted}; min-height: 20px; }}
    """)

