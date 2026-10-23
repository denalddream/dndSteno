"""Dark theme: Fusion palette plus a little QSS.

Widgets opt into styles via properties instead of inline stylesheets:
    button.setProperty("role", "primary" | "danger")
    label.setProperty("muted", True)
    label.setProperty("heading", True)
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#1e1f24"
SURFACE = "#26282e"
SURFACE_2 = "#30333a"
BORDER = "#3a3d45"
TEXT = "#e6e6e6"
MUTED = "#9a9ca3"
ACCENT = "#e08a2c"
DANGER = "#d9534f"
SUCCESS = "#4caf6a"
WARNING = "#e0b92c"

QSS = f"""
QWidget {{ font-size: 10pt; }}
QListWidget#sidebar {{
    background: {SURFACE}; border: none; border-right: 1px solid {BORDER};
    padding-top: 12px; font-size: 11pt;
}}
QListWidget#sidebar::item {{ padding: 10px 14px; border-radius: 6px; margin: 2px 8px; }}
QListWidget#sidebar::item:selected {{ background: {SURFACE_2}; color: {ACCENT}; }}
QWidget#jobsBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QLabel[heading="true"] {{ font-size: 16pt; font-weight: 600; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton[role="primary"] {{ background: {ACCENT}; border: none; color: #1a1a1a; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: #eb9a40; }}
QPushButton[role="danger"] {{ background: {DANGER}; border: none; color: white; font-weight: 600; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextBrowser, QListWidget, QTableWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px;
}}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; }}
QTabBar::tab {{ padding: 6px 14px; }}
QTabBar::tab:selected {{ color: {ACCENT}; }}
QProgressBar {{ background: {SURFACE_2}; border: none; border-radius: 4px; height: 8px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: SURFACE_2,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE_2,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: "#1a1a1a",
        QPalette.ColorRole.ToolTipBase: SURFACE_2,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.PlaceholderText: MUTED,
        QPalette.ColorRole.Link: ACCENT,
    }.items():
        p.setColor(role, QColor(color))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(MUTED))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(MUTED))
    app.setPalette(p)
    app.setStyleSheet(QSS)
