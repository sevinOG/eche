# gui/widgets/usercontextwindow.py
# Discord is the source of truth for context pins.
# Local disk is only written when the user clicks "Save Local".

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
    QListWidget,
    QListWidgetItem,
    QSplitter,
)

from gui.theme import APP_NAME
from gui.widgets.loading import LoadingIndicator

try:
    from core.paths import ensure_user_layout
    PROJECT_ROOT = ensure_user_layout()
except Exception:
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_settings():
    from core.secrets import load_all
    return load_all(PROJECT_ROOT)


class ContextWorker(QThread):
    """Background Discord I/O only (no silent local writes)."""

    finished_ok = pyqtSignal(bool, str, object)

    def __init__(
        self,
        action: str,
        server_id: str = "",
        user_id: str = "",
        content: str = "",
    ):
        super().__init__()
        self.action = action  # list_guilds | list_users | fetch | save | save_local
        self.server_id = str(server_id or "")
        self.user_id = str(user_id or "")
        self.content = content

    def run(self):
        try:
            if self.action == "save_local":
                from core import user_context as uc
                path = uc.save_user_context(self.server_id, self.user_id, self.content)
                self.finished_ok.emit(True, f"Saved local snapshot:\n{path}", {
                    "path": path,
                    "text": self.content,
                })
                return
            self._run_discord()
        except Exception as e:
            self.finished_ok.emit(False, str(e), None)

    def _run_discord(self):
        import discord
        from dotenv import load_dotenv

        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
        settings = load_settings()
        token = (settings.get("discord_token") or "").strip() or os.getenv("DISCORD_TOKEN")
        if not token:
            self.finished_ok.emit(False, "Discord token missing. Set it in Settings.", None)
            return

        server_id = self.server_id or (settings.get("home_server_id") or "").strip()
        if not server_id and self.action != "list_guilds":
            self.finished_ok.emit(False, "No server selected.", None)
            return

        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        client = discord.Client(intents=intents)

        payload = None
        message = "ok"

        @client.event
        async def on_ready():
            nonlocal payload, message
            try:
                from core import user_context as uc

                if self.action == "list_guilds":
                    payload = [
                        {"id": str(g.id), "name": g.name}
                        for g in client.guilds
                    ]
                    message = f"{len(payload)} guilds from Discord"
                    return

                guild = client.get_guild(int(server_id))
                if not guild:
                    try:
                        guild = await client.fetch_guild(int(server_id))
                    except Exception:
                        guild = None
                if not guild:
                    message = f"Guild {server_id} not found (is the bot in that server?)."
                    payload = None
                    return

                if self.action == "list_users":
                    # Discord pins only — do not write local cache
                    users = await uc.discord_list_user_contexts(guild)
                    # Strip heavy raw from list payload for UI; keep ids/names
                    payload = [
                        {
                            "id": u["id"],
                            "display_name": u.get("display_name") or u["id"],
                            "summary_preview": u.get("summary_preview") or "",
                            "new_count": u.get("new_count") or 0,
                            "raw": u.get("raw") or "",
                        }
                        for u in users
                    ]
                    message = f"{len(payload)} users with memory categories in {guild.name}"

                elif self.action == "fetch":
                    text = await uc.discord_fetch_user_context(guild, self.user_id)
                    payload = {
                        "server_id": server_id,
                        "user_id": self.user_id,
                        "text": text,
                        "guild": guild.name,
                    }
                    message = "Loaded pin from Discord" if text else "No pin on Discord yet"

                elif self.action == "save":
                    text = await uc.discord_save_user_context(
                        guild, self.user_id, self.content
                    )
                    payload = {
                        "server_id": server_id,
                        "user_id": self.user_id,
                        "text": text,
                    }
                    message = "Saved to Discord pin"
                else:
                    message = f"Unknown action {self.action}"
            except Exception as ex:
                message = str(ex)
                payload = None
            finally:
                await client.close()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(client.start(token))
        finally:
            loop.close()

        if self.action == "fetch":
            self.finished_ok.emit(True, message, payload)
        elif payload is not None:
            self.finished_ok.emit(True, message, payload)
        else:
            self.finished_ok.emit(False, message, None)


class UserContextWindow(QMainWindow):
    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window
        self.setWindowTitle(f"{APP_NAME} — User Context")
        self.resize(920, 620)
        self.setMinimumSize(700, 460)

        self._server_id = ""
        self._user_id = ""
        self._users_cache: dict[str, dict] = {}  # user_id -> list row (may include raw)
        self._worker: ContextWorker | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("User Context")
        title.setObjectName("Title")
        titles.addWidget(title)
        sub = QLabel(
            "Reads and writes Discord memory pins (memory-{{user_id}} / context). "
            "Optional Save Local keeps a file snapshot under context/ — only when you ask."
        )
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        titles.addWidget(sub)
        head.addLayout(titles, stretch=1)
        self.loader = LoadingIndicator()
        head.addWidget(self.loader, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Servers
        left = QFrame()
        left.setObjectName("Panel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 10, 10, 10)
        ll.setSpacing(6)
        ll.addWidget(self._panel_title("SERVERS"))
        self.server_list = QListWidget()
        self.server_list.currentItemChanged.connect(self._on_server_selected)
        ll.addWidget(self.server_list, stretch=1)
        splitter.addWidget(left)

        # Users
        mid = QFrame()
        mid.setObjectName("Panel")
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(10, 10, 10, 10)
        ml.setSpacing(6)
        ml.addWidget(self._panel_title("USERS"))
        self.user_list = QListWidget()
        self.user_list.currentItemChanged.connect(self._on_user_selected)
        ml.addWidget(self.user_list, stretch=1)
        splitter.addWidget(mid)

        # Editor
        right = QFrame()
        right.setObjectName("Panel")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 10, 10, 10)
        rl.setSpacing(6)
        rl.addWidget(self._panel_title("DISCORD PIN"))
        self.source_label = QLabel("Select a server — Discord loads automatically.")
        self.source_label.setObjectName("FieldHint")
        self.source_label.setWordWrap(True)
        rl.addWidget(self.source_label)
        self.editor = QTextEdit()
        self.editor.setPlaceholderText(
            "Context for Name:\n\nSummary:\n(none yet)\n\nNew:\n"
        )
        rl.addWidget(self.editor, stretch=1)
        erow = QHBoxLayout()
        erow.addStretch()
        save_local = QPushButton("Save Local")
        save_local.setObjectName("ghost")
        save_local.setToolTip("Optional snapshot to context/{server}/{user}.txt")
        save_local.clicked.connect(self._save_local)
        erow.addWidget(save_local)
        save_discord = QPushButton("Save to Discord")
        save_discord.setObjectName("primary")
        save_discord.clicked.connect(self._save_discord)
        erow.addWidget(save_discord)
        rl.addLayout(erow)
        splitter.addWidget(right)

        splitter.setSizes([200, 220, 480])
        root.addWidget(splitter, stretch=1)

        self.status = QLabel("Connecting to Discord…")
        self.status.setObjectName("FieldHint")
        root.addWidget(self.status)

        # Auto-fetch guilds on open
        self._bootstrap_discord()

    def _panel_title(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("PanelTitle")
        return lab

    def _log(self, msg: str):
        self.status.setText(msg)
        if self.main_window and hasattr(self.main_window, "append_log"):
            self.main_window.append_log(f"[context] {msg}")

    def _set_busy(self, busy: bool, msg: str = "Working…"):
        self.loader.set_busy(busy, msg)
        if self.main_window and hasattr(self.main_window, "set_loading"):
            self.main_window.set_loading(busy, msg)

    def _busy(self) -> bool:
        return bool(self._worker and self._worker.isRunning())

    def _start(self, worker: ContextWorker, slot):
        if self._busy():
            QMessageBox.information(self, "Busy", "Another context operation is running.")
            return
        self._worker = worker
        worker.finished_ok.connect(slot)
        worker.start()

    def _bootstrap_discord(self):
        self._set_busy(True, "Loading guilds…")
        self._log("Fetching guild list from Discord…")
        self._start(ContextWorker("list_guilds"), self._on_guilds)

    def _on_guilds(self, ok: bool, message: str, payload):
        self._set_busy(False)
        self.server_list.clear()
        if not ok:
            QMessageBox.warning(self, "Discord", message)
            self._log(message)
            # Fallback: show home server id so UI isn't empty
            cfg = load_settings()
            home = (cfg.get("home_server_id") or "").strip()
            if home:
                item = QListWidgetItem(f"Home ({home})")
                item.setData(Qt.ItemDataRole.UserRole, home)
                self.server_list.addItem(item)
            return

        cfg = load_settings()
        home = (cfg.get("home_server_id") or "").strip()
        for g in payload or []:
            sid = str(g["id"])
            name = g.get("name") or sid
            label = f"{name}" + ("  ·  home" if sid == home else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self.server_list.addItem(item)

        self._log(message)
        # Auto-select home or first
        if home:
            for i in range(self.server_list.count()):
                it = self.server_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == home:
                    self.server_list.setCurrentItem(it)
                    return
        if self.server_list.count():
            self.server_list.setCurrentRow(0)

    def _on_server_selected(self, current, _previous):
        if not current:
            return
        self._server_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        self.user_list.clear()
        self.editor.clear()
        self._users_cache.clear()
        self._user_id = ""
        if not self._server_id:
            return
        self._set_busy(True, "Loading users…")
        self._log(f"Loading memory categories for {self._server_id}…")
        self._start(
            ContextWorker("list_users", server_id=self._server_id),
            self._on_users,
        )

    def _on_users(self, ok: bool, message: str, payload):
        self._set_busy(False)
        self.user_list.clear()
        self._users_cache.clear()
        if not ok:
            QMessageBox.warning(self, "Discord", message)
            self._log(message)
            return
        for u in payload or []:
            uid = str(u["id"])
            self._users_cache[uid] = u
            label = f"{u.get('display_name') or uid}  ({uid})"
            if u.get("new_count"):
                label += f"  ·  {u['new_count']} new"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, uid)
            self.user_list.addItem(item)
        self._log(message)
        if self.user_list.count():
            self.user_list.setCurrentRow(0)

    def _on_user_selected(self, current, _previous):
        if not current or not self._server_id:
            return
        self._user_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        cached = self._users_cache.get(self._user_id) or {}
        # Prefer raw already loaded with the list to avoid a second Discord login
        raw = (cached.get("raw") or "").strip()
        if raw:
            self.editor.setPlainText(raw)
            self.source_label.setText(
                f"Discord pin · server {self._server_id} · user {self._user_id}"
            )
            self._log("Showing pin from Discord list")
            return
        # Otherwise fetch pin
        self._set_busy(True, "Fetching pin…")
        self._log("Fetching pin from Discord…")
        self._start(
            ContextWorker("fetch", server_id=self._server_id, user_id=self._user_id),
            self._on_fetched,
        )

    def _on_fetched(self, ok: bool, message: str, payload):
        self._set_busy(False)
        if not ok:
            QMessageBox.warning(self, "Discord", message)
            self._log(message)
            return
        text = ""
        if isinstance(payload, dict):
            text = payload.get("text") or ""
        self.editor.setPlainText(text)
        self.source_label.setText(
            f"Discord pin · server {self._server_id} · user {self._user_id}"
        )
        self._log(message)

    def _save_local(self):
        if not self._server_id or not self._user_id:
            QMessageBox.information(self, "Select", "Pick a server and user first.")
            return
        content = self.editor.toPlainText()
        self._set_busy(True, "Saving local…")
        self._start(
            ContextWorker(
                "save_local",
                server_id=self._server_id,
                user_id=self._user_id,
                content=content,
            ),
            self._on_saved_local,
        )

    def _on_saved_local(self, ok: bool, message: str, payload):
        self._set_busy(False)
        if not ok:
            QMessageBox.warning(self, "Save Local", message)
            self._log(message)
            return
        path = (payload or {}).get("path") if isinstance(payload, dict) else ""
        self.source_label.setText(f"Local snapshot · {path}")
        self._log(message)
        QMessageBox.information(self, "Saved locally", message)

    def _save_discord(self):
        if not self._server_id or not self._user_id:
            QMessageBox.information(self, "Select", "Pick a server and user first.")
            return
        content = self.editor.toPlainText()
        if not content.strip():
            QMessageBox.warning(self, "Empty", "Context cannot be empty.")
            return
        self._set_busy(True, "Saving to Discord…")
        self._log("Saving pin to Discord…")
        self._start(
            ContextWorker(
                "save",
                server_id=self._server_id,
                user_id=self._user_id,
                content=content,
            ),
            self._on_saved_discord,
        )

    def _on_saved_discord(self, ok: bool, message: str, payload):
        self._set_busy(False)
        if not ok:
            QMessageBox.warning(self, "Discord", message)
            self._log(message)
            return
        text = (payload or {}).get("text") if isinstance(payload, dict) else None
        if text:
            self.editor.setPlainText(text)
            if self._user_id in self._users_cache:
                self._users_cache[self._user_id]["raw"] = text
        self.source_label.setText(
            f"Discord pin · server {self._server_id} · user {self._user_id}"
        )
        self._log(message)
        QMessageBox.information(self, "Saved", message)
