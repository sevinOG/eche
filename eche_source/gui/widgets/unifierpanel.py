# gui/widgets/unifierpanel.py
# Edit core/builder.py in the portable package.

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTextEdit,
    QLabel,
    QFrame,
    QPushButton,
    QMessageBox,
    QHBoxLayout,
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import pyqtSignal

from gui.theme import APP_NAME


def _read_path() -> str:
    try:
        from core.paths import readable_core_file
        return readable_core_file("builder.py")
    except Exception:
        return os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "core", "builder.py")
        )


def _write_path() -> str:
    try:
        from core.paths import writable_core_file
        return writable_core_file("builder.py")
    except Exception:
        return _read_path()


class UnifierPanel(QWidget):
    content_saved = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Unifier")
        self.resize(720, 560)
        self.current_file_path: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Unifier")
        title.setObjectName("Title")
        root.addWidget(title)
        self.path_label = QLabel("core/builder.py")
        self.path_label.setObjectName("Subtitle")
        self.path_label.setWordWrap(True)
        root.addWidget(self.path_label)
        hint = QLabel(
            "Orchestration / builder prompt source. Saves to package core/builder.py "
            "(plain text — not encrypted). Restart the bot after saving."
        )
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(8)

        label = QLabel("BUILDER.PY")
        label.setObjectName("PanelTitle")
        card_layout.addWidget(label)

        self.text = QTextEdit()
        self.text.setFont(QFont("Consolas", 10))
        self.text.setPlaceholderText("File content will appear here…")
        self.text.setReadOnly(False)
        card_layout.addWidget(self.text)

        row = QHBoxLayout()
        row.addStretch()
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.on_save_clicked)
        row.addWidget(self.save_button)
        card_layout.addLayout(row)

        root.addWidget(card, stretch=1)

        # Default-load package builder
        self.load_file_content(_read_path())

    def load_file_content(self, file_path: str | None = None):
        path = file_path or _read_path()
        self.current_file_path = path
        self.path_label.setText(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.text.setPlainText(content)
            self.setWindowTitle(f"{APP_NAME} — Unifier · {os.path.basename(path)}")
            self.save_button.setEnabled(True)
        except FileNotFoundError:
            self.text.setPlainText(f"File not found: {path}")
            self.save_button.setEnabled(False)
        except Exception as e:
            self.text.setPlainText(f"Error loading {path}: {e}")
            self.save_button.setEnabled(False)

    def on_save_clicked(self):
        content = self.text.toPlainText()
        target = _write_path()
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            self.current_file_path = target
            self.path_label.setText(target)
            self.content_saved.emit(target, content)
            self.setWindowTitle(
                f"{APP_NAME} — Unifier · {os.path.basename(target)} (Saved)"
            )
            QMessageBox.information(
                self,
                "Saved",
                f"Builder source updated (not encrypted):\n{target}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
