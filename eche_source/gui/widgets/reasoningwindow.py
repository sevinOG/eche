# Reasoning traces from the provider. Not the chat pane.

from __future__ import annotations

from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
)
from PyQt6.QtGui import QFont

from gui.theme import APP_NAME


class ReasoningWindow(QWidget):
    def __init__(self, on_clear=None):
        super().__init__()
        self._on_clear = on_clear
        self.setWindowTitle(f"{APP_NAME} — Reasoning")
        self.resize(760, 560)
        self._entries: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Reasoning")
        title.setObjectName("Title")
        layout.addWidget(title)
        hint = QLabel("Traces from the provider. Chat still gets the reply only.")
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFont("Consolas", 10))
        self.view.setPlaceholderText("No reasoning yet. A trace shows up here after the next reply.")
        layout.addWidget(self.view, stretch=1)

        row = QHBoxLayout()
        row.addStretch()
        clear = QPushButton("Clear")
        clear.setObjectName("ghost")
        clear.setMinimumHeight(36)
        clear.clicked.connect(self.clear)
        row.addWidget(clear)
        layout.addLayout(row)

    def set_entries(self, entries: list[str]) -> None:
        self._entries = list(entries)
        self.view.setPlainText("\n\n".join(self._entries))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def add_entry(self, provider: str, model: str, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        who = " · ".join(part for part in (provider.strip(), model.strip()) if part)
        head = f"{stamp}  {who}".rstrip()
        body = (text or "").strip() or "(empty)"
        self._entries.append(f"{head}\n{body}")
        self._entries = self._entries[-40:]
        self.set_entries(self._entries)

    def clear(self) -> None:
        if callable(self._on_clear):
            self._on_clear()
            return
        self._entries = []
        self.view.clear()
