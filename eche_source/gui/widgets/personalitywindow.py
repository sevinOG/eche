# gui/widgets/personalitywindow.py

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

from gui.theme import APP_NAME


class PersonalityWindow(QWidget):
    def __init__(self, settings_window=None):
        super().__init__()
        self.settings_window = settings_window
        self.setWindowTitle(f"{APP_NAME} — Personality")
        self.resize(900, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("Personality")
        title.setObjectName("Title")
        head.addWidget(title, stretch=1)
        from gui.widgets.loading import LoadingIndicator
        self.loader = LoadingIndicator()
        self.loader.set_state("offline")
        head.addWidget(self.loader)
        layout.addLayout(head)
        hint = QLabel(
            "Injected as the bot identity layer in every chat prompt. "
            "Saves to package config/personality.txt (plain text — not encrypted)."
        )
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(8)

        label = QLabel("PROMPT")
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
            from core.personality import (
                ensure_personality_file,
                get_personality_prompt,
                personality_path,
            )
            ensure_personality_file()
            self.editor.setPlainText(get_personality_prompt())
            self.setWindowTitle(f"{APP_NAME} — Personality · {personality_path()}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def save_file(self):
        self.loader.set_busy(True, "Saving…")
        try:
            from core.personality import save_personality_prompt
            path = save_personality_prompt(self.editor.toPlainText())
            self.loader.set_state("online")
            if self.settings_window and hasattr(self.settings_window, "flash_save_spinner"):
                self.settings_window.flash_save_spinner()
            QMessageBox.information(self, "Saved", f"Personality updated.\n{path}")
        except Exception as e:
            self.loader.set_state("error")
            QMessageBox.critical(self, "Error", str(e))
        finally:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(600, lambda: self.loader.set_state("offline"))

    def reset_default(self):
        reply = QMessageBox.question(
            self,
            "Reset personality?",
            "Replace the editor contents with the built-in default?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from core.personality import DEFAULT_PERSONALITY
        self.editor.setPlainText(DEFAULT_PERSONALITY)
