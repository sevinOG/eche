# gui/widgets/terminalwindow.py
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QPushButton,
    QLabel,
)

from gui.theme import APP_NAME, apply_theme


class TerminalWindow(QMainWindow):
    """Dedicated custom terminal output window for updates, builds, or logs."""

    def __init__(self, title: str = "Terminal Output", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — {title}")
        self.resize(800, 500)
        self.setMinimumSize(600, 350)

        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        # Header / Title
        top = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("Title")
        top.addWidget(self.title_label, stretch=1)

        self.close_btn = QPushButton("Close")
        self.close_btn.setObjectName("ghost")
        self.close_btn.clicked.connect(self.close)
        top.addWidget(self.close_btn)
        lay.addLayout(top)

        # Terminal text box
        self.text_area = QTextEdit()
        self.text_area.setObjectName("LogBox")
        self.text_area.setReadOnly(True)
        font = QFont("Consolas", 10)
        self.text_area.setFont(font)
        lay.addWidget(self.text_area, stretch=1)

        apply_theme(self)

    def append_line(self, text: str):
        """Append a line of text/logs to the custom terminal window."""
        self.text_area.append(text)
        cursor = self.text_area.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.text_area.setTextCursor(cursor)

    def clear(self):
        self.text_area.clear()
