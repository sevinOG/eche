# gui/main.py — Echelon v1 control panel

from __future__ import annotations

import os
import sys
import time
import subprocess
import psutil
import atexit
import traceback # Import traceback for detailed error logging

from gui.watchdog import ensure_single_gui_instance, cleanup_lockfile

if not ensure_single_gui_instance():
    sys.exit(0)

atexit.register(cleanup_lockfile)

os.environ["ECHELON_RUNNING"] = "GUI"

BOT_STARTED = False

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
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
from gui.widgets.dialogs import (
    present_failure,
    show_error,
    looks_like_traceback,
)

# Tip jar (Cash App + on-chain Bitcoin)
CASHAPP_TAG = "$reshi7"
CASHAPP_URL = "https://cash.app/$reshi7"
# Native SegWit-style / multi-path bitcoin address provided by the maintainer
BTC_ADDRESS = "bc1qp989v95u54zpnmw9j75azwp9hrqnd0k6d7jp3lvv6z3yywpfdutszkkhg6"

# --- Path Resolution Logic ---
try:
    # Attempt to import from core.paths first (standard in packaged apps)
    from core.paths import is_frozen, user_dir, ensure_user_layout, bundle_file
except ImportError:
    # Fallback definitions if core.paths fails to import
    print("WARNING: core.paths module not found. Using fallback path resolution.")

    def is_frozen():
        # Check if running as a frozen executable
        return bool(getattr(sys, "frozen", False))

    def user_dir_fallback():
        # Get the directory of the current script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # gui/main.py -> package root (echelon/)
        return os.path.abspath(os.path.join(script_dir, ".."))

    def ensure_user_layout_fallback():
        root = user_dir_fallback()
        # Ensure config directory exists
        os.makedirs(os.path.join(root, "config"), exist_ok=True)
        return root

    # Assign fallback functions
    is_frozen = is_frozen
    ensure_user_layout = ensure_user_layout_fallback
# --- End Path Resolution Logic ---

# Determine PROJECT_ROOT based on resolved logic
PROJECT_ROOT = ensure_user_layout()
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "settings.json")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE_PATH = os.path.join(LOG_DIR, "gui_log.txt")

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Define global builder.py path.
# In source this is PROJECT_ROOT/core/builder.py; in frozen builds the bot code
# is bundled under <bundle>/_internal/core/builder.py, so we
# resolve it via core.paths.bundle_file() and fall back to the source layout.
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
                # Read line from stdout
                line = self.process.stdout.readline()
                if line:
                    self.line_received.emit(line.rstrip("\n"))
            except Exception as e:
                print(f"Error reading from bot process stdout: {e}")
                break # Exit loop on error

    def stop(self):
        self._running = False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 780)
        self.setMinimumSize(900, 560)
        self.move(80, 60)

        # Same brand icon as the app (assets/icon.png)
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
        # Accumulate multi-line Python tracebacks from the bot child process
        self._tb_buffer: list[str] = []
        self._tb_active = False

        # --- GUI Layout Setup ---
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Toolbar: brand | status | balanced action cluster
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

        # Single status chip (spinner while busy → "Bot online" when ready)
        self.loading = LoadingIndicator()
        tb.addWidget(self.loading)

        # Action buttons — Unifier lives in Settings → Memory only
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

        # Main panels: Chat, Subconscious, Logs — balanced split
        splitter_vertical = QSplitter(Qt.Orientation.Vertical)
        splitter_top = QSplitter(Qt.Orientation.Horizontal)

        self.chat_output = QTextEdit()
        self.chat_output.setReadOnly(True)
        self.sub_output = QTextEdit()
        self.sub_output.setReadOnly(True)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        splitter_top.addWidget(self._panel("Chat", self.chat_output))
        splitter_top.addWidget(self._panel("Subconscious", self.sub_output))
        splitter_top.setStretchFactor(0, 1)
        splitter_top.setStretchFactor(1, 1)
        splitter_top.setSizes([560, 560])

        splitter_vertical.addWidget(splitter_top)
        splitter_vertical.addWidget(self._panel("Logs", self.log_output))
        splitter_vertical.setStretchFactor(0, 3)
        splitter_vertical.setStretchFactor(1, 1)
        splitter_vertical.setSizes([520, 200])

        main_layout.addWidget(splitter_vertical, stretch=1)

        # Out-of-the-way donate (bottom strip)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addStretch()
        self.donate_button = QPushButton("pls donate, im poor")
        self.donate_button.setObjectName("donate")
        self.donate_button.setToolTip("Open the tip jar (optional, no pressure)")
        self.donate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.donate_button.clicked.connect(self.on_donate_clicked)
        footer.addWidget(self.donate_button)
        main_layout.addLayout(footer)

        self.set_status("offline")
        self.append_log(f"[INFO] {APP_TITLE} GUI started.")
        self.append_log("[INFO] Open Settings to manage tokens, then Run Bot.")
        # --- End GUI Layout Setup ---

    def set_loading(self, busy: bool, message: str = "Working…"):
        """Shared busy indicator — only spins when not already online."""
        try:
            if busy:
                self.loading.set_state("busy", message)
            # When clearing busy, leave online/offline alone (set_status owns that)
        except Exception:
            pass

    def _warn_no_provider(self, settings: dict) -> bool:
        """
        Warn that chat AI will not work without a provider key.
        Returns False if the user cancelled Run Bot.
        """
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

        # Cash App
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

        # Bitcoin
        btc = QFrame()
        btc.setObjectName("Card")
        bl = QVBoxLayout(btc)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.addWidget(self._donate_section_title("Bitcoin (on-chain)"))
        bl.addWidget(QLabel("Send BTC to this address from any wallet (Electrum, BlueWallet, Sparrow, mobile apps, exchange withdraw):"))
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

    def _panel(self, title: str, body: QTextEdit) -> QFrame:
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
        """Drive the single toolbar status chip (offline / busy / online / error)."""
        labels = {
            "offline": "Offline",
            "online": "Bot online",
            "starting": "Starting bot…",
            "error": "Error",
            "busy": "Working…",
        }
        msg = text or labels.get(state, state)
        # Strip leading bullet if callers still pass old style
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

    # --- Bot Control Methods ---
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

        home = (settings.get("home_server_id") or "").strip() or (
            os.environ.get("HOME_SERVER_ID") or ""
        ).strip()
        if not home:
            self.set_status("error")
            show_error(
                self,
                "Home Server ID is missing",
                "The bot needs a Discord guild (server) ID for memory and context.",
                hint=(
                    "Open Settings → Discord and set Home Server ID.\n"
                    "Discord → Settings → Advanced → Developer Mode, then "
                    "right-click your server → Copy Server ID."
                ),
            )
            self.append_log("[ERROR] HOME_SERVER_ID not set.")
            return

        # Provider key optional — warn once (unless suppressed)
        provider_key = (settings.get("inf_api_key") or "").strip() or (
            os.environ.get("GROQ_API_KEY") or ""
        ).strip()
        if not provider_key:
            suppress = (settings.get("suppress_no_provider_warn") or "").strip() in (
                "1", "true", "yes", "on",
            )
            if not suppress and not self._warn_no_provider(settings):
                return  # user cancelled

        env = os.environ.copy()
        from core.secrets import ENV_MAP
        for key, env_name in ENV_MAP.items():
            val = (settings.get(key) or "").strip()
            if val:
                env[env_name] = val

        env["DISCORD_TOKEN"] = token
        env["HOME_SERVER_ID"] = home
        env["ECHELON_RUNNING"] = "BOT"
        env["ECHELON_GUI_BRIDGE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["ECHELON_USER_ROOT"] = PROJECT_ROOT
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
            launch_label = "Echelon.exe --bot"
        else:
            bot_cmd = [sys.executable, "-u", "-m", "core.echelon"]
            launch_label = "core.echelon"

        self.append_log(f"[INFO] Starting bot ({launch_label})...")
        self.set_status("starting")
        self.set_loading(True, "Starting bot…")
        self._tb_buffer.clear()
        self._tb_active = False

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

        # Safely stop the reader thread
        if self.reader_thread:
            try:
                self.reader_thread.stop()
                self.reader_thread.wait(2000) # Wait a bit for it to finish
            except Exception as e:
                self.append_log(f"[WARN] Error stopping reader thread: {e}")
            self.reader_thread = None

        # Stop the bot process
        if self.bot_process and self.bot_process.poll() is None:
            try:
                self.bot_process.terminate() # Send SIGTERM
            except Exception as e:
                self.append_log(f"[WARN] Error terminating bot process: {e}")
            try:
                self.bot_process.wait(timeout=5) # Wait for process to exit
            except subprocess.TimeoutExpired:
                self.append_log("[WARN] Bot process did not terminate, killing...")
                try:
                    self.bot_process.kill() # Force kill if terminate failed
                except Exception as e:
                    self.append_log(f"[WARN] Error killing bot process: {e}")

        # Clean up any remaining bot processes potentially spawned by the bot itself
        bot_markers = (
            "-m core.echelon", "core\\echelon.py", "core/echelon.py",
            "run\\run_bot.py", "run/run_bot.py", "--bot", "Echelon.exe", "Echelon_app.exe"
        )
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.pid == os.getpid(): continue # Skip self
                cmd = proc.info["cmdline"]
                if not cmd: continue
                cmd_str = " ".join(cmd).lower()
                name = (proc.info.get("name") or "").lower()
                is_bot_cmd = any(marker in cmd_str for marker in bot_markers)
                if is_bot_cmd:
                    self.append_log(f"[INFO] Killing stray bot process PID: {proc.pid} ({' '.join(cmd)})")
                    proc.kill()
            except Exception as e:
                self.append_log(f"[WARN] Could not kill process PID {proc.pid}: {e}")

        self.bot_process = None
        BOT_STARTED = False
        self.set_status("offline")
        self.append_log("[INFO] Bot stopped.")

    def prepare_for_update(self):
        """Called by Settings before running the updater.

        Stops the running bot (and its reader thread) so the update can
        overwrite Echelon.exe cleanly. Opens the TerminalWindow output window
        and keeps it alive while closing the Settings window.
        """
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

        # Open TerminalWindow and keep a reference on main window so it doesn't get garbage collected
        try:
            from gui.widgets.terminalwindow import TerminalWindow
            self.term_win = TerminalWindow(title="Echelon Updater & Builder")
            self.term_win.show()
            self.term_win.raise_()
            self.term_win.activateWindow()
            self.term_win.append_line("[INFO] Starting update and build process...")
        except Exception as e:
            self.append_log(f"[WARN] Could not open terminal window: {e}")

        # Close settings window so it releases file locks
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

        # Also hide / close the main window so that Echelon.exe itself isn't locked by the GUI process
        try:
            self.hide()
        except Exception:
            pass

    def finish_update_restart(self):
        """Called by Settings after a successful update.

        Restarts the bot only if it was running before the update.
        """
        if getattr(self, "_bot_was_running_before_update", False):
            self.append_log("[INFO] Update complete: restarting bot.")
            try:
                self.on_run_clicked()
            except Exception as e:
                self.append_log(f"[WARN] Error restarting bot after update: {e}")
        else:
            self.append_log("[INFO] Update complete (bot was not running).")

    def handle_bot_output(self, line: str):
        """Handle one line of bot stdout. Lines are either gui_bridge JSON
        events ({"event": ..., "data": {...}}) or plain-text prints.
        Must never raise: it runs from a Qt signal and would crash the GUI."""
        try:
            raw = "" if line is None else str(line).rstrip("\n")
            if not raw.strip():
                # blank line may end a traceback block
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

            if event == "log":
                msg = data.get("message", "")
                channel = data.get("channel", "")
                text = f"[{channel}] {msg}" if channel else str(msg)
                self.append_log(text)
                if looks_like_traceback(str(msg)):
                    present_failure(self, str(msg), log_fn=None)
            elif event == "fatal":
                msg = str(data.get("message") or "Bot failed to start")
                code = str(data.get("code") or "config")
                self.append_log(f"[FATAL] {msg}")
                self.set_status("error")
                self.set_loading(False)
                present_failure(self, msg, log_fn=None)
            elif event == "ready":
                user = data.get("user", "")
                label = f"Bot online · {user}" if user else "Bot online"
                self.set_status("online", label)
                self.append_log(f"[INFO] Bot ready as {user}." if user else "[INFO] Bot ready.")
            elif event == "chat":
                self._append_panel(self.chat_output, data.get("text", ""))
            elif event in ("cog_list", "status"):
                loaded = data.get("loaded")
                if loaded is not None and self.cog_manager_window and hasattr(self.cog_manager_window, "apply_cog_list"):
                    self.cog_manager_window.apply_cog_list(loaded)
            elif event == "subconscious_update":
                self._append_panel(self.sub_output, data.get("text", ""))
            elif event == "unifier_update":
                self._append_panel(self.chat_output, f"[unifier] {data.get('text', '')}")
            else:
                self.append_log(raw)
        except Exception as e:
            try:
                self.append_log(f"[WARN] Error handling bot output: {e}")
            except Exception:
                pass

    def _handle_plain_line(self, raw: str) -> None:
        """Log plain stdout; assemble multi-line tracebacks into one dialog."""
        if raw.startswith("Traceback (most recent call last)"):
            self._tb_active = True
            self._tb_buffer = [raw]
            self.append_log(raw)
            return

        if self._tb_active:
            self._tb_buffer.append(raw)
            self.append_log(raw)
            # End of traceback: line that looks like ExceptionName: message
            # and does not start with whitespace/File
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
        # Single-line config fatals without JSON (belt and suspenders)
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
        # Already logged line-by-line; show themed traceback window
        present_failure(self, text, log_fn=None, default_title="Code error")

    def _append_panel(self, widget, text: str):
        """Append a line to a side panel (Chat / Subconscious) with a
        timestamp, scrub secrets, and auto-scroll to the newest line."""
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
        """Callback when the bot output reader thread finishes."""
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

    # --- Secondary Windows ---
    def on_settings_clicked(self):
        self.settings_window = SettingsWindow(main_window=self)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def on_unifier_clicked(self):
        """Opens Unifier (builder.py) from Settings — package core file."""
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
        # Cog browser works offline for disk scan; toggles need a running bot.
        if not self.cog_manager_window:
            self.cog_manager_window = CogManagerWindow(
                bot_process=self.bot_process,
                main_window=self,
            )
        else:
            self.cog_manager_window.set_bot_process(self.bot_process)

        if not self.bot_process or self.bot_process.poll() is not None:
            self.append_log("[INFO] Cog browser opened (bot offline — toggles disabled until Run Bot).")

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

    # Method to handle the content saved signal from UnifierPanel
    def on_unifier_content_saved(self, path: str, content: str):
        self.append_log(f"[INFO] Content saved to: {path}")
        if path == BUILDER_FILE_PATH:
            self.append_log("[INFO] Builder file updated. Bot may need to be restarted for changes to take effect.")

    # --- Logging and File Handling ---
    def append_log(self, text: str):
        """Appends text to the GUI log output and to the persistent log file."""
        try:
            from core.secrets import scrub_text
            text = scrub_text(str(text), PROJECT_ROOT)
        except Exception:
            pass # Ignore scrubbing errors, just log original text

        self.log_output.append(text) # Append to GUI log

        # Append to persistent log file
        try:
            with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception as e:
            print(f"FATAL ERROR: Could not write to log file {LOG_FILE_PATH}: {e}")
            # If we can't even write to log file, print to stderr as a last resort
            print(f"Log write failure: {text}", file=sys.stderr)

    def save_logs_to_file(self):
        """Saves the current content of the log_output QTextEdit to the persistent log file."""
        log_content = self.log_output.toPlainText()
        try:
            with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(log_content)
            print(f"GUI logs saved to {LOG_FILE_PATH}")
        except Exception as e:
            print(f"FATAL ERROR: Could not save GUI logs to {LOG_FILE_PATH}: {e}")

    # --- Event Handling ---
    def closeEvent(self, event):
        """Handles the window closing event."""
        self.append_log("[INFO] GUI closing...")
        try:
            self.save_logs_to_file() # Save logs before closing
            self.on_stop_clicked()   # Attempt to stop the bot process
        except Exception as e:
            self.append_log(f"[ERROR] Error during closeEvent: {e}\n{traceback.format_exc()}")
        event.accept() # Accept the close event


def launch_gui():
    """Initializes and runs the PyQt application."""
    app = QApplication(sys.argv)
    apply_theme(app) # Apply application theme
    window = MainWindow()
    window.show()
    sys.exit(app.exec()) # Start the application event loop


if __name__ == "__main__":
    launch_gui()
