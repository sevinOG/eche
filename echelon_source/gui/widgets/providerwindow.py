# gui/widgets/providerwindow.py
# Edit core/client.py — the code that talks to your AI provider.

from __future__ import annotations

import os
import subprocess

from PyQt6.QtCore import Qt, QUrl, QProcess
from PyQt6.QtGui import QFont, QDesktopServices
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QMessageBox,
    QLabel,
    QFrame,
    QComboBox,
)

from gui.theme import APP_NAME

GROQ_CONSOLE_URL = "https://console.groq.com/"
GROQ_KEYS_URL = "https://console.groq.com/keys"

PROVIDER_HELP = """
# What is a “provider”?

A **provider** is the company (or your own computer) that **runs the AI model**.
Echelon does **not** invent answers by itself — it sends a message over the internet
to a provider, and the provider’s model writes the reply.

Think of it like this:

| Piece | Everyday analogy |
|--------|------------------|
| Discord | The chat room where people talk |
| Echelon | The robot that listens and posts |
| Provider | The brain service that thinks |
| API key | The password for that brain service |
| Model ID | Which brain (smart/fast/cheap) to use |

## Default setup: Groq

Echelon ships pointed at **Groq** because it is fast and has a free tier.
You do **not** have to stay with Groq forever.

1. Open [console.groq.com](https://console.groq.com/) and create a free account  
2. Create an API key under **API Keys**  
3. Paste that key in **Settings → AI & Model → Provider API Key**  
4. Keep the model id (or pick another live model from Groq’s docs)  

Other providers (OpenAI, OpenRouter, Together, Fireworks, local Ollama, etc.)
often also have free tiers or cheap plans. If they speak “OpenAI-style” chat APIs,
you can usually switch by editing this file’s **URL**, **key env name**, and **model**.

## Where is the provider block in this file?

In `core/client.py` look near the top for:

- `API_URL = "https://api.groq.com/openai/v1/chat/completions"`  
  → **where** requests go (change this to another host if you switch providers)  
- `_api_key()` reading `GROQ_API_KEY`  
  → **password** from Settings / environment  
- `_model()` / `DEFAULT_MODEL`  
  → **which model** to call  
- `call_groq(...)`  
  → the function that actually sends chat messages  

You almost never need to change the rest of the file at first. Start with a key
in Settings; only edit this code if you want a different company or a local model.

## Restart after save

The running bot loads this file when it starts. After you Save, **Kill Bot** then
**Run Bot** so it picks up changes.
"""


def _read_path() -> str:
    try:
        from core.paths import readable_core_file
        return readable_core_file("client.py")
    except Exception:
        return os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "core", "client.py")
        )


def _write_path() -> str:
    try:
        from core.paths import writable_core_file
        return writable_core_file("client.py")
    except Exception:
        return _read_path()


class ProviderWindow(QWidget):
    def __init__(self, settings_window=None):
        super().__init__()
        self.settings_window = settings_window
        self.path = _read_path()
        self.setWindowTitle(f"{APP_NAME} — Provider")
        self.resize(920, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Provider")
        title.setObjectName("Title")
        titles.addWidget(title)
        self.path_label = QLabel(self.path)
        self.path_label.setObjectName("Subtitle")
        self.path_label.setWordWrap(True)
        titles.addWidget(self.path_label)
        head.addLayout(titles, stretch=1)
        help_btn = QPushButton("ℹ How providers work")
        help_btn.setObjectName("ghost")
        help_btn.setMinimumHeight(36)
        help_btn.clicked.connect(self._show_help)
        head.addWidget(help_btn, alignment=Qt.AlignmentFlag.AlignTop)
        from gui.widgets.loading import LoadingIndicator
        self.loader = LoadingIndicator()
        self.loader.set_state("offline")
        head.addWidget(self.loader, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        # (Removed local models browser from ProviderWindow)

        note = QLabel(
            "This file is the “phone line” to your AI provider. "
            "Default is **Cloud / Groq** (free tier at console.groq.com). "
            "Prefer the Settings dropdown for Cloud vs Ollama — edit this file for advanced URL tweaks. "
            "Look near the top for GROQ_API_URL / OLLAMA_API_URL, _provider_backend(), and DEFAULT_MODEL. "
            "Saves to package core/client.py (plain text). Restart the bot after saving."
        )
        note.setObjectName("FieldHint")
        note.setWordWrap(True)
        layout.addWidget(note)

        link_row = QHBoxLayout()
        link_row.setSpacing(8)
        groq_btn = QPushButton("Groq console")
        groq_btn.setObjectName("link")
        groq_btn.setToolTip("Optional default provider — free tier at console.groq.com")
        groq_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GROQ_CONSOLE_URL)))
        link_row.addWidget(groq_btn)
        keys_btn = QPushButton("API keys")
        keys_btn.setObjectName("link")
        keys_btn.setToolTip("Create a free API key (only if you use Groq)")
        keys_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GROQ_KEYS_URL)))
        link_row.addWidget(keys_btn)
        link_row.addStretch()
        layout.addLayout(link_row)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(8)

        label = QLabel("CLIENT.PY  ·  provider block near the top")
        label.setObjectName("PanelTitle")
        card_layout.addWidget(label)

        self.editor = QTextEdit(self)
        self.editor.setAcceptRichText(False)
        self.editor.setFont(QFont("Consolas", 10))
        card_layout.addWidget(self.editor)
        layout.addWidget(card, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
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

    def _show_help(self):
        from gui.widgets.dialogs import show_info
        show_info(self, "Providers — plain-language guide", PROVIDER_HELP)



    def load_file(self):
        try:
            self.path = _read_path()
            self.path_label.setText(self.path)
            with open(self.path, "r", encoding="utf-8") as f:
                self.editor.setPlainText(f.read())
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def save_file(self):
        self.loader.set_busy(True, "Saving…")
        try:
            target = _write_path()
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
            self.path = target
            self.path_label.setText(self.path)
            self.loader.set_state("online")
            if self.settings_window and hasattr(self.settings_window, "flash_save_spinner"):
                self.settings_window.flash_save_spinner()
            QMessageBox.information(
                self,
                "Saved",
                f"Provider source updated (not encrypted):\n{self.path}\n\n"
                "Restart the bot (Kill → Run) so it reloads this file.",
            )
        except Exception as e:
            self.loader.set_state("error")
            QMessageBox.critical(self, "Error", str(e))
        finally:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(600, lambda: self.loader.set_state("offline"))
