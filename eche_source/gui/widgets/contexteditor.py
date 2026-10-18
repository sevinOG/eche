# gui/widgets/contexteditor.py
# One editor for the home-server pins: a person's context, the bot's
# context for that person, and a server's episode. Fetch does not create
# a channel. The id field is how you pick who or which server.

from __future__ import annotations

import asyncio
import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.theme import APP_NAME
from gui.widgets.loading import LoadingIndicator

try:
    from core.paths import ensure_user_layout
    PROJECT_ROOT = ensure_user_layout()
except Exception:
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

KIND_USER = "user"
KIND_BOT = "bot"
KIND_EPISODIC = "episodic"
_KINDS = (
    ("User", KIND_USER),
    ("Bot", KIND_BOT),
    ("Episodic", KIND_EPISODIC),
)
_DISCORD_CAP = 2000


def load_settings():
    from core.secrets import load_all
    return load_all(PROJECT_ROOT)


def _valid_id(raw: str) -> bool:
    text = (raw or "").strip()
    return text.isdigit() and len(text) >= 15


class ContextEditorWorker(QThread):
    """Discord read or write for one pin. The window stays on the GUI thread."""

    finished_ok = pyqtSignal(bool, str, object)

    def __init__(self, action: str, kind: str, target_id: str, content: str = ""):
        super().__init__()
        self.action = action  # fetch | save | reset
        self.kind = kind
        self.target_id = (target_id or "").strip()
        self.content = content

    def _finish(self, ok: bool, message: str, text: str = ""):
        self.finished_ok.emit(ok, message, {
            "kind": self.kind,
            "target": self.target_id,
            "text": text,
        })

    def run(self):
        try:
            if not _valid_id(self.target_id):
                if self.kind == KIND_EPISODIC:
                    self._finish(False, "Enter the Discord server ID (the long number).")
                else:
                    self._finish(False, "Enter that person's Discord user ID (the long number).")
                return
            if self.action in ("save", "reset") and self.kind == KIND_EPISODIC:
                from core.episodic import MAX_STORE, episode_prefix

                prefix = episode_prefix(int(self.target_id))
                if self.action == "save" and not (self.content or "").startswith(prefix):
                    self._finish(False, f"The episode pin has to start with {prefix}")
                    return
                if self.action == "save" and len(self.content) > MAX_STORE:
                    self._finish(
                        False,
                        f"An episode pin must be {MAX_STORE} characters or fewer.",
                    )
                    return
            if self.action == "save" and self.kind != KIND_EPISODIC and len(self.content) > _DISCORD_CAP:
                self._finish(False, "Discord messages must be 2000 characters or fewer.")
                return
            self._run_discord()
        except Exception as exc:
            self._finish(False, str(exc))

    def _run_discord(self):
        import discord
        from dotenv import load_dotenv

        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
        settings = load_settings()
        token = (settings.get("discord_token") or "").strip() or os.getenv("DISCORD_TOKEN")
        home_raw = settings.get("home_server_id") or os.getenv("HOME_SERVER_ID")
        if not token:
            self._finish(False, "Discord token missing. Set it in Settings.")
            return
        if not home_raw:
            self._finish(False, "Home Server ID missing. Set it in Settings.")
            return

        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        client = discord.Client(intents=intents)
        outcome = {"ok": False, "message": "Could not reach Discord.", "text": ""}

        @client.event
        async def on_ready():
            try:
                outcome.update(await self._on_ready(client, home_raw))
            except Exception as exc:
                outcome["ok"] = False
                outcome["message"] = f"Error communicating with Discord: {exc}"
                outcome["text"] = ""
            finally:
                await client.close()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(client.start(token))
        finally:
            loop.close()
        self._finish(outcome["ok"], outcome["message"], outcome["text"])

    async def _home_guild(self, client, home_raw: str):
        from core.home_id import parse_home_server_id

        guild_id = parse_home_server_id(str(home_raw))
        if not guild_id:
            return None, (
                "Home Server ID is not a Discord server ID. "
                "Copy Server ID, not the server icon link."
            )
        guild = client.get_guild(guild_id)
        if not guild:
            try:
                guild = await client.fetch_guild(guild_id)
            except Exception:
                guild = None
        if guild is None:
            return None, "Home server not found. Is the bot in that server?"
        try:
            await guild.fetch_channels()
        except Exception:
            pass
        return guild, ""

    async def _on_ready(self, client, home_raw: str) -> dict:
        guild, problem = await self._home_guild(client, home_raw)
        if guild is None:
            return {"ok": False, "message": problem, "text": ""}
        if self.kind == KIND_USER:
            return await self._user(guild)
        if self.kind == KIND_BOT:
            return await self._bot(guild)
        return await self._episode(guild)

    async def _user(self, guild) -> dict:
        from core import user_context as uc

        if self.action == "fetch":
            text = await uc.discord_fetch_user_context(guild, self.target_id)
            if not text:
                return {
                    "ok": True,
                    "message": "No user context pin yet. Save creates it.",
                    "text": "",
                }
            return {"ok": True, "message": "Loaded the user pin.", "text": text}
        if self.action == "save":
            text = await uc.discord_save_user_context(guild, self.target_id, self.content)
            return {"ok": True, "message": "Saved the user pin.", "text": text}
        return {"ok": False, "message": f"Unknown action {self.action}", "text": ""}

    async def _bot(self, guild) -> dict:
        from core.discord_store import (
            THREAD_CONTEXT,
            find_bot_record,
            find_thread,
            user_channel,
        )

        channel = user_channel(guild, int(self.target_id))
        if channel is None:
            return {
                "ok": False,
                "message": (
                    f"No user-{self.target_id} channel in bot memory yet. "
                    "Fetch does not create one."
                ),
                "text": "",
            }
        thread = await find_thread(channel, THREAD_CONTEXT)
        if thread is None:
            return {"ok": False, "message": "That user has no context thread yet.", "text": ""}
        pin = await find_bot_record(thread)
        if pin is None:
            return {
                "ok": False,
                "message": (
                    "No bot self-context message in that context thread yet. "
                    "A chat with this user creates it."
                ),
                "text": "",
            }
        if self.action == "fetch":
            return {"ok": True, "message": "Loaded the bot pin.", "text": pin.content or ""}
        if self.action == "save":
            await pin.edit(content=self.content)
            return {"ok": True, "message": "Saved the bot pin.", "text": self.content}
        if self.action == "reset":
            from core.discord_store import bot_memory_initial

            blank = bot_memory_initial(self.target_id)
            await pin.edit(content=blank)
            return {"ok": True, "message": "Reset the bot pin.", "text": blank}
        return {"ok": False, "message": f"Unknown action {self.action}", "text": ""}

    async def _episode(self, guild) -> dict:
        from core.discord_store import (
            THREAD_EPISODIC,
            _open_thread,
            bot_channel,
            find_thread,
            read_pinned,
            write_pinned,
        )
        from core.episodic import episode_prefix

        channel = bot_channel(guild)
        if channel is None:
            return {"ok": False, "message": "No bot channel in bot memory yet.", "text": ""}
        thread = await _open_thread(await find_thread(channel, THREAD_EPISODIC))
        if thread is None:
            return {"ok": False, "message": "No episodic thread yet.", "text": ""}
        prefix = episode_prefix(int(self.target_id))
        if self.action == "fetch":
            message, pins_ok = await read_pinned(thread, prefix)
            if not pins_ok:
                return {"ok": False, "message": "Could not read the episodic pins.", "text": ""}
            if message is None:
                return {
                    "ok": True,
                    "message": "No episode for that server yet. Save creates the pin.",
                    "text": "",
                }
            return {
                "ok": True,
                "message": "Loaded the episode.",
                "text": getattr(message, "content", "") or "",
            }
        if self.action != "save":
            return {"ok": False, "message": f"Unknown action {self.action}", "text": ""}
        written, note = await write_pinned(thread, prefix, self.content)
        if written is None or note in {
            "pin-failed",
            "not-confirmed",
            "pins-failed",
            "edit-failed",
            "create-failed",
            "missing-parent",
        }:
            return {
                "ok": False,
                "message": f"Could not store the episode ({note}).",
                "text": "",
            }
        return {
            "ok": True,
            "message": "Saved the episode.",
            "text": getattr(written, "content", "") or self.content,
        }


class ContextEditorWindow(QMainWindow):
    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window
        self.setWindowTitle(f"{APP_NAME} — Context editor")
        self.resize(920, 620)
        self.setMinimumSize(700, 460)

        self._kind = KIND_USER
        self._worker: ContextEditorWorker | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Context editor")
        title.setObjectName("Title")
        titles.addWidget(title)
        sub = QLabel(
            "Pins live on the home server. Pick User, Bot, or Episodic, "
            "enter an id, then fetch. Fetch does not create a channel."
        )
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        titles.addWidget(sub)
        head.addLayout(titles, stretch=1)
        self.loader = LoadingIndicator()
        head.addWidget(self.loader, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QFrame()
        left.setObjectName("Panel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(6)
        left_layout.addWidget(self._panel_title("RECORD"))
        self.kind_list = QListWidget()
        self.kind_list.setObjectName("ContextKindList")
        for label, kind in _KINDS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, kind)
            self.kind_list.addItem(item)
        self.kind_list.currentItemChanged.connect(self._on_kind_selected)
        left_layout.addWidget(self.kind_list, stretch=1)
        splitter.addWidget(left)

        right = QFrame()
        right.setObjectName("Panel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(8)
        self.pin_title = self._panel_title("USER PIN")
        right_layout.addWidget(self.pin_title)

        id_row = QHBoxLayout()
        self.id_label = QLabel("User ID")
        id_row.addWidget(self.id_label)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("Discord user ID")
        self.id_edit.returnPressed.connect(self.fetch_pin)
        id_row.addWidget(self.id_edit, stretch=1)
        fetch_btn = QPushButton("Fetch")
        fetch_btn.setObjectName("ghost")
        fetch_btn.clicked.connect(self.fetch_pin)
        id_row.addWidget(fetch_btn)
        right_layout.addLayout(id_row)

        self.source_label = QLabel("Enter an id, then fetch.")
        self.source_label.setObjectName("FieldHint")
        self.source_label.setWordWrap(True)
        right_layout.addWidget(self.source_label)

        self.editor = QTextEdit()
        right_layout.addWidget(self.editor, stretch=1)

        row = QHBoxLayout()
        row.addStretch()
        self.reset_btn = QPushButton("Reset / Clear")
        self.reset_btn.setObjectName("danger")
        self.reset_btn.clicked.connect(self.reset_pin)
        row.addWidget(self.reset_btn)
        save_btn = QPushButton("Save to Discord")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self.save_pin)
        row.addWidget(save_btn)
        right_layout.addLayout(row)
        splitter.addWidget(right)

        splitter.setSizes([200, 680])
        root.addWidget(splitter, stretch=1)

        self.status = QLabel("")
        self.status.setObjectName("FieldHint")
        root.addWidget(self.status)

        self.kind_list.setCurrentRow(0)
        self._apply_kind_chrome()

    def _panel_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("PanelTitle")
        return label

    def _log(self, message: str):
        self.status.setText(message)
        if self.main_window and hasattr(self.main_window, "append_log"):
            self.main_window.append_log(f"[context] {message}")

    def _busy(self) -> bool:
        return bool(self._worker and self._worker.isRunning())

    def _set_busy(self, busy: bool, message: str = "Working…"):
        self.loader.set_busy(busy, message)
        self.editor.setEnabled(not busy)
        self.id_edit.setEnabled(not busy)
        self.kind_list.setEnabled(not busy)

    def _apply_kind_chrome(self):
        episodic = self._kind == KIND_EPISODIC
        self.id_label.setText("Server ID" if episodic else "User ID")
        self.id_edit.setPlaceholderText(
            "Discord server ID" if episodic else "Discord user ID"
        )
        self.reset_btn.setVisible(self._kind == KIND_BOT)
        titles = {
            KIND_USER: "USER PIN",
            KIND_BOT: "BOT PIN",
            KIND_EPISODIC: "EPISODE PIN",
        }
        self.pin_title.setText(titles.get(self._kind, "PIN"))
        if self._kind == KIND_USER:
            self.editor.setPlaceholderText("Context for Name:\n\nSummary:\n(none yet)\n")
        elif self._kind == KIND_BOT:
            self.editor.setPlaceholderText("Eche with Name:\n\nSummary:\n(none yet)\n")
        else:
            self.editor.setPlaceholderText("Episodic {server id}:\nSummary:\n")

    def _on_kind_selected(self, current, previous):
        if current is None:
            return
        kind = str(current.data(Qt.ItemDataRole.UserRole) or KIND_USER)
        old = KIND_USER if previous is None else str(previous.data(Qt.ItemDataRole.UserRole) or "")
        self._kind = kind
        if previous is not None:
            crossed = (kind == KIND_EPISODIC) != (old == KIND_EPISODIC)
            if crossed:
                self.id_edit.clear()
            self.editor.clear()
            self.source_label.setText("Enter an id, then fetch.")
        self._apply_kind_chrome()

    def _target(self) -> str | None:
        raw = self.id_edit.text().strip()
        if _valid_id(raw):
            return raw
        if self._kind == KIND_EPISODIC:
            text = "Enter the Discord server ID (the long number)."
        else:
            text = "Enter that person's Discord user ID (the long number)."
        QMessageBox.warning(self, "ID", text)
        return None

    def _start(self, action: str, content: str = ""):
        if self._busy():
            QMessageBox.information(self, "Busy", "Another context operation is running.")
            return
        target = self._target()
        if not target:
            return
        notes = {
            "fetch": "Fetching from Discord…",
            "save": "Saving to Discord…",
            "reset": "Resetting…",
        }
        self._set_busy(True, notes.get(action, "Working…"))
        self._log(notes.get(action, action))
        worker = ContextEditorWorker(action, self._kind, target, content)
        self._worker = worker
        worker.finished_ok.connect(self._on_finished)
        worker.start()

    def fetch_pin(self):
        self._start("fetch")

    def save_pin(self):
        content = self.editor.toPlainText()
        if not content.strip():
            QMessageBox.warning(self, "Empty", "Content cannot be empty.")
            return
        self._start("save", content)

    def reset_pin(self):
        if self._kind != KIND_BOT:
            return
        reply = QMessageBox.question(
            self,
            "Reset memory?",
            "Reset this user's bot self-context to a blank summary?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start("reset")

    def _on_finished(self, ok: bool, message: str, payload):
        self._set_busy(False)
        info = payload if isinstance(payload, dict) else {}
        if info.get("kind") != self._kind or info.get("target") != self.id_edit.text().strip():
            self._log(message or "Finished after the selection changed.")
            return
        text = info.get("text") or ""
        if ok:
            self.editor.setPlainText(text)
            if text.strip():
                self.source_label.setText(self._source_line())
            else:
                self.source_label.setText(message or "Nothing stored yet.")
            self._log(message or "Done.")
            return
        if self._worker is not None and self._worker.action == "fetch":
            self.editor.clear()
        self.source_label.setText(message or "Could not reach Discord.")
        self._log(message or "Could not reach Discord.")
        QMessageBox.warning(self, "Discord", message or "Could not reach Discord.")

    def _source_line(self) -> str:
        target = self.id_edit.text().strip()
        if self._kind == KIND_USER:
            return f"User context · {target}"
        if self._kind == KIND_BOT:
            return f"Bot context · {target}"
        return f"Episode · server {target}"
