# gui/widgets/botmemorywindow.py
# Self context is the second pinned message in a user's context thread:
#   bot memory / user-{id} / context
# Fetch, save, and reset edit that message. They do not create a channel.

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
    QLineEdit,
    QFrame,
    QMessageBox,
)

from gui.theme import APP_NAME

try:
    from core.paths import ensure_user_layout
    PROJECT_ROOT = ensure_user_layout()
except Exception:
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

def _blank_for(user_id: str) -> str:
    from core.discord_store import bot_memory_initial

    return bot_memory_initial(user_id)


def load_settings():
    from core.secrets import load_all
    return load_all(PROJECT_ROOT)


class BotMemoryWorker(QThread):
    finished_fetch = pyqtSignal(bool, str, object)  # success, content, unused

    def __init__(self, action: str, user_id: str, new_content: str = ""):
        super().__init__()
        self.action = action  # fetch, save, delete
        self.user_id = (user_id or "").strip()
        self.new_content = new_content

    def run(self):
        try:
            import discord
            from dotenv import load_dotenv
            load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

            if not self.user_id.isdigit() or len(self.user_id) < 15:
                self.finished_fetch.emit(
                    False,
                    "Enter that person's Discord user ID (the long number).",
                    None,
                )
                return

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

            ok = False
            fetched_text = ""

            @client.event
            async def on_ready():
                nonlocal ok, fetched_text
                try:
                    from core.home_id import parse_home_server_id
                    from core.discord_store import (
                        THREAD_CONTEXT,
                        find_bot_record,
                        find_thread,
                        user_channel,
                    )

                    guild_id = parse_home_server_id(str(home_server_id))
                    if not guild_id:
                        fetched_text = (
                            "Home Server ID is not a Discord server ID. "
                            "Copy Server ID, not the server icon link."
                        )
                        return
                    guild = client.get_guild(guild_id)
                    if not guild:
                        try:
                            guild = await client.fetch_guild(guild_id)
                        except Exception:
                            guild = None
                    if guild is None:
                        fetched_text = "Home server not found. Is the bot in that server?"
                        return
                    try:
                        await guild.fetch_channels()
                    except Exception:
                        pass

                    channel = user_channel(guild, int(self.user_id))
                    if channel is None:
                        fetched_text = (
                            f"No user-{self.user_id} channel in bot memory yet. "
                            "Fetch does not create one."
                        )
                        return
                    thread = await find_thread(channel, THREAD_CONTEXT)
                    if thread is None:
                        fetched_text = "That user has no context thread yet."
                        return
                    pin = await find_bot_record(thread)
                    if pin is None:
                        fetched_text = (
                            "No bot self-context message in that context thread yet. "
                            "A chat with this user creates it."
                        )
                        return

                    if self.action == "fetch":
                        fetched_text = pin.content or ""
                    elif self.action == "save":
                        await pin.edit(content=self.new_content)
                        fetched_text = self.new_content
                    elif self.action == "delete":
                        blank = _blank_for(self.user_id)
                        await pin.edit(content=blank)
                        fetched_text = blank
                    else:
                        fetched_text = f"Unknown action {self.action}"
                        return
                    ok = True
                except Exception as ex:
                    fetched_text = f"Error communicating with Discord: {ex}"
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(client.start(token))
            self.finished_fetch.emit(ok, fetched_text, None)
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

        subtitle = QLabel(
            "The bot's self-context for one person lives in their context thread "
            "(bot memory / user-{id} / context), next to that person's own pin. "
            "Enter a user ID. Fetch does not create a channel."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        titles.addWidget(subtitle)
        head.addLayout(titles, stretch=1)

        from gui.widgets.loading import LoadingIndicator
        self.loader = LoadingIndicator()
        head.addWidget(self.loader, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        id_row = QHBoxLayout()
        id_label = QLabel("User ID")
        id_row.addWidget(id_label)
        self.user_id = QLineEdit()
        self.user_id.setPlaceholderText("Discord user ID")
        id_row.addWidget(self.user_id, stretch=1)
        layout.addLayout(id_row)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(8)

        head_label = QLabel("PINNED SELF CONTEXT")
        head_label.setObjectName("PanelTitle")
        card_layout.addWidget(head_label)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Enter a user ID, then Fetch from Discord.")
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

    def _require_user_id(self) -> str | None:
        user_id = self.user_id.text().strip()
        if not user_id.isdigit() or len(user_id) < 15:
            QMessageBox.warning(
                self,
                "User ID",
                "Enter that person's Discord user ID (the long number).",
            )
            return None
        return user_id

    def fetch_memory(self):
        user_id = self._require_user_id()
        if not user_id:
            return
        self.loader.set_busy(True, "Fetching memory…")
        self.editor.setEnabled(False)
        self.editor.setPlainText("Connecting to Discord and fetching self context...")
        self.worker = BotMemoryWorker("fetch", user_id)
        self.worker.finished_fetch.connect(self.on_worker_finished)
        self.worker.start()

    def save_memory(self):
        user_id = self._require_user_id()
        if not user_id:
            return
        content = self.editor.toPlainText()
        if not content.strip():
            QMessageBox.warning(self, "Empty", "Content cannot be empty.")
            return
        if len(content) > 2000:
            QMessageBox.warning(
                self,
                "Too long",
                "Discord messages must be 2000 characters or fewer.",
            )
            return
        self.loader.set_busy(True, "Saving changes…")
        self.editor.setEnabled(False)
        self.worker = BotMemoryWorker("save", user_id, new_content=content)
        self.worker.finished_fetch.connect(self.on_worker_finished)
        self.worker.start()

    def delete_memory(self):
        user_id = self._require_user_id()
        if not user_id:
            return
        reply = QMessageBox.question(
            self,
            "Reset memory?",
            "Reset this user's bot self-context to a blank summary?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.loader.set_busy(True, "Resetting…")
        self.editor.setEnabled(False)
        self.worker = BotMemoryWorker("delete", user_id)
        self.worker.finished_fetch.connect(self.on_worker_finished)
        self.worker.start()

    def on_worker_finished(self, ok: bool, text: str, pin):
        self.loader.set_busy(False)
        self.editor.setEnabled(True)
        self.editor.setPlainText(text or "")
        if ok:
            if self.main_window and hasattr(self.main_window, "append_log"):
                self.main_window.append_log("[info] Self memory updated/fetched successfully.")
        else:
            QMessageBox.warning(self, "Discord Error", text or "Could not reach Discord.")
