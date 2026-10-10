# gui/widgets/settingswindow.py
# Multi-page settings: Discord | AI | Media | Memory | Economy | Security | Updates
# Cloud, OpenRouter, and Ollama: hide unused fields; each mode keeps its own model.

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt, QUrl, QTimer, QProcess
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
    QRadioButton,
    QButtonGroup,
)

from gui.theme import APP_NAME, APP_VERSION
from gui.widgets.providerwindow import ProviderWindow
from gui.widgets.personalitywindow import PersonalityWindow
from gui.widgets.loading import LoadingIndicator
from gui.widgets.dialogs import show_error, show_info, present_failure
from gui.widgets.settings_help import FIELD_HELP
from gui.widgets.settings_workers import SettingsSaveWorker, UpdatePrepareWorker

UNSPLASH_DEV_URL = "https://unsplash.com/developers"
UNSPLASH_APPS_URL = "https://unsplash.com/oauth/applications"
OPENROUTER_KEYS_URL = "https://openrouter.ai/keys"
OPENROUTER_MODELS_URL = "https://openrouter.ai/models"

_CLOUD_DEFAULT = "qwen/qwen3.8-27b"
_OLLAMA_DEFAULT = "llama3"
_OPENROUTER_DEFAULT = "openrouter/free"
_OLLAMA_PLACEHOLDER_PREFIXES = ("Fetching", "Ollama not", "No local")

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


def _valid_ollama_selection(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return not any(t.startswith(p) for p in _OLLAMA_PLACEHOLDER_PREFIXES)


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
        self._save_worker = None
        self._save_spin_token = 0
        self._cloud_only: list = []
        self._ollama_only: list = []
        self._openrouter_only: list = []
        self._pending_ollama_model = _OLLAMA_DEFAULT
        self._ollama_process: QProcess | None = None

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
        self.save_loader = LoadingIndicator()
        self.save_loader.set_state("offline")
        self.save_loader.setToolTip("Spins when a settings or prompt file is saving")
        header.addWidget(self.save_loader, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)

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
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setObjectName("primary")
        self.save_btn.setMinimumHeight(38)
        self.save_btn.setMinimumWidth(150)
        self.save_btn.clicked.connect(self.save)
        footer.addWidget(self.save_btn)
        root.addLayout(footer)

        self._populate_fields()
        self.nav.setCurrentRow(0)

    def show_page(self, key: str) -> None:
        """Open one settings category. `key` matches PAGES, for example `updates`."""
        for index, (_label, page_key) in enumerate(self.PAGES):
            if page_key == key:
                self.nav.setCurrentRow(index)
                return

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
        self.show_secrets = QCheckBox("Show secrets (all pages)")
        self.show_secrets.setMinimumHeight(28)
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

        self._cloud_only = []
        self._ollama_only = []
        self._openrouter_only = []

        be_block = QVBoxLayout()
        be_block.setSpacing(8)
        be_top = QHBoxLayout()
        be_lab = QLabel("Provider backend")
        be_lab.setObjectName("FieldLabel")
        be_top.addWidget(be_lab)
        be_top.addStretch()
        be_top.addWidget(self._info_button("provider_backend"))
        be_block.addLayout(be_top)
        be_hint = QLabel(
            "Groq is the default cloud. OpenRouter uses its own key and model id. "
            "Ollama runs on this PC. Only fields for the selected mode are shown."
        )
        be_hint.setObjectName("FieldHint")
        be_hint.setWordWrap(True)
        be_block.addWidget(be_hint)
        self.provider_combo = QComboBox()
        self.provider_combo.setMinimumHeight(34)
        self.provider_combo.addItem("Cloud — Groq (default)", "cloud")
        self.provider_combo.addItem("OpenRouter", "openrouter")
        self.provider_combo.addItem("Local — Ollama", "ollama")
        self.provider_combo.currentIndexChanged.connect(self._on_provider_backend_changed)
        be_block.addWidget(self.provider_combo)
        body.addLayout(be_block)

        # Cloud-only: API key
        api_inner = QVBoxLayout()
        api_inner.setContentsMargins(0, 0, 0, 0)
        api_inner.addLayout(self._field_block(
            "Provider API Key",
            "From console.groq.com — Cloud only (hidden for Ollama)",
            secret=True, key="inf_api_key", help_key="inf_api_key",
        ))
        api_wrap = QWidget()
        api_wrap.setLayout(api_inner)
        self._cloud_only.append(api_wrap)
        body.addWidget(api_wrap)

        # Cloud-only: model text
        model_inner = QVBoxLayout()
        model_inner.setContentsMargins(0, 0, 0, 0)
        model_inner.addLayout(self._field_block(
            "Model ID (Cloud)",
            f"Default: {_CLOUD_DEFAULT}",
            secret=False, key="cloud_model", help_key="groq_model",
        ))
        model_wrap = QWidget()
        model_wrap.setLayout(model_inner)
        self._cloud_only.append(model_wrap)
        body.addWidget(model_wrap)

        # Ollama-only
        ol_wrap = QWidget()
        ol_l = QVBoxLayout(ol_wrap)
        ol_l.setContentsMargins(0, 0, 0, 0)
        ol_l.setSpacing(12)
        ol_top = QHBoxLayout()
        ol_lab = QLabel("Local Ollama Model")
        ol_lab.setObjectName("FieldLabel")
        ol_top.addWidget(ol_lab)
        ol_top.addStretch()
        ol_top.addWidget(self._info_button("provider_backend"))
        ol_l.addLayout(ol_top)
        ol_hint = QLabel("Pick a model from `ollama list`. No API key required.")
        ol_hint.setObjectName("FieldHint")
        ol_hint.setWordWrap(True)
        ol_l.addWidget(ol_hint)
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setMinimumHeight(34)
        self.ollama_model_combo.setPlaceholderText("Select a local model…")
        ol_l.addWidget(self.ollama_model_combo)
        self.refresh_ollama_btn = QPushButton("Refresh Local Models")
        self.refresh_ollama_btn.setObjectName("ghost")
        self.refresh_ollama_btn.setMinimumHeight(34)
        self.refresh_ollama_btn.clicked.connect(self._refresh_ollama_models)
        ol_l.addWidget(self.refresh_ollama_btn)
        self._ollama_only.append(ol_wrap)
        body.addWidget(ol_wrap)

        or_wrap = QWidget()
        or_l = QVBoxLayout(or_wrap)
        or_l.setContentsMargins(0, 0, 0, 0)
        or_l.setSpacing(12)
        or_l.addLayout(self._field_block(
            "OpenRouter API Key",
            "From openrouter.ai/keys — stored separately from the Groq key",
            secret=True, key="openrouter_api_key", help_key="openrouter_api_key",
        ))
        or_l.addLayout(self._field_block(
            "Model ID (OpenRouter)",
            f"Default: {_OPENROUTER_DEFAULT}. Paste any author/slug from the models page.",
            secret=False, key="openrouter_model", help_key="openrouter_model",
        ))
        or_links = QHBoxLayout()
        or_links.setSpacing(8)
        or_keys = QPushButton("OpenRouter keys")
        or_keys.setObjectName("link")
        or_keys.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(OPENROUTER_KEYS_URL)))
        or_links.addWidget(or_keys)
        or_models = QPushButton("OpenRouter models")
        or_models.setObjectName("link")
        or_models.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(OPENROUTER_MODELS_URL)))
        or_links.addWidget(or_models)
        or_links.addStretch()
        or_l.addLayout(or_links)
        self._openrouter_only.append(or_wrap)
        body.addWidget(or_wrap)

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

        layout.addWidget(self._card(
            "AI provider & model",
            "Provider = who runs the AI. Groq, OpenRouter, and Ollama each keep their own fields.",
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
        dev_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(UNSPLASH_DEV_URL)))
        link_row.addWidget(dev_btn)
        apps_btn = QPushButton("Your applications")
        apps_btn.setObjectName("link")
        apps_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(UNSPLASH_APPS_URL)))
        link_row.addWidget(apps_btn)
        link_row.addStretch()
        body.addLayout(link_row)
        layout.addWidget(self._card(
            "Media & tool APIs",
            "Optional photo search via Unsplash (free developer keys).",
            body,
        ))
        layout.addStretch(1)
        return page

    def _page_memory(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 8, 8)
        layout.setSpacing(12)
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
        sum_btn = QPushButton("Edit Summarizer Prompt")
        sum_btn.setObjectName("ghost")
        sum_btn.setMinimumHeight(40)
        sum_btn.clicked.connect(self.open_summarizer_window)
        sum_row.addWidget(sum_btn, stretch=1)
        sum_row.addWidget(self._info_button("summarizer_prompt"))
        sum_body.addLayout(sum_row)
        layout.addWidget(self._card(
            "Memory summarizer",
            "When Discord memory fills up, this prompt + model compress old lines.",
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
        o_body = QVBoxLayout()
        o_body.setSpacing(12)
        o_body.addLayout(self._field_block(
            "Owner IDs",
            "Discord user ids for owner-only commands and for mute, timeout, kick, and ban. "
            "Separate more than one with a comma. "
            "Developer Mode, then right-click a name → Copy User ID. "
            "Leave blank to use the Discord application owner.",
            secret=False,
            key="owner_id",
            help_key="owner_id",
        ))
        self.security_admin_box = QCheckBox("Admin tools")
        self.security_admin_box.setToolTip(
            "Same switch as the main window. Off, and Eche never sees mute, timeout, kick, or ban."
        )
        self.security_admin_box.toggled.connect(self._on_security_admin_toggled)
        o_body.addWidget(self.security_admin_box)
        o_hint = QLabel(
            "The bot needs Kick Members, Ban Members, Moderate Members, and Mute Members, "
            "and its role has to sit above the person it acts on."
        )
        o_hint.setObjectName("FieldHint")
        o_hint.setWordWrap(True)
        o_body.addWidget(o_hint)
        layout.addWidget(self._card(
            "Owner",
            "Who may ask Eche to mute, timeout, kick, or ban.",
            o_body,
        ))
        c_body = QVBoxLayout()
        c_hint = QLabel(
            "Place optional cookie files here (for example ytcookies.txt for music). "
            "After adding or changing a file, restart the bot."
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
        s_body = QVBoxLayout()
        s_hint = QLabel(
            "Encrypted secrets (Discord token, provider keys) live in a DPAPI file "
            "for this Windows user only."
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
            self.cookies_path_label.setText(cookies)
            self.secrets_path_label.setText(secrets_path(root))
        except Exception as e:
            self.cookies_path_label.setText(str(e))
            self.secrets_path_label.setText("")

    def _open_cookies_folder(self):
        try:
            from core.paths import ensure_user_layout
            path = os.path.join(ensure_user_layout(), "cookies")
            os.makedirs(path, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as e:
            QMessageBox.warning(self, "Cookies", str(e))

    def _open_secrets_folder(self):
        try:
            from core.paths import ensure_user_layout
            from core.secrets import secrets_path
            root = ensure_user_layout()
            sec = secrets_path(root)
            folder = os.path.dirname(sec)
            os.makedirs(folder, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
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
            "Fetch application source from GitHub, or build a local eche_source folder. "
            "Eche closes first so Windows can replace the running app. "
            "A command window named Eche update finishes the copy and build, then opens Eche again."
        )
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        row.addWidget(hint, stretch=1)
        row.addWidget(self._info_button("updates"))
        body.addLayout(row)

        self.update_mode = QButtonGroup(self)
        self.radio_github = QRadioButton("GitHub  ·  sevinOG/eche main")
        self.radio_local = QRadioButton("Local source folder")
        self.radio_github.setChecked(True)
        self.update_mode.addButton(self.radio_github, 0)
        self.update_mode.addButton(self.radio_local, 1)
        self.radio_github.toggled.connect(self._on_update_mode)
        body.addWidget(self.radio_github)
        body.addWidget(self.radio_local)

        path_label_row = QHBoxLayout()
        path_lab = QLabel("Local eche_source path")
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
        self.project_path_edit.setText(detected or "")
        self.project_path_edit.setPlaceholderText("…/eche_source")
        self.project_path_edit.setMinimumHeight(34)
        path_row.addWidget(self.project_path_edit, stretch=1)
        self._update_browse = QPushButton("Browse…")
        self._update_browse.setObjectName("ghost")
        self._update_browse.setMinimumHeight(34)
        self._update_browse.clicked.connect(self._browse_project)
        path_row.addWidget(self._update_browse)
        self._update_detect = QPushButton("Re-detect")
        self._update_detect.setObjectName("ghost")
        self._update_detect.setMinimumHeight(34)
        self._update_detect.clicked.connect(self._redetect_path)
        path_row.addWidget(self._update_detect)
        body.addLayout(path_row)
        self.update_status = QLabel("Ready. GitHub is the default. Local source is optional.")
        self.update_status.setObjectName("FieldHint")
        self.update_status.setWordWrap(True)
        body.addWidget(self.update_status)
        upd_row = QHBoxLayout()
        self.check_btn = QPushButton("Update Eche")
        self.check_btn.setObjectName("primary")
        self.check_btn.setMinimumHeight(40)
        self.check_btn.setMinimumWidth(200)
        self.check_btn.clicked.connect(self.check_for_updates)
        upd_row.addWidget(self.check_btn)
        self.update_loader = LoadingIndicator()
        self.update_loader.set_state("offline")
        upd_row.addWidget(self.update_loader)
        upd_row.addStretch()
        body.addLayout(upd_row)
        layout.addWidget(self._card(
            "Update",
            "GitHub download, or the folder you pick. The running app closes before files are replaced.",
            body,
        ))
        layout.addStretch(1)
        self._on_update_mode(True)
        return page

    def _on_update_mode(self, _checked: bool = False):
        local = bool(getattr(self, "radio_local", None) and self.radio_local.isChecked())
        for widget in (
            getattr(self, "project_path_edit", None),
            getattr(self, "_update_browse", None),
            getattr(self, "_update_detect", None),
        ):
            if widget is not None:
                widget.setEnabled(local)

    def _on_nav(self, row: int):
        if row >= 0:
            self.stack.setCurrentIndex(row)

    def _info_button(self, help_key: str) -> QPushButton:
        btn = QPushButton("ℹ")
        btn.setObjectName("info")
        btn.setFixedSize(20, 20)
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
        layout.setContentsMargins(16, 16, 20, 16)
        layout.setSpacing(12)
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
        top.setSpacing(8)
        top.setContentsMargins(0, 6, 0, 2)
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        top.addWidget(lab)
        top.addStretch()
        if help_key and help_key in FIELD_HELP:
            btn = self._info_button(help_key)
            top.addWidget(btn, alignment=Qt.AlignmentFlag.AlignTop)
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

    def _apply_provider_ui(self):
        if not hasattr(self, "provider_combo"):
            return
        backend = self.provider_combo.currentData() or "cloud"
        for w in self._cloud_only:
            w.setVisible(backend == "cloud")
        for w in self._ollama_only:
            w.setVisible(backend == "ollama")
        for w in self._openrouter_only:
            w.setVisible(backend == "openrouter")
        if backend == "ollama":
            self._refresh_ollama_models()

    def _on_provider_backend_changed(self, _index: int = 0):
        self._apply_provider_ui()

    def _populate_fields(self):
        for key, edit in self._secret_edits.items():
            val = (self.data.get(key) or "").strip()
            if val:
                edit.setText(val)
            else:
                edit.clear()
        for key, edit in self._public_edits.items():
            if key in ("cloud_model", "openrouter_model"):
                continue
            edit.setText(str(self.data.get(key) or ""))

        saved_backend = (self.data.get("provider_backend") or "cloud").strip().lower()
        cloud_m = (self.data.get("cloud_model") or "").strip()
        if not cloud_m and saved_backend == "cloud":
            cloud_m = (self.data.get("groq_model") or "").strip()
        cloud_m = cloud_m or _CLOUD_DEFAULT
        if "cloud_model" in self._public_edits:
            self._public_edits["cloud_model"].setText(cloud_m)

        openrouter_m = (self.data.get("openrouter_model") or _OPENROUTER_DEFAULT).strip()
        if "openrouter_model" in self._public_edits:
            self._public_edits["openrouter_model"].setText(openrouter_m or _OPENROUTER_DEFAULT)

        self._pending_ollama_model = (
            self.data.get("ollama_model") or _OLLAMA_DEFAULT
        ).strip() or _OLLAMA_DEFAULT

        if hasattr(self, "provider_combo"):
            backend = (self.data.get("provider_backend") or "cloud").strip().lower()
            if backend not in ("cloud", "ollama", "openrouter"):
                backend = "cloud"
            idx = self.provider_combo.findData(backend)
            self.provider_combo.blockSignals(True)
            self.provider_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.provider_combo.blockSignals(False)

        self._apply_provider_ui()
        if hasattr(self, "security_admin_box"):
            from core.admin_tools import flag_on
            self.security_admin_box.blockSignals(True)
            self.security_admin_box.setChecked(flag_on(self.data.get("admin_tools"), ""))
            self.security_admin_box.blockSignals(False)

    def _refresh_ollama_models(self):
        if not hasattr(self, "ollama_model_combo"):
            return
        self.ollama_model_combo.clear()
        self.ollama_model_combo.addItem("Fetching local models...", "")
        process = QProcess(self)
        self._ollama_process = process
        process.finished.connect(
            lambda code, _status, p=process: self._handle_ollama_list(p, code)
        )
        process.start("ollama", ["list"])

    def _handle_ollama_list(self, process: QProcess, exit_code: int):
        if not hasattr(self, "ollama_model_combo"):
            return
        self.ollama_model_combo.blockSignals(True)
        self.ollama_model_combo.clear()
        if exit_code != 0:
            self.ollama_model_combo.addItem("Ollama not running", "")
            self.ollama_model_combo.blockSignals(False)
            return
        raw = process.readAllStandardOutput()
        output = bytes(raw).decode("utf-8", errors="replace")
        lines = output.splitlines()
        models = [line.split()[0] for line in lines[1:] if line.split()]
        if not models:
            self.ollama_model_combo.addItem("No local models found", "")
        else:
            for m in models:
                self.ollama_model_combo.addItem(m, m)
            want = self._pending_ollama_model
            i = self.ollama_model_combo.findText(want)
            if i >= 0:
                self.ollama_model_combo.setCurrentIndex(i)
        self.ollama_model_combo.blockSignals(False)

    def begin_save_spinner(self, message: str = "Saving…"):
        """Show the header spinner and keep it spinning until end_save_spinner."""
        if not hasattr(self, "save_loader"):
            return
        self._save_spin_token = getattr(self, "_save_spin_token", 0) + 1
        self.save_loader.set_busy(True, message)

    def end_save_spinner(self, ok: bool = True, note: str = ""):
        if not hasattr(self, "save_loader"):
            return
        token = getattr(self, "_save_spin_token", 0)
        if not note:
            note = "Saved" if ok else "Save failed"
        self.save_loader.set_state("online" if ok else "error", note)

        def _idle(expected=token):
            if getattr(self, "_save_spin_token", 0) != expected:
                return
            worker = getattr(self, "_save_worker", None)
            if worker is not None and worker.isRunning():
                return
            self.save_loader.set_state("offline")

        QTimer.singleShot(700, _idle)

    def flash_save_spinner(self, ms: int = 700):
        self.begin_save_spinner()
        QTimer.singleShot(ms, lambda: self.end_save_spinner(True))

    def _toggle_secret_visibility(self, checked: bool):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        for edit in self._secret_edits.values():
            edit.setEchoMode(mode)

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
        main = self.main_window
        if main is not None and hasattr(main, "open_economy_window"):
            main.open_economy_window()
            return
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

    def _browse_project(self):
        start = self.project_path_edit.text().strip() or PROJECT_ROOT
        path = QFileDialog.getExistingDirectory(
            self, "Select eche_source folder", start
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

    def _collect_payload(self) -> dict:
        payload: dict = {}
        for key, edit in self._secret_edits.items():
            payload[key] = edit.text().strip()
        for key, edit in self._public_edits.items():
            if key in ("cloud_model", "groq_model", "ollama_model", "openrouter_model"):
                continue
            payload[key] = edit.text().strip()

        backend = "cloud"
        if hasattr(self, "provider_combo"):
            backend = self.provider_combo.currentData() or "cloud"
        payload["provider_backend"] = backend

        cloud_m = ""
        if "cloud_model" in self._public_edits:
            cloud_m = self._public_edits["cloud_model"].text().strip()
        if not cloud_m:
            cloud_m = (self.data.get("cloud_model") or _CLOUD_DEFAULT).strip()
        payload["cloud_model"] = cloud_m or _CLOUD_DEFAULT

        ollama_m = (self.data.get("ollama_model") or _OLLAMA_DEFAULT).strip()
        if hasattr(self, "ollama_model_combo"):
            text = self.ollama_model_combo.currentText().strip()
            data = self.ollama_model_combo.currentData()
            if _valid_ollama_selection(text):
                ollama_m = text
            elif data and _valid_ollama_selection(str(data)):
                ollama_m = str(data).strip()
        payload["ollama_model"] = ollama_m or _OLLAMA_DEFAULT
        self._pending_ollama_model = payload["ollama_model"]

        openrouter_m = (self.data.get("openrouter_model") or _OPENROUTER_DEFAULT).strip()
        if "openrouter_model" in self._public_edits:
            typed = self._public_edits["openrouter_model"].text().strip()
            if typed:
                openrouter_m = typed
        payload["openrouter_model"] = openrouter_m or _OPENROUTER_DEFAULT

        if backend == "ollama":
            payload["groq_model"] = payload["ollama_model"]
        elif backend == "openrouter":
            payload["groq_model"] = payload["openrouter_model"]
        else:
            payload["groq_model"] = payload["cloud_model"]

        if hasattr(self, "project_path_edit"):
            payload["project_path"] = self.project_path_edit.text().strip()
        if hasattr(self, "security_admin_box"):
            payload["admin_tools"] = "1" if self.security_admin_box.isChecked() else "0"
        else:
            admin = (self.data.get("admin_tools") or "").strip()
            try:
                admin = (load_settings().get("admin_tools") or admin).strip()
            except Exception:
                pass
            payload["admin_tools"] = admin
        # The main-window Default tools switch is not on this form.
        # Keep whatever it last saved so a settings save does not turn it back on.
        try:
            payload["default_tools"] = (load_settings().get("default_tools") or "").strip()
        except Exception:
            payload["default_tools"] = (self.data.get("default_tools") or "").strip()
        owner_raw = (payload.get("owner_id") or "").strip()
        if owner_raw:
            from core.admin_tools import parse_owner_ids
            parsed_owners = parse_owner_ids(owner_raw)
            if parsed_owners:
                payload["owner_id"] = ", ".join(str(item) for item in parsed_owners)
        return payload

    def _on_security_admin_toggled(self, checked: bool):
        window = self.main_window
        box = getattr(window, "admin_tools_box", None) if window is not None else None
        if box is not None:
            if box.isChecked() != checked:
                box.setChecked(checked)
            return
        try:
            settings = load_settings()
            settings["admin_tools"] = "1" if checked else "0"
            save_settings(settings)
        except Exception as e:
            self.security_admin_box.blockSignals(True)
            self.security_admin_box.setChecked(not checked)
            self.security_admin_box.blockSignals(False)
            if self.main_window and hasattr(self.main_window, "append_log"):
                self.main_window.append_log(f"[WARN] Could not save admin tools toggle: {e}")

    def check_for_updates(self):
        from core.app_update import default_source_target

        local = bool(self.radio_local.isChecked())
        typed = self.project_path_edit.text().strip()
        if local:
            target = typed
            try:
                from core.paths import is_buildable_source
            except Exception:
                is_buildable_source = is_source_tree  # type: ignore
            if not target or not is_buildable_source(target):
                show_error(
                    self,
                    "Local source not found",
                    "Pick the eche_source folder that contains BUILD.bat, core/, and gui/.",
                    hint="Browse to the source tree, not the portable eche folder.",
                    details=typed or "(empty)",
                )
                return
        else:
            target = default_source_target(typed or None)

        self._update_target = os.path.abspath(target)
        where = "GitHub (sevinOG/eche, main)" if not local else self._update_target
        git_note = ""
        if not local and os.path.isdir(os.path.join(self._update_target, ".git")):
            git_note = (
                "\n\nThat folder is a git checkout. "
                "The GitHub copy overwrites code files there."
            )
        reply = QMessageBox.question(
            self,
            "Update Eche?",
            "Eche will close so the update can replace the running files.\n\n"
            f"Source: {where}\n"
            f"Build in: {self._update_target}\n\n"
            "A command window named Eche update stays open, runs the build, "
            "and starts Eche again. Cookies, settings, and secrets stay in place. "
            f"Code files are updated.{git_note}\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if local:
            payload = self._collect_payload()
            payload["project_path"] = self._update_target
            try:
                save_settings(payload)
            except Exception as exc:
                if self.main_window and hasattr(self.main_window, "append_log"):
                    self.main_window.append_log(f"[update] Could not save the source path: {exc}")

        self.check_btn.setEnabled(False)
        self.stack.setEnabled(False)
        self.update_status.setText(
            "Downloading from GitHub…" if not local else "Preparing the local update…"
        )
        if hasattr(self, "update_loader"):
            self.update_loader.set_busy(True, self.update_status.text())
        self._update_worker = UpdatePrepareWorker("github" if not local else "local")
        self._update_worker.log_line.connect(self._on_update_log)
        self._update_worker.finished_ok.connect(self._on_update_ready)
        self._update_worker.start()

    def _on_update_log(self, line: str):
        self.update_status.setText(line[:240] if line else "Working…")
        if self.main_window and hasattr(self.main_window, "append_log"):
            self.main_window.append_log(f"[update] {line}")

    def _on_update_ready(self, ok: bool, stage_or_error: str):
        if not ok:
            self.check_btn.setEnabled(True)
            self.stack.setEnabled(True)
            if hasattr(self, "update_loader"):
                self.update_loader.set_state("error")
                QTimer.singleShot(1200, lambda: self.update_loader.set_state("offline"))
            self.update_status.setText(stage_or_error.split("\n")[0][:240])
            log = self.main_window.append_log if self.main_window else None
            present_failure(self, stage_or_error, log_fn=log, default_title="Update failed")
            return

        stage = stage_or_error.strip() or None
        target = getattr(self, "_update_target", "") or ""
        try:
            from core.app_update import launch_handoff, relaunch_command, write_handoff_script

            script = write_handoff_script(
                pid=os.getpid(),
                target=target,
                relaunch=relaunch_command(target),
                stage=stage,
            )
            launch_handoff(script)
        except Exception as exc:
            self.check_btn.setEnabled(True)
            self.stack.setEnabled(True)
            if hasattr(self, "update_loader"):
                self.update_loader.set_state("error")
            self.update_status.setText(str(exc)[:240])
            log = self.main_window.append_log if self.main_window else None
            present_failure(self, str(exc), log_fn=log, default_title="Update failed")
            return

        self.update_status.setText("Closing Eche. The Eche update window will finish the build.")
        if hasattr(self, "update_loader"):
            self.update_loader.set_busy(True, "Closing…")
        if self.main_window and hasattr(self.main_window, "shutdown_for_update"):
            self.main_window.shutdown_for_update()
            return
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(400, app.quit)

    def _save_busy(self) -> bool:
        worker = getattr(self, "_save_worker", None)
        return worker is not None and worker.isRunning()

    def save(self):
        if self._save_busy():
            return
        self.save_btn.setEnabled(False)
        self.stack.setEnabled(False)
        self.begin_save_spinner("Saving…")
        payload = self._collect_payload()
        if not (payload.get("provider_backend") or "").strip():
            payload["provider_backend"] = "cloud"
        # Return to the event loop first so the spinner can paint, then write.
        QTimer.singleShot(0, lambda: self._start_save_worker(payload, clear=False))

    def _start_save_worker(self, payload: dict | None, *, clear: bool):
        self._save_was_clear = clear
        self._save_worker = SettingsSaveWorker(payload, clear=clear)
        self._save_worker.finished_ok.connect(self._on_settings_saved)
        self._save_worker.start()

    def _on_settings_saved(self, ok: bool, err: str):
        self.save_btn.setEnabled(True)
        self.stack.setEnabled(True)
        if getattr(self, "_save_was_clear", False):
            self._save_was_clear = False
            if not ok:
                self.end_save_spinner(False)
                show_error(self, "Clear failed", err or "Stored secrets could not be removed.")
                return
            for edit in self._secret_edits.values():
                edit.clear()
            try:
                self.data = load_settings()
            except Exception:
                pass
            self.end_save_spinner(True, "Cleared")
            QMessageBox.information(self, "Cleared", "Stored secrets were removed.")
            return
        if not ok:
            self.end_save_spinner(False)
            show_error(self, "Save failed", err or "Settings could not be saved.")
            return
        try:
            self.data = load_settings()
            self._populate_fields()
            main_box = getattr(self.main_window, "admin_tools_box", None)
            if main_box is not None:
                from core.admin_tools import flag_on
                on = flag_on(self.data.get("admin_tools"), "")
                if main_box.isChecked() != on:
                    main_box.blockSignals(True)
                    main_box.setChecked(on)
                    main_box.blockSignals(False)
            if hasattr(self, "project_path_edit"):
                self.project_path_edit.setText(
                    resolve_source_root((self.data.get("project_path") or "").strip() or None)
                )
        except Exception as exc:
            self.end_save_spinner(False)
            show_error(self, "Save failed", str(exc) or "Settings were written, then the form failed to reload.")
            return
        self.end_save_spinner(True)
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
            "Restart the bot after provider changes.",
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
        if self._save_busy():
            return
        self.save_btn.setEnabled(False)
        self.begin_save_spinner("Clearing secrets…")
        QTimer.singleShot(0, lambda: self._start_save_worker(None, clear=True))