# gui/widgets/settingswindow.py
# Multi-page settings: Discord | AI | Media | Memory | Economy | Security | Updates
# Cloud vs Ollama: hide unused fields; separate cloud_model / ollama_model; mode-aware save.

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
)

from gui.theme import APP_NAME, APP_VERSION
from gui.widgets.providerwindow import ProviderWindow
from gui.widgets.personalitywindow import PersonalityWindow
from gui.widgets.loading import LoadingIndicator
from gui.widgets.dialogs import show_error, show_info, present_failure
from gui.widgets.settings_help import FIELD_HELP
from gui.widgets.settings_workers import UpdateWorker

UNSPLASH_DEV_URL = "https://unsplash.com/developers"
UNSPLASH_APPS_URL = "https://unsplash.com/oauth/applications"

_CLOUD_DEFAULT = "llama-3.3-70b-versatile"
_OLLAMA_DEFAULT = "llama3"
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
        self._cloud_only: list = []
        self._ollama_only: list = []
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
        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("primary")
        save_btn.setMinimumHeight(38)
        save_btn.setMinimumWidth(150)
        save_btn.clicked.connect(self.save)
        footer.addWidget(save_btn)
        root.addLayout(footer)

        self._populate_fields()
        self.nav.setCurrentRow(0)

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

        self._cloud_only = []
        self._ollama_only = []

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
            "Cloud = Groq (default free tier). Ollama = models on this PC. "
            "Only fields for the selected mode are shown."
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
        ol_l.setSpacing(6)
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
            "Provider = who runs the AI. Unused fields are hidden when you switch backend.",
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
            "One-tap rebuild from **eche_source** (not this portable folder). "
            "Runs BUILD.bat → refreshes sibling eche\\Eche.exe + icons."
        )
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        row.addWidget(hint, stretch=1)
        row.addWidget(self._info_button("updates"))
        body.addLayout(row)
        path_label_row = QHBoxLayout()
        path_lab = QLabel("eche_source path")
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
        self.project_path_edit.setPlaceholderText("…/eche_source (auto-detected)")
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
        self.check_btn.clicked.connect(self.check_for_updates)
        upd_row.addWidget(self.check_btn)
        self.update_loader = LoadingIndicator()
        self.update_loader.set_state("offline")
        upd_row.addWidget(self.update_loader)
        upd_row.addStretch()
        body.addLayout(upd_row)
        layout.addWidget(self._card(
            "One-tap rebuild",
            "Source: eche_source\\BUILD.bat → publishes to eche\\",
            body,
        ))
        layout.addStretch(1)
        return page

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

    def _apply_provider_ui(self):
        if not hasattr(self, "provider_combo"):
            return
        backend = self.provider_combo.currentData() or "cloud"
        is_ollama = backend == "ollama"
        for w in self._cloud_only:
            w.setVisible(not is_ollama)
        for w in self._ollama_only:
            w.setVisible(is_ollama)
        if is_ollama:
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
            if key == "cloud_model":
                continue
            edit.setText(str(self.data.get(key) or ""))

        cloud_m = (
            self.data.get("cloud_model")
            or (self.data.get("groq_model") if (self.data.get("provider_backend") or "cloud") != "ollama" else "")
            or _CLOUD_DEFAULT
        ).strip()
        if "cloud_model" in self._public_edits:
            self._public_edits["cloud_model"].setText(cloud_m or _CLOUD_DEFAULT)

        self._pending_ollama_model = (
            self.data.get("ollama_model") or _OLLAMA_DEFAULT
        ).strip() or _OLLAMA_DEFAULT

        if hasattr(self, "provider_combo"):
            backend = (self.data.get("provider_backend") or "cloud").strip().lower()
            if backend not in ("cloud", "ollama"):
                backend = "cloud"
            idx = self.provider_combo.findData(backend)
            self.provider_combo.blockSignals(True)
            self.provider_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.provider_combo.blockSignals(False)

        self._apply_provider_ui()

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

    def flash_save_spinner(self, ms: int = 700):
        if not hasattr(self, "save_loader"):
            return
        self.save_loader.set_busy(True, "Saving…")
        QTimer.singleShot(ms, lambda: self.save_loader.set_state("offline"))

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
            if key in ("cloud_model", "groq_model", "ollama_model"):
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

        if backend == "ollama":
            payload["groq_model"] = payload["ollama_model"]
        else:
            payload["groq_model"] = payload["cloud_model"]

        if hasattr(self, "project_path_edit"):
            payload["project_path"] = self.project_path_edit.text().strip()
        return payload

    def check_for_updates(self):
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

        project_path = source_root(typed or None) or source_root(None) or ""
        if project_path:
            self.project_path_edit.setText(project_path)

        if not project_path or not is_buildable_source(project_path):
            show_error(
                self,
                "eche_source not found",
                "In-app rebuild needs the **eche_source** folder with BUILD.bat, core/, gui/, .venv.",
                hint="Browse to …\\workspace\\eche_source",
                details=typed or project_path or "(empty)",
            )
            return

        if not has_build_venv(project_path):
            show_error(
                self,
                "Python venv missing in source",
                f"Found source at:\n{project_path}\n\n"
                "Run: python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt",
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
                self, "BUILD.bat not found",
                f"Expected BUILD.bat inside:\n{project_path}",
                details=project_path,
            )
            return

        payload = self._collect_payload()
        payload["project_path"] = project_path
        save_settings(payload)

        reply = QMessageBox.question(
            self,
            "Rebuild portable app?",
            "This runs one command:\n\n"
            f"  {bat_path}\n\n"
            "It rebuilds Eche.exe into the sibling eche\\ folder "
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

    def save(self):
        self.flash_save_spinner(900)
        payload = self._collect_payload()
        if not (payload.get("provider_backend") or "").strip():
            payload["provider_backend"] = "cloud"
        save_settings(payload)
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
        from core.secrets import clear_secrets as wipe_secrets
        wipe_secrets(PROJECT_ROOT)
        for edit in self._secret_edits.values():
            edit.clear()
        self.data = load_settings()
        QMessageBox.information(self, "Cleared", "Stored secrets were removed.")