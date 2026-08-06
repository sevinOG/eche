# gui/widgets/botmemorywindow.py

from __future__ import annotations

import asyncio
import os
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QLabel,
    QFrame,
    QMessageBox,
    QSizePolicy,
)

from gui.theme import APP_NAME

try:
    from core.paths import ensure_user_layout
    PROJECT_ROOT = ensure_user_layout()
except Exception:
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_settings():
    from core.secrets import load_all
    return load_all(PROJECT_ROOT)


class BotMemoryWorker(QThread):
    finished_fetch = pyqtSignal(bool, str, object) # success, content, pin_message_obj

    def __init__(self, action: str, new_content: str = ""):
        super().__init__()
        self.action = action # fetch, save, delete
        self.new_content = new_content

    def run(self):
        try:
            import discord
            from dotenv import load_dotenv
            load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

            settings = load_settings()
            token = (settings.get("discord_token") or "").strip() or os.getenv("DISCORD_TOKEN")
            home_server_id = settings.get("home_server_id") or os.getenv("HOME_SERVER_ID")

            if not token:
                self.finished_fetch.emit(False, "Discord token missing. Set it in Settings.", None)
                return
            if not home_server_id:
                self.finished_fetch.emit(False, "Home Server ID missing. Set it in Settings.", None)
                return

            intents = discord.Intents.default()
            intents.guilds = True
            intents.messages = True
            intents.message_content = True

            client = discord.Client(intents=intents)

            fetched_text = ""
            target_pin = None

            @client.event
            async def on_ready():
                nonlocal fetched_text, target_pin
                try:
                    guild = client.get_guild(int(home_server_id))
                    if not guild:
                        guild = await client.fetch_guild(int(home_server_id))
                    
                    category = discord.utils.get(guild.categories, name="bot-memory")
                    channel = None
                    if category:
                        channel = discord.utils.get(category.channels, name="context")
                    if not channel:
                        # Fallback search all text channels
                        channel = discord.utils.get(guild.text_channels, name="context")

                    if not channel:
                        fetched_text = "Self context channel not found yet (bot hasn't initialized it)."
                        await client.close()
                        return

                    pins = await channel.pins()
                    if not pins:
                        fetched_text = "No pinned self context message found yet."
                        await client.close()
                        return

                    pin = pins[0]
                    target_pin = pin

                    if self.action == "fetch":
                        fetched_text = pin.content
                    elif self.action == "save":
                        await pin.edit(content=self.new_content)
                        fetched_text = pin.content
                    elif self.action == "delete":
                        await pin.edit(content="Self Conversation Data (Group Setting):\n\nSummary:\n(none yet)\n\nNew:\n")
                        fetched_text = pin.content

                except Exception as ex:
                    fetched_text = f"Error communicating with Discord: {ex}"
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(client.start(token))

            self.finished_fetch.emit(True, fetched_text, None)
        except Exception as e:
            self.finished_fetch.emit(False, str(e), None)


class BotMemoryWindow(QMainWindow):
    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window
        self.setWindowTitle(f"{APP_NAME} — Self Memory (Bot)")
        self.resize(700, 600)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Self Memory (Bot Context)")
        title.setObjectName("Title")
        titles.addWidget(title)

        subtitle = QLabel("Displays and allows direct editing or resetting of the bot's pinned self-context message.")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        titles.addWidget(subtitle)
        head.addLayout(titles, stretch=1)

        from gui.widgets.loading import LoadingIndicator
        self.loader = LoadingIndicator()
        head.addWidget(self.loader, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(8)

        head = QLabel("PINNED CONTEXT CONTENT")
        head.setObjectName("PanelTitle")
        card_layout.addWidget(head)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Loading self memory from Discord...")
        card_layout.addWidget(self.editor, stretch=1)
        layout.addWidget(card, stretch=1)

        row = QHBoxLayout()
        refresh_btn = QPushButton("Fetch from Discord")
        refresh_btn.setObjectName("ghost")
        refresh_btn.clicked.connect(self.fetch_memory)
        row.addWidget(refresh_btn)

        row.addStretch()

        delete_btn = QPushButton("Reset / Clear")
        delete_btn.setObjectName("danger")
        delete_btn.clicked.connect(self.delete_memory)
        row.addWidget(delete_btn)

        save_btn = QPushButton("Save Changes")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self.save_memory)
        row.addWidget(save_btn)

        layout.addLayout(row)

        self.worker = None
        self.fetch_memory()

    def fetch_memory(self):
        self.loader.set_busy(True, "Fetching memory…")
        self.editor.setEnabled(False)
        self.editor.setPlainText("Connecting to Discord and fetching self context...")
        self.worker = BotMemoryWorker("fetch")
        self.worker.finished_fetch.connect(self.on_worker_finished)
        self.worker.start()

    def save_memory(self):
        content = self.editor.toPlainText()
        if not content.strip():
            QMessageBox.warning(self, "Empty", "Content cannot be empty.")
            return
        self.loader.set_busy(True, "Saving changes…")
        self.editor.setEnabled(False)
        self.worker = BotMemoryWorker("save", new_content=content)
        self.worker.finished_fetch.connect(self.on_worker_finished)
        self.worker.start()

    def delete_memory(self):
        reply = QMessageBox.question(
            self,
            "Reset memory?",
            "Are you sure you want to reset the self context message to blank?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.loader.set_busy(True, "Resetting…")
        self.editor.setEnabled(False)
        self.worker = BotMemoryWorker("delete")
        self.worker.finished_fetch.connect(self.on_worker_finished)
        self.worker.start()

    def on_worker_finished(self, ok: bool, text: str, pin):
        self.loader.set_busy(False)
        self.editor.setEnabled(True)
        if ok:
            self.editor.setPlainText(text)
            if self.main_window and hasattr(self.main_window, "append_log"):
                self.main_window.append_log("[info] Self memory updated/fetched successfully.")
        else:
            self.editor.setPlainText(text)
            QMessageBox.warning(self, "Discord Error", text)
