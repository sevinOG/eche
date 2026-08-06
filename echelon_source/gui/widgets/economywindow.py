# gui/widgets/economywindow.py
# Bank pin browser — same location rules as cogs/economy/bet.py + bank.py:
#   HOME_SERVER_ID guild
#   category  memory-{user_id}
#   channel   economy
#   pin       content.startswith("BANK DATA")
#             lines: BANK DATA / {balance} / STARTER:0|1

from __future__ import annotations

import asyncio
import os
import re

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
    QDoubleSpinBox,
)

from gui.theme import APP_NAME
from gui.widgets.loading import LoadingIndicator

try:
    from core.paths import ensure_user_layout
    PROJECT_ROOT = ensure_user_layout()
except Exception:
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Match bet.py / bank.py exactly
ECONOMY_CHANNEL_NAME = "economy"
MEMORY_CAT_RE = re.compile(r"^memory-(\d+)$", re.IGNORECASE)


def load_settings():
    from core.secrets import load_all
    return load_all(PROJECT_ROOT)


def parse_bank(text: str) -> tuple[float, str, str]:
    raw = text or ""
    lines = raw.splitlines()
    bal = 0.0
    starter = "STARTER:1"
    if lines and lines[0].strip().startswith("BANK DATA"):
        if len(lines) >= 2:
            try:
                bal = round(float(lines[1].strip()), 2)
            except Exception:
                bal = 0.0
        if len(lines) >= 3:
            starter = lines[2].strip() or starter
    return bal, starter, raw


def format_bank(balance: float, starter: str = "STARTER:1") -> str:
    return f"BANK DATA\n{round(float(balance), 2):.2f}\n{(starter or 'STARTER:1').strip()}"


async def _resolve_guild(client, home_id: int):
    """Same as bet: prefer get_guild (full channel cache)."""
    import discord

    guild = client.get_guild(home_id)
    if guild is not None:
        return guild
    guild = discord.utils.get(client.guilds, id=home_id)
    if guild is not None:
        return guild
    return None


async def _find_economy_channel(guild, user_id: str | int):
    """
    Mirror bet.load_balance:
      category = memory-{user_id}
      channel  = economy under that category
    """
    import discord

    uid = str(user_id)
    category = discord.utils.get(guild.categories, name=f"memory-{uid}")
    if category is None:
        # categories sometimes partial — scan all
        for cat in guild.categories:
            if (cat.name or "").lower() == f"memory-{uid}":
                category = cat
                break
    if category is None:
        return None, None

    economy_channel = discord.utils.get(category.text_channels, name=ECONOMY_CHANNEL_NAME)
    if economy_channel is None:
        # Fall back: text channels in category
        for ch in getattr(category, "channels", []) or []:
            if getattr(ch, "name", None) == ECONOMY_CHANNEL_NAME:
                economy_channel = ch
                break
    if economy_channel is None:
        # Last resort: guild text channels with matching category_id
        for ch in guild.text_channels:
            if ch.name == ECONOMY_CHANNEL_NAME and ch.category_id == category.id:
                economy_channel = ch
                break
    return category, economy_channel


async def _read_bank_pin(economy_channel):
    """pins where content.startswith('BANK DATA') — same filter as bet.py."""
    if economy_channel is None:
        return None, ""
    pins = await economy_channel.pins()
    bank_messages = [
        m for m in pins
        if (m.content or "").startswith("BANK DATA")
    ]
    if not bank_messages:
        return None, ""
    msg = bank_messages[0]
    return msg, msg.content or ""


class EconomyWorker(QThread):
    finished_ok = pyqtSignal(bool, str, object)

    def __init__(
        self,
        action: str,
        server_id: str = "",
        user_id: str = "",
        content: str = "",
    ):
        super().__init__()
        self.action = action  # list_users | fetch | save
        self.server_id = str(server_id or "")
        self.user_id = str(user_id or "")
        self.content = content

    def run(self):
        try:
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

        # Always home server — bank.py / bet.py only store balances there
        home_raw = (
            self.server_id
            or (settings.get("home_server_id") or "").strip()
            or (os.getenv("HOME_SERVER_ID") or "").strip()
        )
        if not home_raw:
            self.finished_ok.emit(
                False,
                "HOME_SERVER_ID is not set. Economy lives on the home guild only.",
                None,
            )
            return
        try:
            home_id = int(home_raw)
        except ValueError:
            self.finished_ok.emit(False, f"Invalid home server id: {home_raw}", None)
            return

        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        intents.members = True
        client = discord.Client(intents=intents)

        payload = None
        message = "ok"

        @client.event
        async def on_ready():
            nonlocal payload, message
            try:
                guild = await _resolve_guild(client, home_id)
                if guild is None:
                    message = (
                        f"Home guild {home_id} not found. "
                        "Is the bot invited to that server?"
                    )
                    payload = None
                    return

                if self.action == "list_users":
                    # Prefer opted-in users (same population economy tools use),
                    # then any memory-* categories that already exist.
                    user_ids: set[str] = set()
                    try:
                        from core.opt_in_manager import load_opted_in
                        user_ids |= {str(x) for x in load_opted_in()}
                    except Exception:
                        pass
                    for cat in guild.categories:
                        m = MEMORY_CAT_RE.match(cat.name or "")
                        if m:
                            user_ids.add(m.group(1))

                    users = []
                    for uid in sorted(user_ids, key=lambda x: int(x) if x.isdigit() else 0):
                        cat, econ = await _find_economy_channel(guild, uid)
                        bal_preview = "—"
                        raw = ""
                        has_bank = False
                        if econ is not None:
                            _pin, raw = await _read_bank_pin(econ)
                            if raw:
                                has_bank = True
                                b, _, _ = parse_bank(raw)
                                bal_preview = f"{b:.2f}"
                            else:
                                bal_preview = "no pin"
                        else:
                            bal_preview = "no channel"

                        display = uid
                        try:
                            member = guild.get_member(int(uid))
                            if member:
                                display = member.display_name
                            else:
                                u = await client.fetch_user(int(uid))
                                display = getattr(u, "global_name", None) or u.name or uid
                        except Exception:
                            pass

                        users.append({
                            "id": uid,
                            "display_name": display,
                            "balance_preview": bal_preview,
                            "raw": raw,
                            "has_bank": has_bank,
                            "has_channel": econ is not None,
                            "guild_name": guild.name,
                        })

                    # Show people with banks first
                    users.sort(
                        key=lambda u: (
                            0 if u.get("has_bank") else 1,
                            (u.get("display_name") or u["id"]).lower(),
                        )
                    )
                    payload = {
                        "users": users,
                        "guild_id": str(guild.id),
                        "guild_name": guild.name,
                    }
                    message = (
                        f"{len(users)} users on {guild.name} "
                        f"({sum(1 for u in users if u.get('has_bank'))} with BANK DATA pins)"
                    )

                elif self.action == "fetch":
                    cat, econ = await _find_economy_channel(guild, self.user_id)
                    if cat is None:
                        message = f"No memory-{self.user_id} category on home server."
                        payload = {"text": "", "user_id": self.user_id}
                        return
                    if econ is None:
                        message = f"No #{ECONOMY_CHANNEL_NAME} channel under memory-{self.user_id}."
                        payload = {"text": "", "user_id": self.user_id}
                        return
                    _pin, text = await _read_bank_pin(econ)
                    if not text:
                        # Match bet: empty pin is still a valid location
                        text = format_bank(0.0, "STARTER:0")
                        message = "Economy channel found — no BANK DATA pin yet (showing template)"
                    else:
                        message = "Loaded BANK DATA pin from Discord"
                    payload = {
                        "text": text,
                        "user_id": self.user_id,
                        "channel_id": econ.id,
                    }

                elif self.action == "save":
                    cat, econ = await _find_economy_channel(guild, self.user_id)
                    if cat is None:
                        # Create like bet does when missing
                        cat = await guild.create_category(f"memory-{self.user_id}")
                    if econ is None:
                        econ = await cat.create_text_channel(ECONOMY_CHANNEL_NAME)

                    content = (self.content or "").strip()
                    if not content.startswith("BANK DATA"):
                        try:
                            bal = float(content.split()[0])
                            content = format_bank(bal)
                        except Exception:
                            content = format_bank(0.0)

                    _pin, existing = await _read_bank_pin(econ)
                    pins = await econ.pins()
                    bank_messages = [
                        m for m in pins if (m.content or "").startswith("BANK DATA")
                    ]
                    if bank_messages:
                        await bank_messages[0].edit(content=content)
                        text = bank_messages[0].content
                    else:
                        msg = await econ.send(content)
                        await msg.pin()
                        text = msg.content
                    payload = {"text": text, "user_id": self.user_id}
                    message = "Saved BANK DATA pin to Discord"
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


class EconomyWindow(QMainWindow):
    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window
        self.setWindowTitle(f"{APP_NAME} — Economy")
        self.resize(920, 620)
        self.setMinimumSize(700, 460)

        self._home_id = ""
        self._user_id = ""
        self._cache: dict[str, dict] = {}
        self._worker: EconomyWorker | None = None
        self._starter = "STARTER:1"

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Economy (bank pins)")
        title.setObjectName("Title")
        titles.addWidget(title)
        sub = QLabel(
            "Balances live on the Home Server only — same as ?bet / bank: "
            "memory-{{user_id}} → #economy → pinned message starting with BANK DATA."
        )
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        titles.addWidget(sub)
        head.addLayout(titles, stretch=1)
        self.loader = LoadingIndicator()
        head.addWidget(self.loader, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        self.home_label = QLabel("Home server: …")
        self.home_label.setObjectName("FieldHint")
        root.addWidget(self.home_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        mid = QFrame()
        mid.setObjectName("Panel")
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(10, 10, 10, 10)
        ml.addWidget(self._pt("USERS (HOME SERVER)"))
        self.user_list = QListWidget()
        self.user_list.currentItemChanged.connect(self._on_user)
        ml.addWidget(self.user_list, stretch=1)
        refresh = QPushButton("Refresh from Discord")
        refresh.setObjectName("ghost")
        refresh.clicked.connect(self._bootstrap)
        ml.addWidget(refresh)
        splitter.addWidget(mid)

        right = QFrame()
        right.setObjectName("Panel")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 10, 10, 10)
        rl.addWidget(self._pt("BANK DATA PIN"))
        self.source_label = QLabel("Pick a user to load their bank pin.")
        self.source_label.setObjectName("FieldHint")
        self.source_label.setWordWrap(True)
        rl.addWidget(self.source_label)

        bal_row = QHBoxLayout()
        bal_lab = QLabel("Balance")
        bal_lab.setObjectName("FieldLabel")
        bal_row.addWidget(bal_lab)
        self.balance_spin = QDoubleSpinBox()
        self.balance_spin.setRange(-1_000_000_000, 1_000_000_000)
        self.balance_spin.setDecimals(2)
        self.balance_spin.setSingleStep(10.0)
        self.balance_spin.setMinimumHeight(34)
        self.balance_spin.valueChanged.connect(self._sync_editor_from_spin)
        bal_row.addWidget(self.balance_spin, stretch=1)
        rl.addLayout(bal_row)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("BANK DATA\n500.00\nSTARTER:1")
        self.editor.setMaximumHeight(160)
        self.editor.textChanged.connect(self._sync_spin_from_editor)
        rl.addWidget(self.editor)
        rl.addStretch(1)

        erow = QHBoxLayout()
        erow.addStretch()
        save_btn = QPushButton("Save to Discord")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        erow.addWidget(save_btn)
        rl.addLayout(erow)
        splitter.addWidget(right)

        splitter.setSizes([320, 520])
        root.addWidget(splitter, stretch=1)

        self.status = QLabel("Connecting…")
        self.status.setObjectName("FieldHint")
        root.addWidget(self.status)

        self._bootstrap()

    def _pt(self, t: str) -> QLabel:
        lab = QLabel(t)
        lab.setObjectName("PanelTitle")
        return lab

    def _log(self, msg: str):
        self.status.setText(msg)
        if self.main_window and hasattr(self.main_window, "append_log"):
            self.main_window.append_log(f"[economy] {msg}")

    def _set_busy(self, busy: bool, msg: str = "Working…"):
        self.loader.set_busy(busy, msg)
        if self.main_window and hasattr(self.main_window, "set_loading"):
            # Only drive main toolbar for economy when main is offline/busy-safe
            if busy:
                self.main_window.set_loading(True, msg)

    def _busy(self) -> bool:
        return bool(self._worker and self._worker.isRunning())

    def _start(self, worker: EconomyWorker, slot):
        if self._busy():
            QMessageBox.information(self, "Busy", "Another economy operation is running.")
            return
        self._worker = worker
        worker.finished_ok.connect(slot)
        worker.start()

    def _bootstrap(self):
        cfg = load_settings()
        self._home_id = (cfg.get("home_server_id") or "").strip()
        self.home_label.setText(
            f"Home server (bank storage): {self._home_id or '(not set — open Settings → Discord)'}"
        )
        if not self._home_id:
            self._log("Set Home Server ID in Settings first.")
            QMessageBox.warning(
                self,
                "Home server missing",
                "Economy balances are stored on the Home Server only "
                "(same as ?bet). Set Home Server ID under Settings → Discord.",
            )
            return
        self._set_busy(True, "Loading banks…")
        self._start(
            EconomyWorker("list_users", server_id=self._home_id),
            self._on_users,
        )

    def _on_users(self, ok: bool, message: str, payload):
        self._set_busy(False)
        if self.main_window and hasattr(self.main_window, "set_loading"):
            # clear transient busy without forcing offline if bot is up
            pass
        self.user_list.clear()
        self._cache.clear()
        if not ok:
            QMessageBox.warning(self, "Discord", message)
            self._log(message)
            return
        users = (payload or {}).get("users") if isinstance(payload, dict) else (payload or [])
        gname = (payload or {}).get("guild_name") if isinstance(payload, dict) else ""
        if gname:
            self.home_label.setText(
                f"Home server: {gname} ({self._home_id}) — bank pins via memory-*/economy"
            )
        for u in users:
            uid = str(u["id"])
            self._cache[uid] = u
            label = f"{u.get('display_name') or uid}  ·  {u.get('balance_preview', '—')}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, uid)
            self.user_list.addItem(item)
        self._log(message)
        if self.user_list.count():
            self.user_list.setCurrentRow(0)

    def _on_user(self, current, _prev):
        if not current:
            return
        self._user_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        cached = self._cache.get(self._user_id) or {}
        raw = (cached.get("raw") or "").strip()
        if raw:
            self._apply_text(raw)
            self.source_label.setText(
                f"Discord · memory-{self._user_id} / #{ECONOMY_CHANNEL_NAME} · BANK DATA pin"
            )
            return
        self._set_busy(True, "Fetching bank…")
        self._start(
            EconomyWorker("fetch", server_id=self._home_id, user_id=self._user_id),
            self._on_fetched,
        )

    def _apply_text(self, text: str):
        bal, starter, _ = parse_bank(text)
        self._starter = starter
        self.editor.blockSignals(True)
        self.balance_spin.blockSignals(True)
        self.editor.setPlainText(text if text.strip() else format_bank(bal, starter))
        self.balance_spin.setValue(bal)
        self.editor.blockSignals(False)
        self.balance_spin.blockSignals(False)

    def _on_fetched(self, ok: bool, message: str, payload):
        self._set_busy(False)
        if not ok:
            QMessageBox.warning(self, "Discord", message)
            self._log(message)
            return
        text = (payload or {}).get("text") if isinstance(payload, dict) else ""
        self._apply_text(text or format_bank(0.0, "STARTER:0"))
        self.source_label.setText(
            f"Discord · memory-{self._user_id} / #{ECONOMY_CHANNEL_NAME} · BANK DATA pin"
        )
        self._log(message)

    def _sync_editor_from_spin(self, value: float):
        text = format_bank(value, self._starter)
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)

    def _sync_spin_from_editor(self):
        bal, starter, _ = parse_bank(self.editor.toPlainText())
        self._starter = starter
        self.balance_spin.blockSignals(True)
        self.balance_spin.setValue(bal)
        self.balance_spin.blockSignals(False)

    def _save(self):
        if not self._user_id:
            QMessageBox.information(self, "Select", "Pick a user first.")
            return
        content = format_bank(self.balance_spin.value(), self._starter)
        self.editor.blockSignals(True)
        self.editor.setPlainText(content)
        self.editor.blockSignals(False)
        self._set_busy(True, "Saving bank…")
        self._start(
            EconomyWorker(
                "save",
                server_id=self._home_id,
                user_id=self._user_id,
                content=content,
            ),
            self._on_saved,
        )

    def _on_saved(self, ok: bool, message: str, payload):
        self._set_busy(False)
        if not ok:
            QMessageBox.warning(self, "Discord", message)
            self._log(message)
            return
        text = (payload or {}).get("text") if isinstance(payload, dict) else None
        if text:
            self._apply_text(text)
            if self._user_id in self._cache:
                self._cache[self._user_id]["raw"] = text
                b, _, _ = parse_bank(text)
                self._cache[self._user_id]["balance_preview"] = f"{b:.2f}"
        for i in range(self.user_list.count()):
            it = self.user_list.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) == self._user_id:
                u = self._cache.get(self._user_id) or {}
                it.setText(
                    f"{u.get('display_name') or self._user_id}  ·  {u.get('balance_preview', '—')}"
                )
                break
        self._log(message)
        QMessageBox.information(self, "Saved", message)
