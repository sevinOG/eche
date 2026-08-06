# gui/widgets/settingswindow.py
# Multi-page settings: Discord | AI | Media | Memory | Updates
# Paths are self-identifying via core.paths (portable clone).

from __future__ import annotations

import os
import sys
import subprocess

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QTimer, QProcess
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QCheckBox,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QComboBox,
)

from gui.theme import APP_NAME, APP_VERSION
from gui.widgets.providerwindow import ProviderWindow
from gui.widgets.personalitywindow import PersonalityWindow
from gui.widgets.loading import LoadingIndicator
from gui.widgets.dialogs import show_error, show_info, present_failure

UNSPLASH_DEV_URL = "https://unsplash.com/developers"
UNSPLASH_APPS_URL = "https://unsplash.com/oauth/applications"

try:
    from core.paths import ensure_user_layout, resolve_source_root, is_source_tree, package_root
    PROJECT_ROOT = ensure_user_layout()
except Exception:
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def resolve_source_root(stored=None):
        return PROJECT_ROOT

    def is_source_tree(path):
        return bool(path and os.path.isdir(path))

    def package_root():
        return PROJECT_ROOT


# Educational copy — written for users with little or no technical background
FIELD_HELP: dict[str, tuple[str, str]] = {
    "discord_token": (
        "Discord Bot Token (the bot’s password)",
        "### What is this?\n"
        "Discord does not let random programs join as a bot. You create a **bot account** "
        "on Discord’s website, and Discord gives you a long secret string called a **token**. "
        "That token is the bot’s password.\n\n"
        "### How to get one (step by step)\n"
        "1. Open the [Discord Developer Portal](https://discord.com/developers/applications)\n"
        "2. Click **New Application**, give it a name\n"
        "3. Open **Bot** → **Reset Token** / **Copy**\n"
        "4. Paste it here and click **Save Settings**\n"
        "5. Under **OAuth2 → URL Generator**, pick `bot` + the permissions you need, "
        "open the invite link, add the bot to your server\n\n"
        "### Safety\n"
        "Anyone with this token controls your bot. Echelon stores it encrypted for **your "
        "Windows user only**. Never post it in chat or commit it to GitHub.\n\n"
        "### How it fits the bigger picture\n"
        "Discord is just the **chat room**. The AI “brain” is separate (see Provider API Key).",
    ),
    "inf_api_key": (
        "Provider API Key (the AI brain’s password)",
        "### What is a provider?\n"
        "A **provider** is a company that runs large AI models on their computers so you "
        "don’t need a fancy GPU at home. Echelon sends a question to the provider; the "
        "provider’s model writes the answer; Echelon posts it in Discord.\n\n"
        "### Default: Groq (free tier)\n"
        "Echelon is set up for **Groq** by default because it is fast and has a free tier:\n"
        "1. Go to [console.groq.com](https://console.groq.com/)\n"
        "2. Make an account → **API Keys** → create a key\n"
        "3. Paste it here → **Save Settings**\n\n"
        "### Using Local Ollama\n"
        "You can also run models completely locally and privately using **Ollama**:\n"
        "1. Install and run [Ollama](https://ollama.com/) on your machine.\n"
        "2. Pull your model of choice (e.g., `ollama pull llama3` or `ollama pull mistral`).\n"
        "3. Click **Edit Provider (client.py)** in the AI & Model section and update `API_URL` to point to your local Ollama endpoint (typically `http://localhost:11434/v1`).\n"
        "4. Set your **Model ID** in settings to match your Ollama model name (e.g., `llama3`).\n"
        "5. Leave the Provider API Key blank or set it to `ollama` since local Ollama does not require a paid key.\n\n"
        "### Can I run without a key?\n"
        "Yes, for local providers like Ollama or for non-AI bot features (games, economy, music). "
        "It just cannot invent chat replies until a valid provider and key/endpoint are configured.\n\n"
        "### The three pieces of “AI setup”\n"
        "1. **Provider API key** (this field) — password\n"
        "2. **Model ID** — which brain size/style\n"
        "3. **Provider code** (`client.py`) — the phone number (URL) of the service",
    ),
    "us_access_token": (
        "Unsplash Access Token (optional photo search)",
        "### What is this?\n"
        "Some bot commands search the web for photos (Unsplash). Unsplash gives you a free "
        "app key so they know who is searching.\n\n"
        "### Do I need it?\n"
        "Only if you use image-search features. Chat, bank, and games work without it.\n\n"
        "### How to get one\n"
        "Create a free developer account at Unsplash, register an application, copy the "
        "**Access Key**, paste it here, Save.",
    ),
    "us_secret_token": (
        "Unsplash Secret Token (optional)",
        "A second secret Unsplash sometimes uses. Treat it like a password. "
        "Most simple searches only need the Access Token.",
    ),
    "home_server_id": (
        "Home Server ID (which Discord server is “home”)",
        "### What is this?\n"
        "Your bot can join many Discord servers. **Home Server** is the one place Echelon "
        "stores memory and bank data (folders of channels named `memory-…`).\n\n"
        "### How to copy the ID (no typing long numbers by hand)\n"
        "1. Discord → **User Settings → Advanced → Developer Mode = ON**\n"
        "2. Right-click your server icon → **Copy Server ID**\n"
        "3. Paste here → Save\n\n"
        "### Why it matters\n"
        "Without a home server, the bot cannot create user memory or economy channels. "
        "This is required even if you skip the AI provider key.",
    ),
    "thoughts_thread_id": (
        "Thoughts Thread ID (optional debug notepad)",
        "### What is this?\n"
        "Optional. You can make a private thread where the bot posts “thinking” notes "
        "while you learn how it works.\n\n"
        "### How to get a thread ID\n"
        "Enable Developer Mode, right-click the thread → **Copy Channel ID**.\n\n"
        "### Can I leave it blank?\n"
        "Yes. Completely optional for normal use.",
    ),
    "groq_model": (
        "Model ID (which AI brain)",
        "### What is a model?\n"
        "A **model** is a specific AI trained by researchers. Bigger models are often "
        "smarter but slower or cost more. Smaller ones are cheaper and fine for simple chat.\n\n"
        "### Default (Cloud / Groq)\n"
        "`llama-3.3-70b-versatile` works well with the default provider setup.\n\n"
        "### Local Ollama\n"
        "Use a name you pulled, e.g. `llama3` or `mistral` (`ollama list`).\n\n"
        "### If chat breaks with “model not found”\n"
        "The provider renamed or removed that model. Open their model list in the docs "
        "or console, copy a current name, paste it here, Save, restart the bot.\n\n"
        "### This is not the same as the API key\n"
        "Key = password. Model ID = which product to run after you log in.",
    ),
    "provider_backend": (
        "Provider backend (Cloud vs Ollama)",
        "### Cloud (default — Groq)\n"
        "Sends chat to Groq’s free-tier cloud API. Paste a key from "
        "[console.groq.com](https://console.groq.com/).\n\n"
        "### Local Ollama\n"
        "Runs models on **your PC** via [Ollama](https://ollama.com/). "
        "No paid key required. Install Ollama, `ollama pull llama3`, set Model ID to `llama3`.\n\n"
        "### Edit client.py\n"
        "Advanced users can still open the provider file to change URLs by hand. "
        "The dropdown sets `ECHELON_PROVIDER` so normal switches don’t require code edits.",
    ),
    "summarizer_model": (
        "Summarizer model",
        "### What is this?\n"
        "When memory fills up, Echelon asks an AI to **compress** old messages into a "
        "short Summary. That call can use a **different** model than chat.\n\n"
        "### Leave blank?\n"
        "Uses the same Model ID as chat.\n\n"
        "### Why separate?\n"
        "You might want a cheap/fast model for summaries and a smarter one for replies.",
    ),
    "summarizer_prompt": (
        "Summarizer prompt file",
        "### What is this?\n"
        "The instruction sheet the bot gives the AI when compressing memory — "
        "same idea as Personality, but for summaries only.\n\n"
        "### Default path\n"
        "`config/summarizer_prompt.txt` in this package.\n\n"
        "### Custom path\n"
        "Optional: point at another `.txt` file. Relative paths are from the package root.\n\n"
        "### Placeholder\n"
        "Keep `{combined_for_summary}` in the file so conversation text is inserted.",
    ),
    "personality": (
        "Personality (the bot’s character sheet)",
        "### What is this?\n"
        "A plain-English instruction sheet that is added to **every** AI chat call. "
        "It sets tone, humor, boundaries, and identity — without training a new model.\n\n"
        "### Examples of what you might write\n"
        "- “You are a chill server butler who keeps answers short.”\n"
        "- “Never reveal API keys. Stay in character.”\n\n"
        "### Where it saves\n"
        "`config/personality.txt` in this package (not encrypted).\n\n"
        "### Tip\n"
        "Change one sentence at a time and test. Small wording changes can matter a lot.",
    ),
    "provider": (
        "Provider code (client.py) — the phone line to the AI",
        "### What is this editor?\n"
        "It opens `core/client.py`, the program code that **calls the AI provider**.\n\n"
        "Near the **top of the file** you will see:\n"
        "- `API_URL = ...` — the website address of the provider\n"
        "- `_api_key()` — reads your Provider API Key from Settings\n"
        "- `DEFAULT_MODEL` / `_model()` — which model name to send\n"
        "- `call_groq(...)` — the function that sends chat messages\n\n"
        "### Using Local Ollama\n"
        "To connect to a local **Ollama** instance:\n"
        "1. Set `API_URL` to your local OpenAI-compatible endpoint (e.g. `http://localhost:11434/v1`).\n"
        "2. Ensure Ollama is running (`ollama serve`).\n\n"
        "### Default vs custom\n"
        "Default points at **Groq** with a free tier: [console.groq.com](https://console.groq.com/). "
        "You can keep that forever, or later point `API_URL` at another OpenAI-compatible host.\n\n"
        "### Do I need to edit code on day one?\n"
        "No. Paste a free provider key in Settings and leave this file alone until you "
        "want a different company or a local model.\n\n"
        "### After Save\n"
        "Kill Bot → Run Bot so the new code loads.",
    ),
    "unifier": (
        "Unifier (how the prompt is assembled)",
        "### What is this?\n"
        "Before the AI answers, Echelon builds one big instruction package: personality, "
        "memory snippets, user message, rules. The **unifier / builder** file is where that "
        "assembly is defined.\n\n"
        "### Why care?\n"
        "This is how real products work — not one magic model, but a **pipeline**. "
        "Editing it lets you change what the bot “knows” about the conversation.\n\n"
        "### Where it saves\n"
        "Package file `core/builder.py` (plain text). Restart the bot after saving.",
    ),
    "bot_memory": (
        "Self Memory (what the bot remembers about itself)",
        "### What is this?\n"
        "On the home Discord server, under a category like `bot-memory`, there is a "
        "pinned note the bot uses as its own diary (summary + recent lines).\n\n"
        "### How it differs from user context\n"
        "- **Self memory** = about the bot\n"
        "- **User context** = about each human (per user folder)\n\n"
        "### Editing\n"
        "You can view and edit that pin from this button. Be careful — it is live on Discord.",
    ),
    "user_context": (
        "User Context (memory of each person)",
        "### What is this?\n"
        "For each opted-in user, Discord has a category `memory-{user id}` with a "
        "`#context` channel and a pinned note (Summary + New lines).\n\n"
        "### What you can do here\n"
        "Browse by server, open a user’s pin, edit it, save back to Discord. "
        "Optional **Save Local** only if you want a file snapshot on disk.\n\n"
        "### Why Discord pins?\n"
        "So memory lives with the community, not only on one PC — and so you can "
        "see it in the Discord app without opening code.",
    ),
    "project_path": (
        "echelon_source folder (dev tree)",
        "### What is this?\n"
        "The **source** package: `echelon_source/` with `BUILD.bat`, `core/`, `gui/`, `.venv`.\n\n"
        "### Not the portable app\n"
        "The flash-drive folder `echelon/` only has `Echelon.exe` — rebuilds always run from **source**.\n\n"
        "### Rebuild\n"
        "One-tap update runs `BUILD.bat` there and publishes a fresh portable app into sibling `echelon/`.",
    ),
    "updates": (
        "One-tap rebuild from echelon_source",
        "### What happens\n"
        "1. Finds `echelon_source` (or the path you set)\n"
        "2. Runs **`BUILD.bat`** with the source `.venv`\n"
        "3. Publishes `Echelon.exe` + `_internal` + **icons** into `../echelon/`\n"
        "4. Keeps your `config/` secrets on the portable side\n\n"
        "### First time\n"
        "In `echelon_source`: `python -m venv .venv` then "
        "`.venv\\Scripts\\pip install -r requirements.txt` once.",
    ),
    "economy": (
        "Economy / bank balances",
        "### Where money lives\n"
        "On the **Home Server**, each user can have a category `memory-{their id}` "
        "with a channel named `#economy`. Inside is a **pinned message** that starts with:\n\n"
        "```\nBANK DATA\n500.00\nSTARTER:1\n```\n\n"
        "That is the same file the bot uses for `?bet`, bank commands, and games.\n\n"
        "### What the bank browser does\n"
        "Lists users, shows the pin, lets you change the balance, saves back to Discord. "
        "You need the bot token and Home Server ID set first.",
    ),
    "security": (
        "Security folders",
        "### Cookies\n"
        "Some features (e.g. music / YouTube) may need a cookies file. "
        "Open the cookies folder, add your file, then **restart the bot** "
        "(and rebuild the exe if you ship a frozen build) so it picks the file up.\n\n"
        "### Secrets (DPAPI)\n"
        "API tokens and the Discord bot token are stored encrypted for your Windows "
        "user in `config/secrets.dpapi.json`. Open that folder to back up or inspect — "
        "do not share the file; it only decrypts on this Windows account.",
    ),
}


def load_settings():
    from core.secrets import load_all
    return load_all(PROJECT_ROOT)


def save_settings(data: dict):
    from core.secrets import save_all
    save_all(data, PROJECT_ROOT)


def _make_secret_edit() -> QLineEdit:
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setPlaceholderText("Saved securely — leave blank to keep")
    edit.setClearButtonEnabled(True)
    edit.setMinimumHeight(34)
    return edit


def _make_plain_edit(placeholder: str = "") -> QLineEdit:
    edit = QLineEdit()
    if placeholder:
        edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    edit.setMinimumHeight(34)
    return edit


class UpdateWorker(QThread):
    """Run echelon_source\\BUILD.bat and stream log lines to the UI."""

    log_line = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str, bool)

    def __init__(self, source_path: str, build_script: str):
        super().__init__()
        self.source_path = source_path
        self.build_script = build_script

    def run(self):
        import subprocess

        if not os.path.isfile(self.build_script):
            self.finished_ok.emit(
                False, f"BUILD.bat not found:\n{self.build_script}", False
            )
            return

        env = os.environ.copy()
        env["ECHELON_NO_PAUSE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # cmd /c so batch runs; keep a console-less window when possible
        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        args = ["cmd.exe", "/c", self.build_script]
        self.log_line.emit(f"Running: {self.build_script}")
        self.log_line.emit(f"Source:  {self.source_path}")

        try:
            proc = subprocess.Popen(
                args,
                cwd=self.source_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            assert proc.stdout is not None
            collected: list[str] = []
            for line in proc.stdout:
                clean = line.rstrip("\n")
                if clean.strip():
                    collected.append(clean)
                    self.log_line.emit(clean)
            code = proc.wait()
            if code == 0:
                # Verify portable exe if sibling exists
                portable_exe = os.path.normpath(
                    os.path.join(self.source_path, "..", "echelon", "Echelon.exe")
                )
                if os.path.isfile(portable_exe):
                    self.finished_ok.emit(
                        True,
                        f"Portable app rebuilt:\n{portable_exe}\n\n"
                        "Icons and _internal were published. Restart Echelon to use the new build.",
                        False,
                    )
                else:
                    self.finished_ok.emit(
                        True,
                        "BUILD.bat finished successfully. "
                        "If Echelon.exe is missing under echelon/, check the log.",
                        False,
                    )
            else:
                tail = "\n".join(collected[-12:]) if collected else ""
                msg = f"BUILD.bat exited with code {code}."
                if tail:
                    msg += f"\n\nLast output:\n{tail}"
                self.finished_ok.emit(False, msg, False)
        except Exception as e:
            self.finished_ok.emit(False, str(e), False)


class SettingsWindow(QWidget):
    """Sidebar navigation + stacked pages for each settings category."""

    PAGES = (
        ("Discord", "discord"),
        ("AI & Model", "ai"),
        ("Media APIs", "media"),
        ("Memory & Prompts", "memory"),
        ("Economy", "economy"),
        ("Security", "security"),
        ("Updates", "updates"),
    )

    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window
        self.setWindowTitle(f"{APP_NAME} — Settings")
        self.resize(780, 640)
        self.setMinimumSize(640, 480)
        self._update_worker = None

        self.data = load_settings()
        self._secret_edits: dict[str, QLineEdit] = {}
        self._public_edits: dict[str, QLineEdit] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Settings")
        title.setObjectName("Title")
        titles.addWidget(title)
        subtitle = QLabel(
            f"v{APP_VERSION} · Portable paths · ℹ teaches each AI option"
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        titles.addWidget(subtitle)
        header.addLayout(titles, stretch=1)
        # Corner spinner — spins while saving settings / prompt files
        self.save_loader = LoadingIndicator()
        self.save_loader.set_state("offline")
        self.save_loader.setToolTip("Spins when a settings or prompt file is saving")
        header.addWidget(self.save_loader, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)

        # --- Nav ---
        nav_frame = QFrame()
        nav_frame.setObjectName("Panel")
        nav_frame.setFixedWidth(168)
        nav_l = QVBoxLayout(nav_frame)
        nav_l.setContentsMargins(8, 10, 8, 10)
        nav_l.setSpacing(4)
        nav_title = QLabel("CATEGORIES")
        nav_title.setObjectName("PanelTitle")
        nav_l.addWidget(nav_title)
        self.nav = QListWidget()
        self.nav.setObjectName("SettingsNav")
        self.nav.setSpacing(2)
        for label, _key in self.PAGES:
            self.nav.addItem(QListWidgetItem(label))
        self.nav.currentRowChanged.connect(self._on_nav)
        nav_l.addWidget(self.nav, stretch=1)
        body.addWidget(nav_frame)

        # --- Pages ---
        self.stack = QStackedWidget()
        self.stack.addWidget(self._wrap_scroll(self._page_discord()))
        self.stack.addWidget(self._wrap_scroll(self._page_ai()))
        self.stack.addWidget(self._wrap_scroll(self._page_media()))
        self.stack.addWidget(self._wrap_scroll(self._page_memory()))
        self.stack.addWidget(self._wrap_scroll(self._page_economy()))
        self.stack.addWidget(self._wrap_scroll(self._page_security()))
        self.stack.addWidget(self._wrap_scroll(self._page_updates()))
        body.addWidget(self.stack, stretch=1)
        root.addLayout(body, stretch=1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        clear_btn = QPushButton("Clear Secrets")
        clear_btn.setObjectName("danger")
        clear_btn.setMinimumHeight(38)
        clear_btn.clicked.connect(self.clear_secrets)
        footer.addWidget(clear_btn)
        footer.addStretch()
        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("primary")
        save_btn.setMinimumHeight(38)
        save_btn.setMinimumWidth(150)
        save_btn.clicked.connect(self.save)
        footer.addWidget(save_btn)
        root.addLayout(footer)

        self._populate_fields()
        self.nav.setCurrentRow(0)

    # ----- page builders -----

    def _wrap_scroll(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def _page_discord(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 8, 8)
        layout.setSpacing(12)
        body = QVBoxLayout()
        body.setSpacing(12)
        body.addLayout(self._field_block(
            "Discord Token", "Bot token from Discord Developer Portal",
            secret=True, key="discord_token", help_key="discord_token",
        ))
        body.addLayout(self._field_block(
            "Home Server ID", "Guild used for memory / context home",
            secret=False, key="home_server_id", help_key="home_server_id",
        ))
        body.addLayout(self._field_block(
            "Thoughts Thread ID", "Optional thread for internal thoughts",
            secret=False, key="thoughts_thread_id", help_key="thoughts_thread_id",
        ))
        self.show_secrets = QCheckBox("Show secrets (all pages)")
        self.show_secrets.toggled.connect(self._toggle_secret_visibility)
        body.addWidget(self.show_secrets)
        layout.addWidget(self._card(
            "Discord connection",
            "Platform front-end for the bot. Secrets stay DPAPI-encrypted.",
            body,
        ))
        layout.addStretch(1)
        return page

    def _page_ai(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 8, 8)
        layout.setSpacing(12)
        body = QVBoxLayout()
        body.setSpacing(12)

        # Backend dropdown — Cloud (Groq) default vs local Ollama
        be_block = QVBoxLayout()
        be_block.setSpacing(4)
        be_top = QHBoxLayout()
        be_lab = QLabel("Provider backend")
        be_lab.setObjectName("FieldLabel")
        be_top.addWidget(be_lab)
        be_top.addStretch()
        be_top.addWidget(self._info_button("provider_backend"))
        be_block.addLayout(be_top)
        be_hint = QLabel(
            "Cloud = Groq (default free tier). Ollama = models on this PC."
        )
        be_hint.setObjectName("FieldHint")
        be_hint.setWordWrap(True)
        be_block.addWidget(be_hint)
        self.provider_combo = QComboBox()
        self.provider_combo.setMinimumHeight(34)
        self.provider_combo.addItem("Cloud — Groq (default)", "cloud")
        self.provider_combo.addItem("Local — Ollama", "ollama")
        self.provider_combo.currentIndexChanged.connect(self._on_provider_backend_changed)
        be_block.addWidget(self.provider_combo)
        
        # Local Ollama Model Browser
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setPlaceholderText("Loading local models...")
        self.ollama_model_combo.setMinimumHeight(34)
        self.ollama_model_combo.setEnabled(False)
        self.ollama_model_combo.currentIndexChanged.connect(self._on_ollama_model_selected)
        be_block.addWidget(QLabel("Local Ollama Model:"))
        be_block.addWidget(self.ollama_model_combo)
        
        body.addLayout(be_block)

        body.addLayout(self._field_block(
            "Provider API Key",
            "Cloud: key from console.groq.com · Ollama: leave blank or type ollama",
            secret=True, key="inf_api_key", help_key="inf_api_key",
        ))
        body.addLayout(self._field_block(
            "Model ID",
            "Cloud default: llama-3.3-70b-versatile · Ollama example: llama3",
            secret=False, key="groq_model", help_key="groq_model",
        ))

        prov_row = QHBoxLayout()
        prov_row.setSpacing(6)
        self.prov_btn = QPushButton("Edit Provider (client.py)")
        self.prov_btn.setObjectName("ghost")
        self.prov_btn.setMinimumHeight(40)
        self.prov_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.prov_btn.clicked.connect(self.open_provider_window)
        prov_row.addWidget(self.prov_btn, stretch=1)
        prov_row.addWidget(self._info_button("provider"))
        body.addLayout(prov_row)

        # Refresh button for Ollama models in Settings (reusing refresh_ollama_models logic)
        self.refresh_ollama_btn = QPushButton("Refresh Local Models")
        self.refresh_ollama_btn.setObjectName("ghost")
        self.refresh_ollama_btn.setMinimumHeight(34)
        self.refresh_ollama_btn.clicked.connect(self._refresh_ollama_models)
        self.refresh_ollama_btn.setEnabled(False)
        be_block.addWidget(self.refresh_ollama_btn)

        layout.addWidget(self._card(
            "AI provider & model",
            "Provider = who runs the AI. Default is **Cloud / Groq** (free tier). "
            "Switch the dropdown to **Ollama** for local models — info ℹ and Edit client.py stay available.",
            body,
        ))
        layout.addStretch(1)
        return page

    def _page_media(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 8, 8)
        layout.setSpacing(12)
        body = QVBoxLayout()
        body.setSpacing(12)
        body.addLayout(self._field_block(
            "Unsplash Access Token",
            "Optional — for photo search commands only",
            secret=True, key="us_access_token", help_key="us_access_token",
        ))
        body.addLayout(self._field_block(
            "Unsplash Secret Token",
            "Optional companion secret (most simple searches only need Access)",
            secret=True, key="us_secret_token", help_key="us_secret_token",
        ))

        link_row = QHBoxLayout()
        link_row.setSpacing(8)
        dev_btn = QPushButton("Unsplash developers")
        dev_btn.setObjectName("link")
        dev_btn.setToolTip("Create a free Unsplash developer account")
        dev_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(UNSPLASH_DEV_URL))
        )
        link_row.addWidget(dev_btn)
        apps_btn = QPushButton("Your applications")
        apps_btn.setObjectName("link")
        apps_btn.setToolTip("Register an app and copy Access / Secret keys")
        apps_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(UNSPLASH_APPS_URL))
        )
        link_row.addWidget(apps_btn)
        link_row.addStretch()
        body.addLayout(link_row)

        layout.addWidget(self._card(
            "Media & tool APIs",
            "Optional photo search via Unsplash (free developer keys). "
            "Chat, bank, and games work without these. Small links open signup pages.",
            body,
        ))
        layout.addStretch(1)
        return page

    def _page_memory(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 8, 8)
        layout.setSpacing(12)

        # --- Summarizer (prompt file + model) — same pattern as client.py edit ---
        sum_body = QVBoxLayout()
        sum_body.setSpacing(12)
        sum_body.addLayout(self._field_block(
            "Summarizer model",
            "Optional — blank uses the same Model ID as chat",
            secret=False, key="summarizer_model", help_key="summarizer_model",
        ))
        sum_body.addLayout(self._field_block(
            "Summarizer prompt file",
            "Blank = config/summarizer_prompt.txt · or a custom path",
            secret=False, key="summarizer_prompt_path", help_key="summarizer_prompt",
        ))
        sum_row = QHBoxLayout()
        sum_row.setSpacing(6)
        sum_btn = QPushButton("Edit Summarizer Prompt")
        sum_btn.setObjectName("ghost")
        sum_btn.setMinimumHeight(40)
        sum_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sum_btn.clicked.connect(self.open_summarizer_window)
        sum_row.addWidget(sum_btn, stretch=1)
        sum_row.addWidget(self._info_button("summarizer_prompt"))
        sum_body.addLayout(sum_row)
        layout.addWidget(self._card(
            "Memory summarizer",
            "When Discord memory fills up, this prompt + model compress old lines into "
            "the Summary block. Edit the prompt file like you edit client.py.",
            sum_body,
        ))

        body = QVBoxLayout()
        body.setSpacing(10)
        hint = QLabel(
            "Deeper editors: system personality, orchestration (unifier), "
            "self-memory, and per-user context by Discord server."
        )
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        body.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        editors = [
            ("Edit Personality", "personality", self.open_personality_window),
            ("Unifier", "unifier", self.open_unifier_window),
            ("Self Memory (Bot)", "bot_memory", self.open_bot_memory_window),
            ("User Context by Server", "user_context", self.open_user_context_window),
        ]
        for i, (text, help_key, slot) in enumerate(editors):
            cell = QHBoxLayout()
            cell.setSpacing(6)
            btn = QPushButton(text)
            btn.setObjectName("ghost")
            btn.setMinimumHeight(40)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(slot)
            cell.addWidget(btn, stretch=1)
            cell.addWidget(self._info_button(help_key))
            wrap = QWidget()
            wrap.setLayout(cell)
            grid.addWidget(wrap, i // 2, i % 2)

        body.addLayout(grid)
        layout.addWidget(self._card("Personality & context editors", None, body))
        layout.addStretch(1)
        return page

    def _page_economy(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 8, 8)
        layout.setSpacing(12)

        bank_body = QVBoxLayout()
        bank_body.setSpacing(10)
        bank_hint = QLabel(
            "Browse opted-in users by Discord server and edit the pinned BANK DATA "
            "message (balance). Changes write straight to Discord."
        )
        bank_hint.setObjectName("FieldHint")
        bank_hint.setWordWrap(True)
        bank_body.addWidget(bank_hint)
        brow = QHBoxLayout()
        open_bank = QPushButton("Open bank browser")
        open_bank.setObjectName("primary")
        open_bank.setMinimumHeight(40)
        open_bank.clicked.connect(self.open_economy_window)
        brow.addWidget(open_bank)
        brow.addWidget(self._info_button("economy"))
        brow.addStretch()
        bank_body.addLayout(brow)
        layout.addWidget(self._card("Bank balances", None, bank_body))

        shops_body = QVBoxLayout()
        shops_body.setSpacing(8)
        coming = QLabel("Coming soon!")
        coming.setObjectName("Title")
        shops_body.addWidget(coming)
        shops_hint = QLabel(
            "Player shops, listings, and buy/sell tools will land here. "
            "The shops cog still works in Discord for now."
        )
        shops_hint.setObjectName("FieldHint")
        shops_hint.setWordWrap(True)
        shops_body.addWidget(shops_hint)
        layout.addWidget(self._card("Shops", None, shops_body))

        layout.addStretch(1)
        return page

    def _page_security(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 8, 8)
        layout.setSpacing(12)

        # Cookies
        c_body = QVBoxLayout()
        c_body.setSpacing(10)
        c_hint = QLabel(
            "Place optional cookie files here (for example ytcookies.txt for music). "
            "After adding or changing a file, restart the bot. If you use a frozen "
            "Echelon.exe build, run a rebuild/update so the packaged app can see new files "
            "when they need to be bundled — for normal source/portable installs, restart is enough."
        )
        c_hint.setObjectName("FieldHint")
        c_hint.setWordWrap(True)
        c_body.addWidget(c_hint)
        self.cookies_path_label = QLabel("")
        self.cookies_path_label.setObjectName("FieldHint")
        self.cookies_path_label.setWordWrap(True)
        c_body.addWidget(self.cookies_path_label)
        crow = QHBoxLayout()
        open_cookies = QPushButton("Open cookies folder")
        open_cookies.setObjectName("primary")
        open_cookies.setMinimumHeight(40)
        open_cookies.clicked.connect(self._open_cookies_folder)
        crow.addWidget(open_cookies)
        crow.addWidget(self._info_button("security"))
        crow.addStretch()
        c_body.addLayout(crow)
        layout.addWidget(self._card("Cookies", None, c_body))

        # Secrets
        s_body = QVBoxLayout()
        s_body.setSpacing(10)
        s_hint = QLabel(
            "Encrypted secrets (Discord token, provider keys) live in a DPAPI file "
            "for this Windows user only. Open the folder to back up or locate the file. "
            "Do not post secrets.dpapi.json online — it will not work on another PC/user."
        )
        s_hint.setObjectName("FieldHint")
        s_hint.setWordWrap(True)
        s_body.addWidget(s_hint)
        self.secrets_path_label = QLabel("")
        self.secrets_path_label.setObjectName("FieldHint")
        self.secrets_path_label.setWordWrap(True)
        s_body.addWidget(self.secrets_path_label)
        srow = QHBoxLayout()
        open_secrets = QPushButton("Open secrets folder")
        open_secrets.setObjectName("ghost")
        open_secrets.setMinimumHeight(40)
        open_secrets.clicked.connect(self._open_secrets_folder)
        srow.addWidget(open_secrets)
        srow.addStretch()
        s_body.addLayout(srow)
        layout.addWidget(self._card("Encrypted secrets (DPAPI)", None, s_body))

        self._refresh_security_paths()
        layout.addStretch(1)
        return page

    def _refresh_security_paths(self):
        try:
            from core.paths import ensure_user_layout
            from core.secrets import secrets_path
            root = ensure_user_layout()
            cookies = os.path.join(root, "cookies")
            os.makedirs(cookies, exist_ok=True)
            sec = secrets_path(root)
            os.makedirs(os.path.dirname(sec), exist_ok=True)
            if hasattr(self, "cookies_path_label"):
                self.cookies_path_label.setText(f"Folder: {cookies}")
            if hasattr(self, "secrets_path_label"):
                self.secrets_path_label.setText(f"File: {sec}")
        except Exception as e:
            if hasattr(self, "cookies_path_label"):
                self.cookies_path_label.setText(f"(path error: {e})")

    def _open_in_explorer(self, path: str, *, select_file: bool = False):
        """Open a folder (or select a file) in the OS file manager."""
        path = os.path.abspath(path)
        try:
            if sys.platform.startswith("win"):
                if select_file and os.path.isfile(path):
                    subprocess.Popen(["explorer", "/select,", path])
                else:
                    folder = path if os.path.isdir(path) else os.path.dirname(path)
                    os.makedirs(folder, exist_ok=True)
                    os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                folder = path if os.path.isdir(path) else os.path.dirname(path)
                subprocess.Popen(["open", folder])
            else:
                folder = path if os.path.isdir(path) else os.path.dirname(path)
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            QMessageBox.warning(self, "Open folder", str(e))

    def _open_cookies_folder(self):
        try:
            from core.paths import ensure_user_layout
            root = ensure_user_layout()
            cookies = os.path.join(root, "cookies")
            os.makedirs(cookies, exist_ok=True)
            self._open_in_explorer(cookies)
            QMessageBox.information(
                self,
                "Cookies folder",
                "Opened the cookies folder.\n\n"
                "After you add or replace a cookies file:\n"
                "• Restart the bot (Kill Bot → Run Bot)\n"
                "• If you ship a frozen exe that bundles data, rebuild/update "
                "so the new file is included where needed.",
            )
            if self.main_window and hasattr(self.main_window, "append_log"):
                self.main_window.append_log(f"[INFO] Opened cookies folder: {cookies}")
        except Exception as e:
            QMessageBox.warning(self, "Cookies", str(e))

    def _open_secrets_folder(self):
        try:
            from core.paths import ensure_user_layout
            from core.secrets import secrets_path
            root = ensure_user_layout()
            sec = secrets_path(root)
            os.makedirs(os.path.dirname(sec), exist_ok=True)
            # Select the secrets file if it exists; otherwise open config/
            if os.path.isfile(sec):
                self._open_in_explorer(sec, select_file=True)
            else:
                self._open_in_explorer(os.path.dirname(sec))
            QMessageBox.information(
                self,
                "Secrets location",
                f"Secrets file (DPAPI-encrypted for this Windows user):\n\n{sec}\n\n"
                "This is managed by Settings → Save. Only open the folder for "
                "backup/troubleshooting — do not share the file.",
            )
            if self.main_window and hasattr(self.main_window, "append_log"):
                self.main_window.append_log(f"[INFO] Secrets path: {sec}")
        except Exception as e:
            QMessageBox.warning(self, "Secrets", str(e))

    def _page_updates(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 8, 8)
        layout.setSpacing(12)
        body = QVBoxLayout()
        body.setSpacing(10)

        row = QHBoxLayout()
        hint = QLabel(
            "One-tap rebuild from **echelon_source** (not this portable folder). "
            "Runs BUILD.bat → refreshes sibling echelon\\Echelon.exe + icons. "
            "Your config\\ secrets stay put."
        )
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        row.addWidget(hint, stretch=1)
        row.addWidget(self._info_button("updates"))
        body.addLayout(row)

        path_label_row = QHBoxLayout()
        path_lab = QLabel("echelon_source path")
        path_lab.setObjectName("FieldLabel")
        path_label_row.addWidget(path_lab)
        path_label_row.addStretch()
        path_label_row.addWidget(self._info_button("project_path"))
        body.addLayout(path_label_row)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.project_path_edit = QLineEdit()
        stored = (self.data.get("project_path") or "").strip()
        try:
            from core.paths import source_root
            detected = source_root(stored or None) or resolve_source_root(stored or None)
        except Exception:
            detected = resolve_source_root(stored or None)
        self.project_path_edit.setText(detected)
        self.project_path_edit.setPlaceholderText("…/echelon_source (auto-detected)")
        self.project_path_edit.setMinimumHeight(34)
        path_row.addWidget(self.project_path_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("ghost")
        browse_btn.setMinimumHeight(34)
        browse_btn.clicked.connect(self._browse_project)
        path_row.addWidget(browse_btn)
        detect_btn = QPushButton("Re-detect")
        detect_btn.setObjectName("ghost")
        detect_btn.setMinimumHeight(34)
        detect_btn.clicked.connect(self._redetect_path)
        path_row.addWidget(detect_btn)
        body.addLayout(path_row)

        try:
            from core.paths import describe_layout, build_script_path
            layout_info = describe_layout()
            bat = build_script_path(self.project_path_edit.text().strip() or None)
            info = QLabel(
                f"portable package: {layout_info.get('package_root')}\n"
                f"source for rebuild: {layout_info.get('source_root')}\n"
                f"BUILD.bat: {bat or '(not found)'}\n"
                f"frozen: {layout_info.get('frozen')}"
            )
            info.setObjectName("FieldHint")
            info.setWordWrap(True)
            body.addWidget(info)
        except Exception:
            pass

        self.update_status = QLabel("Ready — press Rebuild Portable App.")
        self.update_status.setObjectName("FieldHint")
        self.update_status.setWordWrap(True)
        body.addWidget(self.update_status)

        upd_row = QHBoxLayout()
        self.check_btn = QPushButton("Rebuild Portable App")
        self.check_btn.setObjectName("primary")
        self.check_btn.setMinimumHeight(40)
        self.check_btn.setMinimumWidth(200)
        self.check_btn.setToolTip("Runs echelon_source\\BUILD.bat (one tap)")
        self.check_btn.clicked.connect(self.check_for_updates)
        upd_row.addWidget(self.check_btn)
        # Page-corner spinner (also uses header save_loader)
        self.update_loader = LoadingIndicator()
        self.update_loader.set_state("offline")
        upd_row.addWidget(self.update_loader)
        upd_row.addStretch()
        body.addLayout(upd_row)

        layout.addWidget(self._card(
            "One-tap rebuild",
            "Source: echelon_source\\BUILD.bat → publishes to echelon\\ "
            "(Echelon.exe, _internal, assets\\icon.png).",
            body,
        ))
        layout.addStretch(1)
        return page

    # ----- UI helpers -----

    def _on_nav(self, row: int):
        if row >= 0:
            self.stack.setCurrentIndex(row)

    def _info_button(self, help_key: str) -> QPushButton:
        btn = QPushButton("ℹ")
        btn.setObjectName("info")
        btn.setFixedSize(32, 32)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("What is this? (learn how it works)")
        btn.clicked.connect(lambda _=False, k=help_key: self._show_help(k))
        return btn

    def _show_help(self, help_key: str):
        title, body = FIELD_HELP.get(
            help_key,
            ("About this setting", "No extra help is available for this field yet."),
        )
        show_info(self, title, body)

    def _card(self, title: str, hint: str | None, body_layout: QVBoxLayout) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        layout.addWidget(t)
        if hint:
            h = QLabel(hint)
            h.setObjectName("CardHint")
            h.setWordWrap(True)
            layout.addWidget(h)
        layout.addLayout(body_layout)
        return card

    def _field_block(
        self,
        label: str,
        hint: str,
        secret: bool,
        key: str,
        help_key: str | None = None,
    ) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        top.addWidget(lab)
        top.addStretch()
        if help_key and help_key in FIELD_HELP:
            top.addWidget(self._info_button(help_key))
        block.addLayout(top)
        if hint:
            h = QLabel(hint)
            h.setObjectName("FieldHint")
            h.setWordWrap(True)
            block.addWidget(h)
        edit = _make_secret_edit() if secret else _make_plain_edit()
        if secret:
            self._secret_edits[key] = edit
        else:
            self._public_edits[key] = edit
        block.addWidget(edit)
        return block

    def _populate_fields(self):
        for key, edit in self._secret_edits.items():
            val = (self.data.get(key) or "").strip()
            if val:
                edit.setText(val)
            else:
                edit.clear()
        for key, edit in self._public_edits.items():
            edit.setText(str(self.data.get(key) or ""))
        if hasattr(self, "provider_combo"):
            backend = (self.data.get("provider_backend") or "cloud").strip().lower()
            if backend not in ("cloud", "ollama"):
                backend = "cloud"
            idx = self.provider_combo.findData(backend)
            if idx < 0:
                idx = 0
            self.provider_combo.blockSignals(True)
            self.provider_combo.setCurrentIndex(idx)
            self.provider_combo.blockSignals(False)

    def _on_provider_backend_changed(self, _index: int = 0):
        """Toggle UI states based on provider backend."""
        if not hasattr(self, "provider_combo"):
            return
        backend = self.provider_combo.currentData() or "cloud"
        is_ollama = (backend == "ollama")

        # Toggle field states - disable opposite group
        if is_ollama:
            # Local selected - disable cloud fields
            self._secret_edits["inf_api_key"].setEnabled(False)
            self._public_edits["groq_model"].setEnabled(False)
            self.prov_btn.setEnabled(False)
            # Enable Ollama fields
            self.ollama_model_combo.setEnabled(True)
            self.refresh_ollama_btn.setEnabled(True)
        else:
            # Cloud selected - disable local fields
            self.ollama_model_combo.setEnabled(False)
            self.refresh_ollama_btn.setEnabled(False)
            # Enable cloud fields
            self._secret_edits["inf_api_key"].setEnabled(True)
            self._public_edits["groq_model"].setEnabled(True)
            self.prov_btn.setEnabled(True)
        if is_ollama:
            self._refresh_ollama_models()
        
        model_edit = self._public_edits.get("groq_model")
        if not model_edit:
            return
        current = model_edit.text().strip()
        if is_ollama:
            if not current or current == "llama-3.3-70b-versatile":
                model_edit.setText("llama3")
        else:
            if not current or current in ("llama3", "llama3.2", "mistral"):
                model_edit.setText("llama-3.3-70b-versatile")

    def _refresh_ollama_models(self):
        self.ollama_model_combo.clear()
        self.ollama_model_combo.addItem("Fetching local models...", "")
        process = QProcess()
        process.start("ollama", ["list"])
        process.finished.connect(lambda exit_code: self._handle_ollama_list(process, exit_code))

    def _handle_ollama_list(self, process, exit_code):
        self.ollama_model_combo.clear()
        if exit_code != 0:
            self.ollama_model_combo.addItem("Ollama not running", "")
            return
        
        output = str(process.readAll(), "utf-8")
        lines = output.splitlines()
        models = [line.split()[0] for line in lines[1:] if line.split()]
        
        if not models:
            self.ollama_model_combo.addItem("No local models found", "")
        else:
            self.ollama_model_combo.addItems(models)

    def _on_ollama_model_selected(self, index):
        model = self.ollama_model_combo.itemText(index)
        if model and not model.startswith("Fetching") and not model.startswith("Ollama") and not model.startswith("No "):
            model_edit = self._public_edits.get("groq_model")
            if model_edit:
                model_edit.setText(model)

    def flash_save_spinner(self, ms: int = 700):
        """Corner spinner used by Settings Save and prompt editors."""
        if not hasattr(self, "save_loader"):
            return
        self.save_loader.set_busy(True, "Saving…")
        QTimer.singleShot(ms, lambda: self.save_loader.set_state("offline"))

    def _toggle_secret_visibility(self, checked: bool):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        for edit in self._secret_edits.values():
            edit.setEchoMode(mode)

    # ----- secondary windows -----

    def open_provider_window(self):
        self.provider_window = ProviderWindow(settings_window=self)
        self.provider_window.show()
        self.provider_window.raise_()
        self.provider_window.activateWindow()

    def open_summarizer_window(self):
        from gui.widgets.summarizerwindow import SummarizerWindow
        self.summarizer_window = SummarizerWindow(settings_window=self)
        self.summarizer_window.show()
        self.summarizer_window.raise_()
        self.summarizer_window.activateWindow()

    def open_bot_memory_window(self):
        from gui.widgets.botmemorywindow import BotMemoryWindow
        if not hasattr(self, "bot_memory_window") or not self.bot_memory_window:
            self.bot_memory_window = BotMemoryWindow(main_window=self.main_window)
        self.bot_memory_window.show()
        self.bot_memory_window.raise_()
        self.bot_memory_window.activateWindow()

    def open_user_context_window(self):
        from gui.widgets.usercontextwindow import UserContextWindow
        if not hasattr(self, "user_context_window") or not self.user_context_window:
            self.user_context_window = UserContextWindow(main_window=self.main_window)
        self.user_context_window.show()
        self.user_context_window.raise_()
        self.user_context_window.activateWindow()

    def open_economy_window(self):
        from gui.widgets.economywindow import EconomyWindow
        if not hasattr(self, "economy_window") or not self.economy_window:
            self.economy_window = EconomyWindow(main_window=self.main_window)
        self.economy_window.show()
        self.economy_window.raise_()
        self.economy_window.activateWindow()

    def open_personality_window(self):
        self.personality_window = PersonalityWindow(settings_window=self)
        self.personality_window.show()
        self.personality_window.raise_()
        self.personality_window.activateWindow()

    def open_unifier_window(self):
        if self.main_window and hasattr(self.main_window, "on_unifier_clicked"):
            self.main_window.on_unifier_clicked()
        else:
            QMessageBox.information(
                self, "Unifier",
                "Open Unifier from the main window once the control panel is ready.",
            )

    # ----- updates -----

    def _browse_project(self):
        start = self.project_path_edit.text().strip() or PROJECT_ROOT
        path = QFileDialog.getExistingDirectory(
            self, "Select echelon_source folder", start
        )
        if path:
            self.project_path_edit.setText(path)

    def _redetect_path(self):
        try:
            from core.paths import source_root
            path = source_root(None) or resolve_source_root(None)
        except Exception:
            path = resolve_source_root(None)
        self.project_path_edit.setText(path)
        self.update_status.setText(f"Re-detected source: {path}")

    def check_for_updates(self):
        """One-tap: run echelon_source\\BUILD.bat (never the portable echelon\\ tree)."""
        typed = self.project_path_edit.text().strip()
        try:
            from core.paths import (
                source_root,
                build_script_path,
                is_buildable_source,
                has_build_venv,
            )
        except Exception:
            source_root = lambda _=None: None  # type: ignore
            build_script_path = lambda _=None: None  # type: ignore

            def is_buildable_source(p):  # type: ignore
                return is_source_tree(p)

            def has_build_venv(p):  # type: ignore
                return os.path.isfile(os.path.join(p or "", ".venv", "Scripts", "python.exe"))

        # Always score candidates: typed path may be polluted portable echelon\
        project_path = source_root(typed or None) or source_root(None) or ""
        if project_path:
            self.project_path_edit.setText(project_path)

        if not project_path or not is_buildable_source(project_path):
            show_error(
                self,
                "echelon_source not found",
                "In-app rebuild needs the **echelon_source** folder with:\n"
                "• BUILD.bat + build_exe.spec\n"
                "• core/ and gui/\n"
                "• .venv (run once: python -m venv .venv && pip install -r requirements.txt)\n\n"
                "The portable **echelon/** app folder is not enough — even if it has "
                "core/ files from an old install.",
                hint="Browse to …\\workspace\\echelon_source",
                details=typed or project_path or "(empty)",
            )
            return

        if not has_build_venv(project_path):
            show_error(
                self,
                "Python venv missing in source",
                f"Found source at:\n{project_path}\n\n"
                "But there is no .venv yet. Open a terminal there and run:\n\n"
                "  python -m venv .venv\n"
                "  .venv\\Scripts\\pip install -r requirements.txt\n\n"
                "Then press Rebuild again.",
                details=project_path,
            )
            return

        bat_path = build_script_path(project_path)
        if not bat_path:
            for name in ("BUILD.bat", "package_portable.bat"):
                cand = os.path.join(project_path, name)
                if os.path.isfile(cand):
                    bat_path = cand
                    break
        if not bat_path or not os.path.isfile(bat_path):
            show_error(
                self,
                "BUILD.bat not found",
                f"Expected BUILD.bat inside:\n{project_path}",
                details=project_path,
            )
            return

        payload = {k: e.text().strip() for k, e in self._secret_edits.items()}
        payload.update({k: e.text().strip() for k, e in self._public_edits.items()})
        if hasattr(self, "provider_combo"):
            payload["provider_backend"] = self.provider_combo.currentData() or "cloud"
        payload["project_path"] = project_path
        save_settings(payload)

        reply = QMessageBox.question(
            self,
            "Rebuild portable app?",
            "This runs one command:\n\n"
            f"  {bat_path}\n\n"
            "It rebuilds Echelon.exe into the sibling echelon\\ folder "
            "(icons included). Keep this window open — watch the spinner.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.check_btn.setEnabled(False)
        self.update_status.setText("Building… this can take a few minutes.")
        if hasattr(self, "update_loader"):
            self.update_loader.set_busy(True, "Building…")
        if hasattr(self, "save_loader"):
            self.save_loader.set_busy(True, "Building…")
        if self.main_window and hasattr(self.main_window, "append_log"):
            self.main_window.append_log(f"[update] Starting {bat_path}")

        self._update_worker = UpdateWorker(project_path, bat_path)
        self._update_worker.log_line.connect(self._on_update_log)
        self._update_worker.finished_ok.connect(self._on_update_done)
        self._update_worker.start()

    def _on_update_log(self, line: str):
        self.update_status.setText(line[:240] if line else "Building…")
        if hasattr(self, "term_win") and self.term_win:
            self.term_win.append_line(line)
        if self.main_window and hasattr(self.main_window, "append_log"):
            self.main_window.append_log(f"[update] {line}")

    def _on_update_done(self, ok: bool, message: str, already_current: bool = False):
        self.check_btn.setEnabled(True)
        if hasattr(self, "update_loader"):
            self.update_loader.set_state("online" if ok else "error")
            QTimer.singleShot(1200, lambda: self.update_loader.set_state("offline"))
        if hasattr(self, "save_loader"):
            self.save_loader.set_state("online" if ok else "error")
            QTimer.singleShot(1200, lambda: self.save_loader.set_state("offline"))
        self.update_status.setText(message.split("\n")[0][:240])
        if ok:
            title = "Already up to date" if already_current else "Rebuild complete"
            QMessageBox.information(self, title, message)
            if self.main_window and hasattr(self.main_window, "append_log"):
                self.main_window.append_log(f"[update] OK: {message.splitlines()[0]}")
        else:
            log = self.main_window.append_log if self.main_window else None
            present_failure(self, message, log_fn=log, default_title="Rebuild failed")

    # ----- persist -----

    def save(self):
        self.flash_save_spinner(900)
        payload = {}
        for key, edit in self._secret_edits.items():
            payload[key] = edit.text().strip()
        for key, edit in self._public_edits.items():
            payload[key] = edit.text().strip()
        if hasattr(self, "project_path_edit"):
            payload["project_path"] = self.project_path_edit.text().strip()
        if hasattr(self, "provider_combo"):
            payload["provider_backend"] = self.provider_combo.currentData() or "cloud"
        if not (payload.get("groq_model") or "").strip():
            if (payload.get("provider_backend") or "cloud") == "ollama":
                payload["groq_model"] = "llama3"
            else:
                payload["groq_model"] = "llama-3.3-70b-versatile"
        if not (payload.get("provider_backend") or "").strip():
            payload["provider_backend"] = "cloud"

        save_settings(payload)
        # Ensure summarizer prompt file exists for the configured path
        try:
            from core.summarizer_prompt import ensure_summarizer_prompt_file
            ensure_summarizer_prompt_file(payload.get("summarizer_prompt_path") or None)
        except Exception:
            pass

        self.data = load_settings()
        self._populate_fields()
        if hasattr(self, "project_path_edit"):
            self.project_path_edit.setText(
                resolve_source_root((self.data.get("project_path") or "").strip() or None)
            )
        try:
            from core.paths import package_root
            from core.secrets import settings_path, secrets_path
            root = package_root()
            pub = settings_path(root)
            sec = secrets_path(root)
        except Exception:
            pub = "config/settings.json"
            sec = "config/secrets.dpapi.json"
        QMessageBox.information(
            self,
            "Saved",
            "Settings written to the portable package:\n\n"
            f"• Public (model, server IDs, paths, provider):\n  {pub}\n"
            "  (plain JSON — not encrypted)\n\n"
            f"• Secrets (tokens / API keys):\n  {sec}\n"
            "  (Windows DPAPI for this user only)\n\n"
            "Personality, provider, summarizer prompt, and unifier each save "
            "their own source files. Restart the bot after provider changes.",
        )

    def clear_secrets(self):
        reply = QMessageBox.question(
            self,
            "Clear secrets?",
            "Remove all stored API tokens from secure storage?\n"
            "Public IDs (server / thread / model) are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from core.secrets import clear_secrets as wipe_secrets
        wipe_secrets(PROJECT_ROOT)
        for edit in self._secret_edits.values():
            edit.clear()
        self.data = load_settings()
        QMessageBox.information(self, "Cleared", "Stored secrets were removed.")
