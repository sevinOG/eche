# gui/main.py — Eche v1 control panel

from __future__ import annotations

import os
import sys
import time
import subprocess
import psutil
import atexit
import traceback

from gui.watchdog import ensure_single_gui_instance, cleanup_lockfile

if not ensure_single_gui_instance():
    sys.exit(0)

atexit.register(cleanup_lockfile)

os.environ["ECHE_RUNNING"] = "GUI"

BOT_STARTED = False

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QCheckBox,
    QTextEdit,
    QPlainTextEdit,
    QLabel,
    QSplitter,
    QFrame,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from gui.theme import APP_TITLE, APP_VERSION, apply_theme
from gui.widgets.settingswindow import SettingsWindow
from gui.widgets.unifierpanel import UnifierPanel
from gui.widgets.cogmanager import CogManagerWindow
from gui.widgets.botmemorywindow import BotMemoryWindow
from gui.widgets.loading import LoadingIndicator
from gui.widgets.logpane import LogPane
from gui.widgets.dialogs import (
    present_failure,
    show_error,
    looks_like_traceback,
)

# Tip jar (Cash App + on-chain Bitcoin)
CASHAPP_TAG = "$reshi7"
CASHAPP_URL = "https://cash.app/$reshi7"
BTC_ADDRESS = "bc1qp989v95u54zpnmw9j75azwp9hrqnd0k6d7jp3lvv6z3yywpfdutszkkhg6"

# --- Path Resolution Logic ---
try:
    from core.paths import is_frozen, user_dir, ensure_user_layout, bundle_file
except ImportError:
    print("WARNING: core.paths module not found. Using fallback path resolution.")

    def is_frozen():
        return bool(getattr(sys, "frozen", False))

    def user_dir_fallback():
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.abspath(os.path.join(script_dir, ".."))

    def ensure_user_layout_fallback():
        root = user_dir_fallback()
        os.makedirs(os.path.join(root, "config"), exist_ok=True)
        return root

    is_frozen = is_frozen
    ensure_user_layout = ensure_user_layout_fallback
# --- End Path Resolution Logic ---

PROJECT_ROOT = ensure_user_layout()
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "settings.json")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE_PATH = os.path.join(LOG_DIR, "gui_log.txt")

os.makedirs(LOG_DIR, exist_ok=True)

try:
    from core.paths import bundle_file as _bundle_file
    _resolved_builder = _bundle_file("core", "builder.py")
except Exception:
    _resolved_builder = None
if _resolved_builder:
    BUILDER_FILE_PATH = _resolved_builder
else:
    BUILDER_FILE_PATH = os.path.join(PROJECT_ROOT, "core", "builder.py")


def load_settings():
    from core.secrets import load_all
    return load_all(PROJECT_ROOT)


class BotReaderThread(QThread):
    line_received = pyqtSignal(str)

    def __init__(self, process):
        super().__init__()
        self.process = process
        self._running = True

    def run(self):
        while self._running and self.process.poll() is None:
            try:
                line = self.process.stdout.readline()
                if line:
                    self.line_received.emit(line.rstrip("\n"))
            except Exception as e:
                print(f"Error reading from bot process stdout: {e}")
                break

    def stop(self):
        self._running = False


class LocalChatWorker(QThread):
    finished_ok = pyqtSignal(str)
    finished_err = pyqtSignal(str)

    def __init__(self, user_text: str, parent=None):
        super().__init__(parent)
        self.user_text = user_text

    def run(self):
        try:
            from core.local_chat import reply_local_sync
            self.finished_ok.emit(reply_local_sync(self.user_text) or "")
        except Exception as e:
            try:
                import asyncio
                from core.local_chat import reply_local
                self.finished_ok.emit(asyncio.run(reply_local(self.user_text)) or "")
            except Exception as e2:
                self.finished_err.emit(str(e2) or str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 780)
        self.setMinimumSize(900, 560)
        self.move(80, 60)

        try:
            from gui.theme import brand_icon
            icon = brand_icon()
            if not icon.isNull():
                self.setWindowIcon(icon)
        except Exception:
            pass

        self.bot_process: subprocess.Popen | None = None
        self.reader_thread: BotReaderThread | None = None

        self.unifier_window = UnifierPanel()
        self.unifier_window.content_saved.connect(self.on_unifier_content_saved)

        self.cog_manager_window = None
        self.settings_window = None
        self.bot_memory_window = None
        self._tb_buffer: list[str] = []
        self._tb_active = False
        self._local_worker = None

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        toolbar = QFrame()
        toolbar.setObjectName("Toolbar")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(14, 12, 14, 12)
        tb.setSpacing(10)

        brand = QVBoxLayout()
        brand.setSpacing(2)
        title = QLabel(APP_TITLE)
        title.setObjectName("Title")
        brand.addWidget(title)
        sub = QLabel("Discord control panel · learn AI settings in the ℹ dialogs")
        sub.setObjectName("Subtitle")
        brand.addWidget(sub)
        tb.addLayout(brand, stretch=1)

        self.loading = LoadingIndicator()
        tb.addWidget(self.loading)

        self.run_button = QPushButton("Run Bot")
        self.run_button.setObjectName("run")
        self.stop_button = QPushButton("Kill Bot")
        self.stop_button.setObjectName("danger")
        self.cog_manager_button = QPushButton("Cogs")
        self.cog_manager_button.setObjectName("ghost")
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("primary")

        self.run_button.clicked.connect(self.on_run_clicked)
        self.stop_button.clicked.connect(self.on_stop_clicked)
        self.settings_button.clicked.connect(self.on_settings_clicked)
        self.cog_manager_button.clicked.connect(self.on_cog_manager_clicked)

        for btn in (
            self.run_button,
            self.stop_button,
            self.cog_manager_button,
            self.settings_button,
        ):
            btn.setMinimumWidth(96)
            btn.setMinimumHeight(36)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            tb.addWidget(btn)

        main_layout.addWidget(toolbar)

        accent = QFrame()
        accent.setObjectName("AccentBar")
        accent.setFixedHeight(2)
        main_layout.addWidget(accent)

        # Chat (Discord mirror) | Local | Logs
        splitter_vertical = QSplitter(Qt.Orientation.Vertical)
        splitter_top = QSplitter(Qt.Orientation.Horizontal)

        self.chat_output = QTextEdit()
        self.chat_output.setReadOnly(True)
        self.log_output = LogPane()

        splitter_top.addWidget(self._panel("Chat", self.chat_output))
        splitter_top.addWidget(self._build_local_panel())
        splitter_top.setStretchFactor(0, 1)
        splitter_top.setStretchFactor(1, 1)
        splitter_top.setSizes([560, 560])

        splitter_vertical.addWidget(splitter_top)
        splitter_vertical.addWidget(self._panel("Logs", self.log_output))
        splitter_vertical.setStretchFactor(0, 3)
        splitter_vertical.setStretchFactor(1, 1)
        splitter_vertical.setSizes([520, 200])

        main_layout.addWidget(splitter_vertical, stretch=1)

        self._reload_local_transcript()

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self.admin_tools_box = QCheckBox("Admin tools")
        self.admin_tools_box.setToolTip(
            "Inject config/admin_tools.md on your turns. "
            "Off, and the bot never sees that file or the admin tools."
        )
        self.admin_tools_box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.admin_tools_box.blockSignals(True)
        self.admin_tools_box.setChecked(self._admin_tools_saved_on())
        self.admin_tools_box.blockSignals(False)
        self.admin_tools_box.toggled.connect(self._on_admin_tools_toggled)
        footer.addWidget(self.admin_tools_box)
        footer.addStretch()
        self.donate_button = QPushButton("pls donate, im poor")
        self.donate_button.setObjectName("donate")
        self.donate_button.setToolTip("Open the tip jar (optional, no pressure)")
        self.donate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.donate_button.clicked.connect(self.on_donate_clicked)
        footer.addWidget(self.donate_button)
        main_layout.addLayout(footer)

        try:
            from core.admin_tools import ensure_admin_tools_file
            ensure_admin_tools_file()
        except Exception:
            pass

        self.set_status("offline")
        self.append_log(f"[INFO] {APP_TITLE} GUI started.")
        self.append_log("[INFO] Open Settings to manage tokens, then Run Bot.")

    def _admin_tools_saved_on(self) -> bool:
        try:
            from core.admin_tools import flag_on
            return flag_on(load_settings().get("admin_tools"), "")
        except Exception:
            return False

    def _on_admin_tools_toggled(self, checked: bool):
        try:
            settings = load_settings()
            settings["admin_tools"] = "1" if checked else "0"
            from core.secrets import save_all
            save_all(settings, PROJECT_ROOT)
            os.environ["ECHE_ADMIN_TOOLS"] = settings["admin_tools"]
        except Exception as e:
            self.append_log(f"[WARN] Could not save admin tools toggle: {e}")
            self.admin_tools_box.blockSignals(True)
            self.admin_tools_box.setChecked(not checked)
            self.admin_tools_box.blockSignals(False)
            return
        if checked:
            self.append_log(
                "[INFO] Admin tools on. config/admin_tools.md is injected for the owner."
            )
        else:
            self.append_log("[INFO] Admin tools off. The bot will not see that file.")
        settings_box = None
        window = getattr(self, "settings_window", None)
        if window is not None:
            settings_box = getattr(window, "security_admin_box", None)
        if settings_box is not None and settings_box.isChecked() != checked:
            settings_box.blockSignals(True)
            settings_box.setChecked(checked)
            settings_box.blockSignals(False)

    def set_loading(self, busy: bool, message: str = "Working…"):
        try:
            if busy:
                self.loading.set_state("busy", message)
        except Exception:
            pass

    def _warn_no_provider(self, settings: dict) -> bool:
        from PyQt6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QCheckBox,
            QFrame,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("No AI provider key")
        dlg.setMinimumWidth(440)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(12)

        title = QLabel("No inference provider set")
        title.setObjectName("Title")
        lay.addWidget(title)

        card = QFrame()
        card.setObjectName("Card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        body = QLabel(
            "The bot will still start and can run games, bank, music, and other "
            "commands — but it <b>cannot invent chat replies</b> until you add a "
            "Provider API Key.\n\n"
            "Default free setup:\n"
            "1. Open Settings → AI & Model\n"
            "2. Get a free key at console.groq.com\n"
            "3. Paste it under Provider API Key → Save\n\n"
            "You can also open Settings → Memory → Edit Provider to change "
            "companies later. Most providers offer free or cheap tiers."
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        cl.addWidget(body)
        lay.addWidget(card)

        dont = QCheckBox("Do not show this warning again")
        lay.addWidget(dont)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(dlg.reject)
        row.addWidget(cancel)
        cont = QPushButton("Run without AI chat")
        cont.setObjectName("primary")
        cont.clicked.connect(dlg.accept)
        row.addWidget(cont)
        lay.addLayout(row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            self.append_log("[INFO] Run Bot cancelled (no provider key).")
            return False

        if dont.isChecked():
            try:
                payload = dict(settings)
                payload["suppress_no_provider_warn"] = "1"
                from core.secrets import save_all
                save_all(payload, PROJECT_ROOT)
                self.append_log("[INFO] Will not show the no-provider warning again.")
            except Exception as e:
                self.append_log(f"[WARN] Could not save preference: {e}")

        self.append_log(
            "[WARN] Starting without provider API key — chat inference disabled."
        )
        return True

    def on_donate_clicked(self):
        from PyQt6.QtGui import QDesktopServices, QGuiApplication
        from PyQt6.QtCore import QUrl
        from PyQt6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QLineEdit,
            QFrame,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{APP_TITLE.split(' v')[0]} — tip jar")
        dlg.setMinimumWidth(480)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(12)

        title = QLabel("pls donate, im poor")
        title.setObjectName("Title")
        lay.addWidget(title)
        sub = QLabel("single dad btw...")
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        cash = QFrame()
        cash.setObjectName("Card")
        cl = QVBoxLayout(cash)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.addWidget(self._donate_section_title("Cash App"))
        tag = QLabel(f"Cashtag: {CASHAPP_TAG}")
        tag.setObjectName("FieldLabel")
        cl.addWidget(tag)
        crow = QHBoxLayout()
        open_cash = QPushButton(f"Open {CASHAPP_TAG}")
        open_cash.setObjectName("primary")
        open_cash.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(CASHAPP_URL))
        )
        crow.addWidget(open_cash)
        copy_cash = QPushButton("Copy cashtag")
        copy_cash.setObjectName("ghost")
        copy_cash.clicked.connect(
            lambda: (
                QGuiApplication.clipboard().setText(CASHAPP_TAG),
                self.append_log(f"[INFO] Copied Cash App {CASHAPP_TAG}"),
            )
        )
        crow.addWidget(copy_cash)
        crow.addStretch()
        cl.addLayout(crow)
        lay.addWidget(cash)

        btc = QFrame()
        btc.setObjectName("Card")
        bl = QVBoxLayout(btc)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.addWidget(self._donate_section_title("Bitcoin (on-chain)"))
        bl.addWidget(
            QLabel(
                "Send BTC to this address from any wallet "
                "(Electrum, BlueWallet, Sparrow, mobile apps, exchange withdraw):"
            )
        )
        addr = QLineEdit(BTC_ADDRESS)
        addr.setReadOnly(True)
        addr.setMinimumHeight(34)
        bl.addWidget(addr)
        brow = QHBoxLayout()
        copy_btc = QPushButton("Copy address")
        copy_btc.setObjectName("primary")
        copy_btc.clicked.connect(
            lambda: (
                QGuiApplication.clipboard().setText(BTC_ADDRESS),
                self.append_log("[INFO] Copied BTC address to clipboard"),
            )
        )
        brow.addWidget(copy_btc)
        brow.addStretch()
        bl.addLayout(brow)
        lay.addWidget(btc)

        close = QPushButton("Close")
        close.setObjectName("ghost")
        close.clicked.connect(dlg.accept)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.exec()

    def _donate_section_title(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("CardTitle")
        return lab

    def _build_local_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        label = QLabel("LOCAL")
        label.setObjectName("PanelTitle")
        title_row.addWidget(label)
        title_row.addStretch()
        self.local_clear_btn = QPushButton("Clear")
        self.local_clear_btn.setObjectName("ghost")
        self.local_clear_btn.clicked.connect(self._on_local_clear)
        title_row.addWidget(self.local_clear_btn)
        layout.addLayout(title_row)

        self.local_output = QTextEdit()
        self.local_output.setReadOnly(True)
        self.local_output.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.local_output, stretch=1)

        self.local_input = QPlainTextEdit()
        self.local_input.setPlaceholderText("Local chat — not Discord.")
        self.local_input.setFixedHeight(72)
        layout.addWidget(self.local_input)

        row = QHBoxLayout()
        row.addStretch()
        self.local_send_btn = QPushButton("Send")
        self.local_send_btn.setObjectName("primary")
        self.local_send_btn.clicked.connect(self._on_local_send)
        row.addWidget(self.local_send_btn)
        layout.addLayout(row)
        return frame

    def _reload_local_transcript(self) -> None:
        if not hasattr(self, "local_output") or self.local_output is None:
            return
        self.local_output.clear()
        try:
            from core.local_chat_memory import load_turns
            turns = load_turns()
        except Exception as e:
            self.local_output.append(f"(could not load local history: {e})")
            return
        for t in turns:
            role = (t.get("role") or "user").lower()
            content = (t.get("content") or "").strip()
            if not content:
                continue
            who = "You" if role == "user" else "Eche"
            self.local_output.append(f"{who}: {content}")
        sb = self.local_output.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _on_local_send(self) -> None:
        text = self.local_input.toPlainText().strip()
        if not text:
            return
        if self._local_worker is not None and self._local_worker.isRunning():
            return

        self.local_input.clear()
        self.local_output.append(f"You: {text}")
        try:
            from core.local_chat_memory import append_turn
            append_turn("user", text)
        except Exception as e:
            self.local_output.append(f"(save user turn failed: {e})")

        self.local_send_btn.setEnabled(False)
        self.local_input.setEnabled(False)

        self._local_worker = LocalChatWorker(text, self)
        self._local_worker.finished_ok.connect(self._on_local_reply)
        self._local_worker.finished_err.connect(self._on_local_err)
        self._local_worker.finished.connect(self._on_local_worker_done)
        self._local_worker.start()

    def _on_local_reply(self, reply: str) -> None:
        reply = (reply or "").strip() or "(empty)"
        self.local_output.append(f"Eche: {reply}")
        try:
            from core.local_chat_memory import append_turn
            append_turn("assistant", reply)
        except Exception as e:
            self.local_output.append(f"(save assistant turn failed: {e})")
        sb = self.local_output.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _on_local_err(self, err: str) -> None:
        self.local_output.append(f"(local error: {err})")

    def _on_local_worker_done(self) -> None:
        self.local_send_btn.setEnabled(True)
        self.local_input.setEnabled(True)
        self._local_worker = None

    def _on_local_clear(self) -> None:
        try:
            from core.local_chat_memory import clear_turns
            clear_turns()
        except Exception as e:
            self.local_output.append(f"(clear failed: {e})")
            return
        self.local_output.clear()

    def _panel(self, title: str, body: QWidget) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        label = QLabel(title.upper())
        label.setObjectName("PanelTitle")
        layout.addWidget(label)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(body)
        return frame

    def set_status(self, state: str, text: str | None = None):
        labels = {
            "offline": "Offline",
            "online": "Bot online",
            "starting": "Starting bot…",
            "error": "Error",
            "busy": "Working…",
        }
        msg = text or labels.get(state, state)
        if msg.startswith("● "):
            msg = msg[2:]
        try:
            if state == "online":
                self.loading.set_state("online", msg)
            elif state in ("starting", "busy"):
                self.loading.set_state("busy", msg)
            elif state == "error":
                self.loading.set_state("error", msg)
            else:
                self.loading.set_state("offline", msg)
        except Exception:
            pass

    def on_run_clicked(self):
        global BOT_STARTED

        if BOT_STARTED:
            self.append_log("[WARN] Bot already started.")
            return

        if self.bot_process and self.bot_process.poll() is None:
            self.append_log("[WARN] Bot process still running.")
            BOT_STARTED = True
            self.set_status("online")
            return

        settings = load_settings()
        token = (settings.get("discord_token") or "").strip()
        if not token:
            token = (os.environ.get("DISCORD_TOKEN") or "").strip()
        if not token:
            self.set_status("error")
            show_error(
                self,
                "Discord token is missing",
                "No bot token was found in secure storage or the environment.",
                hint="Open Settings → Discord, paste your bot token, then Save and Run Bot again.",
            )
            self.append_log("[ERROR] Discord token not set.")
            return

        from core.home_id import parse_home_server_id

        home_raw = (settings.get("home_server_id") or "").strip() or (
            os.environ.get("HOME_SERVER_ID") or ""
        ).strip()
        home_id = parse_home_server_id(home_raw)
        if not home_id:
            self.set_status("error")
            show_error(
                self,
                "Home Server ID is missing" if not home_raw else "Home Server ID is not a server ID",
                "The bot needs a Discord guild (server) ID for memory and context."
                if not home_raw
                else "That value is not a server ID. A server icon link is ok; other text is not.",
                hint=(
                    "Open Settings → Discord and set Home Server ID.\n"
                    "Discord → Settings → Advanced → Developer Mode, then "
                    "right-click the server name → Copy Server ID."
                ),
            )
            self.append_log("[ERROR] HOME_SERVER_ID not set." if not home_raw else f"[ERROR] HOME_SERVER_ID invalid: {home_raw}")
            return
        home = str(home_id)

        backend = (settings.get("provider_backend") or "cloud").strip().lower()
        provider_key = (settings.get("inf_api_key") or "").strip() or (
            os.environ.get("GROQ_API_KEY") or ""
        ).strip()

        # Ollama does not need a cloud API key
        if backend != "ollama" and not provider_key:
            suppress = (settings.get("suppress_no_provider_warn") or "").strip() in (
                "1", "true", "yes", "on",
            )
            if not suppress and not self._warn_no_provider(settings):
                return

        env = os.environ.copy()
        from core.secrets import ENV_MAP
        for key, env_name in ENV_MAP.items():
            val = (settings.get(key) or "").strip()
            if val:
                env[env_name] = val

        # Force backend + model explicitly (after the ENV_MAP loop)
        env["ECHE_PROVIDER"] = "ollama" if backend == "ollama" else "cloud"
        if backend == "ollama":
            env["GROQ_MODEL"] = (
                settings.get("ollama_model")
                or settings.get("groq_model")
                or "llama3"
            ).strip()
            env.setdefault("GROQ_API_KEY", "ollama")
        else:
            env["GROQ_MODEL"] = (
                settings.get("cloud_model")
                or settings.get("groq_model")
                or ""
            ).strip()

        env["DISCORD_TOKEN"] = token
        env["HOME_SERVER_ID"] = home
        from core.admin_tools import flag_on
        env["ECHE_ADMIN_TOOLS"] = "1" if flag_on(settings.get("admin_tools"), "") else "0"
        env["ECHE_OWNER_ID"] = (settings.get("owner_id") or "").strip()
        env["ECHE_RUNNING"] = "BOT"
        env["ECHE_GUI_BRIDGE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["ECHE_USER_ROOT"] = PROJECT_ROOT
        if not is_frozen():
            env["PYTHONPATH"] = os.pathsep.join(
                p for p in [
                    PROJECT_ROOT,
                    os.path.dirname(PROJECT_ROOT),
                    env.get("PYTHONPATH", ""),
                ] if p
            )

        if is_frozen():
            bot_cmd = [sys.executable, "--bot"]
            launch_label = "Eche.exe --bot"
        else:
            bot_cmd = [sys.executable, "-u", "-m", "core.eche"]
            launch_label = "core.eche"

        self.append_log(f"[INFO] Starting bot ({launch_label})...")
        self.set_status("starting")
        self.set_loading(True, "Starting bot…")
        self._tb_buffer.clear()
        self._tb_active = False

        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        try:
            self.bot_process = subprocess.Popen(
                bot_cmd,
                cwd=PROJECT_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                startupinfo=startupinfo,
            )
            self.append_log(f"[INFO] Bot process started with PID: {self.bot_process.pid}")
            self.set_loading(True, "Bot starting…")
        except Exception as e:
            tb = traceback.format_exc()
            self.append_log(f"[ERROR] Failed to start bot: {e}\n{tb}")
            self.set_status("error")
            self.set_loading(False)
            present_failure(self, tb, log_fn=None, default_title="Failed to start bot")
            return

        BOT_STARTED = True
        self.reader_thread = BotReaderThread(self.bot_process)
        self.reader_thread.line_received.connect(self.handle_bot_output)
        self.reader_thread.finished.connect(self._on_reader_finished)
        self.reader_thread.start()

        if self.cog_manager_window:
            self.cog_manager_window.set_bot_process(self.bot_process)

    def on_stop_clicked(self):
        global BOT_STARTED
        self.append_log("[INFO] Stopping bot...")

        if self.reader_thread:
            try:
                self.reader_thread.stop()
                self.reader_thread.wait(2000)
            except Exception as e:
                self.append_log(f"[WARN] Error stopping reader thread: {e}")
            self.reader_thread = None

        if self.bot_process and self.bot_process.poll() is None:
            try:
                self.bot_process.terminate()
            except Exception as e:
                self.append_log(f"[WARN] Error terminating bot process: {e}")
            try:
                self.bot_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.append_log("[WARN] Bot process did not terminate, killing...")
                try:
                    self.bot_process.kill()
                except Exception as e:
                    self.append_log(f"[WARN] Error killing bot process: {e}")

        bot_markers = (
            "-m core.eche", "core\\eche.py", "core/eche.py",
            "run\\run_bot.py", "run/run_bot.py", "--bot", "Eche.exe", "Eche_app.exe"
        )
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.pid == os.getpid():
                    continue
                cmd = proc.info["cmdline"]
                if not cmd:
                    continue
                cmd_str = " ".join(cmd).lower()
                is_bot_cmd = any(marker in cmd_str for marker in bot_markers)
                if is_bot_cmd:
                    self.append_log(
                        f"[INFO] Killing stray bot process PID: {proc.pid} ({' '.join(cmd)})"
                    )
                    proc.kill()
            except Exception as e:
                self.append_log(f"[WARN] Could not kill process PID {proc.pid}: {e}")

        self.bot_process = None
        BOT_STARTED = False
        self.set_status("offline")
        self.append_log("[INFO] Bot stopped.")

    def prepare_for_update(self):
        global BOT_STARTED
        self._bot_was_running_before_update = bool(
            self.bot_process and self.bot_process.poll() is None
        )
        self.append_log("[INFO] Preparing for update: stopping bot if running.")
        self.set_status("starting")
        try:
            self.on_stop_clicked()
        except Exception as e:
            self.append_log(f"[WARN] Error stopping bot before update: {e}")

        try:
            from gui.widgets.terminalwindow import TerminalWindow
            self.term_win = TerminalWindow(title="Eche Updater & Builder")
            self.term_win.show()
            self.term_win.raise_()
            self.term_win.activateWindow()
            self.term_win.append_line("[INFO] Starting update and build process...")
        except Exception as e:
            self.append_log(f"[WARN] Could not open terminal window: {e}")

        try:
            if self.settings_window:
                self.settings_window.close()
                self.settings_window = None
        except Exception:
            pass

        try:
            if self.cog_manager_window:
                self.cog_manager_window.close()
                self.cog_manager_window = None
        except Exception:
            pass

        try:
            if self.bot_memory_window:
                self.bot_memory_window.close()
                self.bot_memory_window = None
        except Exception:
            pass

        try:
            self.hide()
        except Exception:
            pass

    def finish_update_restart(self):
        if getattr(self, "_bot_was_running_before_update", False):
            self.append_log("[INFO] Update complete: restarting bot.")
            try:
                self.on_run_clicked()
            except Exception as e:
                self.append_log(f"[WARN] Error restarting bot after update: {e}")
        else:
            self.append_log("[INFO] Update complete (bot was not running).")

    def handle_bot_output(self, line: str):
        try:
            raw = "" if line is None else str(line).rstrip("\n")
            if not raw.strip():
                if self._tb_active:
                    self._flush_traceback_buffer()
                return

            event = None
            data = {}
            stripped = raw.lstrip()
            if stripped.startswith("{"):
                try:
                    import json
                    obj = json.loads(stripped)
                    if isinstance(obj, dict) and "event" in obj:
                        event = obj.get("event")
                        data = obj.get("data") or {}
                except Exception:
                    event = None

            if event is None:
                self._handle_plain_line(raw)
                return

            if event == "tool":
                self.append_tool_log(
                    str(data.get("name") or "tool"),
                    str(data.get("user") or "someone"),
                    str(data.get("detail") or ""),
                )
            elif event == "log":
                msg = data.get("message", "")
                channel = data.get("channel", "")
                text = f"[{channel}] {msg}" if channel else str(msg)
                self.append_log(text)
                if looks_like_traceback(str(msg)):
                    present_failure(self, str(msg), log_fn=None)
            elif event == "fatal":
                msg = str(data.get("message") or "Bot failed to start")
                self.append_log(f"[FATAL] {msg}")
                self.set_status("error")
                self.set_loading(False)
                present_failure(self, msg, log_fn=None)
            elif event == "ready":
                user = data.get("user", "")
                label = f"Bot online · {user}" if user else "Bot online"
                self.set_status("online", label)
                self.append_log(
                    f"[INFO] Bot ready as {user}." if user else "[INFO] Bot ready."
                )
            elif event == "chat":
                self._append_panel(self.chat_output, data.get("text", ""))
            elif event in ("cog_list", "status"):
                loaded = data.get("loaded")
                if (
                    loaded is not None
                    and self.cog_manager_window
                    and hasattr(self.cog_manager_window, "apply_cog_list")
                ):
                    self.cog_manager_window.apply_cog_list(loaded)
            elif event == "unifier_update":
                self._append_panel(
                    self.chat_output, f"[unifier] {data.get('text', '')}"
                )
            else:
                self.append_log(raw)
        except Exception as e:
            try:
                self.append_log(f"[WARN] Error handling bot output: {e}")
            except Exception:
                pass

    def _handle_plain_line(self, raw: str) -> None:
        if raw.startswith("Traceback (most recent call last)"):
            self._tb_active = True
            self._tb_buffer = [raw]
            self.append_log(raw)
            return

        if self._tb_active:
            self._tb_buffer.append(raw)
            self.append_log(raw)
            stripped = raw.strip()
            if (
                stripped
                and not stripped.startswith("File ")
                and not stripped.startswith("~")
                and not raw.startswith(" ")
                and not raw.startswith("\t")
                and (
                    "Error" in stripped
                    or "Exception" in stripped
                    or "Error:" in stripped
                    or stripped.endswith("Error")
                )
            ):
                self._flush_traceback_buffer()
            return

        self.append_log(raw)
        if "HOME_SERVER_ID is not set" in raw or "DISCORD_TOKEN missing" in raw:
            present_failure(self, raw, log_fn=None)

    def _flush_traceback_buffer(self) -> None:
        if not self._tb_buffer:
            self._tb_active = False
            return
        text = "\n".join(self._tb_buffer)
        self._tb_buffer.clear()
        self._tb_active = False
        self.set_status("error")
        present_failure(self, text, log_fn=None, default_title="Code error")

    def _append_panel(self, widget, text: str):
        if widget is None or text is None:
            return
        try:
            from core.secrets import scrub_text
            text = scrub_text(str(text), PROJECT_ROOT)
        except Exception:
            text = str(text)
        text = text.rstrip("\n")
        if not text:
            return
        ts = time.strftime("%H:%M:%S")
        widget.append(f"[{ts}] {text}")
        sb = widget.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _on_reader_finished(self):
        global BOT_STARTED
        if self._tb_active:
            self._flush_traceback_buffer()
        if self.bot_process and self.bot_process.poll() is not None:
            code = self.bot_process.returncode
            self.append_log(f"[INFO] Bot process exited with code: {code}.")
            self.bot_process = None
            BOT_STARTED = False
            self.set_status("offline" if code == 0 else "error")
            self.set_loading(False)
        else:
            self.append_log("[INFO] Bot output reader finished.")
            self.set_loading(False)

    def on_settings_clicked(self):
        self.settings_window = SettingsWindow(main_window=self)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def on_unifier_clicked(self):
        try:
            from core.paths import readable_core_file
            path = readable_core_file("builder.py")
        except Exception:
            path = BUILDER_FILE_PATH
        self.append_log(f"[INFO] Opening Unifier: {path}")
        try:
            self.unifier_window.load_file_content(path)
            self.unifier_window.show()
            self.unifier_window.raise_()
            self.unifier_window.activateWindow()
        except Exception as e:
            error_msg = f"[ERROR] Failed to open Unifier: {e}\n{traceback.format_exc()}"
            self.append_log(error_msg)
            present_failure(self, error_msg, log_fn=None)

    def on_cog_manager_clicked(self):
        if not self.cog_manager_window:
            self.cog_manager_window = CogManagerWindow(
                bot_process=self.bot_process,
                main_window=self,
            )
        else:
            self.cog_manager_window.set_bot_process(self.bot_process)

        if not self.bot_process or self.bot_process.poll() is not None:
            self.append_log(
                "[INFO] Cog browser opened (bot offline — toggles disabled until Run Bot)."
            )

        self.cog_manager_window.show()
        self.cog_manager_window.raise_()
        self.cog_manager_window.activateWindow()

    def open_bot_memory_window(self):
        if not self.bot_memory_window:
            self.bot_memory_window = BotMemoryWindow(main_window=self)
        self.bot_memory_window.show()
        self.bot_memory_window.raise_()
        self.bot_memory_window.activateWindow()

    def open_user_context_window(self):
        if not getattr(self, "user_context_window", None):
            from gui.widgets.usercontextwindow import UserContextWindow
            self.user_context_window = UserContextWindow(main_window=self)
        self.user_context_window.show()
        self.user_context_window.raise_()
        self.user_context_window.activateWindow()

    def on_unifier_content_saved(self, path: str, content: str):
        self.append_log(f"[INFO] Content saved to: {path}")
        if path == BUILDER_FILE_PATH:
            self.append_log(
                "[INFO] Builder file updated. Bot may need to be restarted for changes to take effect."
            )

    def append_log(self, text: str):
        try:
            from core.secrets import scrub_text
            text = scrub_text(str(text), PROJECT_ROOT)
        except Exception:
            pass

        self.log_output.append_line(text)
        self._write_log_file(text)

    def append_tool_log(self, name: str, user: str, detail: str):
        name = " ".join(str(name or "tool").split()) or "tool"
        user = " ".join(str(user or "someone").split()) or "someone"
        summary = f"Eche used tool {name} for {user}"
        detail = str(detail or "").strip() or "(no output)"
        try:
            from core.secrets import scrub_text
            summary = scrub_text(summary, PROJECT_ROOT)
            detail = scrub_text(detail, PROJECT_ROOT)
        except Exception:
            pass
        self.log_output.append_tool(summary, detail)
        self._write_log_file(summary + "\n" + detail)

    def _write_log_file(self, text: str) -> None:
        try:
            with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception as e:
            print(f"FATAL ERROR: Could not write to log file {LOG_FILE_PATH}: {e}")
            print(f"Log write failure: {text}", file=sys.stderr)

    def save_logs_to_file(self):
        log_content = self.log_output.plain_text()
        try:
            with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(log_content)
            print(f"GUI logs saved to {LOG_FILE_PATH}")
        except Exception as e:
            print(f"FATAL ERROR: Could not save GUI logs to {LOG_FILE_PATH}: {e}")

    def closeEvent(self, event):
        self.append_log("[INFO] GUI closing...")
        try:
            self.save_logs_to_file()
            self.on_stop_clicked()
        except Exception as e:
            self.append_log(
                f"[ERROR] Error during closeEvent: {e}\n{traceback.format_exc()}"
            )
        event.accept()


def launch_gui():
    """Initializes and runs the PyQt application."""
    app = QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    launch_gui()