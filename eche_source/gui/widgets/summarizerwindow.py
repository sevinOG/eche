# gui/widgets/summarizerwindow.py
# Edit config/summarizer_prompt.txt — same idea as Personality / client.py.

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QMessageBox,
    QLabel,
    QFrame,
)
from PyQt6.QtGui import QFont

from PyQt6.QtCore import Qt

from gui.theme import APP_NAME
from gui.widgets.loading import LoadingIndicator


class SummarizerWindow(QWidget):
    def __init__(self, settings_window=None):
        super().__init__()
        self.settings_window = settings_window
        self.setWindowTitle(f"{APP_NAME} — Summarizer prompt")
        self.resize(900, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Summarizer prompt")
        title.setObjectName("Title")
        titles.addWidget(title)
        self.path_label = QLabel("")
        self.path_label.setObjectName("Subtitle")
        self.path_label.setWordWrap(True)
        titles.addWidget(self.path_label)
        head.addLayout(titles, stretch=1)
        self.loader = LoadingIndicator()
        self.loader.set_state("offline")
        head.addWidget(self.loader, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        hint = QLabel(
            "Used when Eche compresses Discord memory (Summary: block). "
            "Keep the placeholder {combined_for_summary} so history is injected. "
            "Same pattern as editing client.py / personality — plain text, restart bot after change."
        )
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(8)
        label = QLabel("SUMMARIZER PROMPT FILE")
        label.setObjectName("PanelTitle")
        card_layout.addWidget(label)
        self.editor = QTextEdit(self)
        self.editor.setAcceptRichText(False)
        self.editor.setFont(QFont("Consolas", 11))
        card_layout.addWidget(self.editor)
        layout.addWidget(card, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        reset_btn = QPushButton("Reset to Default")
        reset_btn.setObjectName("ghost")
        reset_btn.clicked.connect(self.reset_default)
        btn_row.addWidget(reset_btn)
        reload_btn = QPushButton("Reload")
        reload_btn.setObjectName("ghost")
        reload_btn.clicked.connect(self.load_file)
        btn_row.addWidget(reload_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self.save_file)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self.load_file()

    def load_file(self):
        try:
            from core.summarizer_prompt import (
                ensure_summarizer_prompt_file,
                get_summarizer_prompt,
                summarizer_prompt_path,
            )
            path = ensure_summarizer_prompt_file()
            self.path_label.setText(path)
            self.editor.setPlainText(get_summarizer_prompt())
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def save_file(self):
        self.loader.set_busy(True, "Saving…")
        try:
            from core.summarizer_prompt import save_summarizer_prompt
            path = save_summarizer_prompt(self.editor.toPlainText())
            self.path_label.setText(path)
            self.loader.set_state("online")
            if self.settings_window and hasattr(self.settings_window, "flash_save_spinner"):
                self.settings_window.flash_save_spinner()
            QMessageBox.information(
                self,
                "Saved",
                f"Summarizer prompt updated:\n{path}\n\n"
                "Restart the bot so it reloads this file.",
            )
        except Exception as e:
            self.loader.set_state("error")
            QMessageBox.critical(self, "Error", str(e))
        finally:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(600, lambda: self.loader.set_state("offline"))

    def reset_default(self):
        reply = QMessageBox.question(
            self,
            "Reset summarizer prompt?",
            "Replace the editor with the built-in default?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from core.summarizer_prompt import DEFAULT_SUMMARIZER_PROMPT
        self.editor.setPlainText(DEFAULT_SUMMARIZER_PROMPT.strip())
