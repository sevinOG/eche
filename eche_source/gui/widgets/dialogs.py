# gui/widgets/dialogs.py
# Themed error / traceback / info dialogs that match Eche's dark UI.

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QFrame,
    QSizePolicy,
)

from gui.theme import APP_NAME


class _BaseDialog(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — {title}")
        self.setMinimumSize(420, 280)
        self.setModal(True)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(18, 18, 18, 18)
        self._root.setSpacing(12)

        self._title = QLabel(title)
        self._title.setObjectName("Title")
        self._title.setWordWrap(True)
        self._root.addWidget(self._title)

        self._subtitle = QLabel("")
        self._subtitle.setObjectName("Subtitle")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(False)
        self._root.addWidget(self._subtitle)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def _footer(self, primary_label: str = "OK") -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton(primary_label)
        ok.setObjectName("primary")
        ok.setMinimumWidth(100)
        ok.setMinimumHeight(36)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        return row


class ErrorDialog(_BaseDialog):
    """
    User-facing error: short title, plain-language explanation, optional hint.
    Not for raw Python dumps — use TracebackDialog for those.
    """

    def __init__(
        self,
        title: str,
        message: str,
        *,
        hint: str = "",
        details: str = "",
        parent=None,
    ):
        super().__init__(title, parent)
        self.resize(480, 320)

        card = QFrame()
        card.setObjectName("Card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)

        body = QLabel(message)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(body)

        if hint:
            h = QLabel(hint)
            h.setObjectName("FieldHint")
            h.setWordWrap(True)
            cl.addWidget(h)

        if details:
            det = QTextEdit()
            det.setReadOnly(True)
            det.setPlainText(details)
            det.setMaximumHeight(120)
            cl.addWidget(det)

        self._root.addWidget(card, stretch=1)
        self._root.addLayout(self._footer("Got it"))


class TracebackDialog(_BaseDialog):
    """
    Full traceback for code-level failures (edits, bad cogs, import errors).
    Always log the same text in the main Logs panel when showing this.
    """

    def __init__(
        self,
        title: str,
        traceback_text: str,
        *,
        summary: str = "",
        parent=None,
    ):
        super().__init__(title or "Code error", parent)
        self.resize(640, 480)
        self.setMinimumSize(520, 360)

        if summary:
            self.set_subtitle(summary)
        else:
            self.set_subtitle(
                "This looks like a Python error from code (a cog, provider edit, "
                "or app module). The full traceback is below — also written to Logs."
            )

        card = QFrame()
        card.setObjectName("Panel")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 10, 10, 10)
        head = QLabel("TRACEBACK")
        head.setObjectName("PanelTitle")
        cl.addWidget(head)
        self._tb = QTextEdit()
        self._tb.setReadOnly(True)
        self._tb.setPlainText(traceback_text or "(empty)")
        self._tb.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        cl.addWidget(self._tb, stretch=1)
        self._root.addWidget(card, stretch=1)

        row = QHBoxLayout()
        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("ghost")
        copy_btn.clicked.connect(self._copy)
        row.addWidget(copy_btn)
        row.addStretch()
        ok = QPushButton("Close")
        ok.setObjectName("primary")
        ok.setMinimumWidth(100)
        ok.setMinimumHeight(36)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        self._root.addLayout(row)

    def _copy(self):
        self._tb.selectAll()
        self._tb.copy()


class InfoDialog(_BaseDialog):
    """Educational / help content (markdown-ish plain text)."""

    def __init__(self, title: str, body: str, parent=None):
        super().__init__(title, parent)
        self.resize(520, 440)
        self.set_subtitle("How this works")

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        # Simple markdown: treat double newlines as paragraphs
        browser.setMarkdown(body.replace("\n", "  \n"))
        browser.setMinimumHeight(260)
        self._root.addWidget(browser, stretch=1)
        self._root.addLayout(self._footer("Close"))


# ---------------------------------------------------------------------------
# Helpers: classify messages & show the right dialog
# ---------------------------------------------------------------------------

# Config / setup issues users can fix without reading Python
_CONFIG_HINTS: list[tuple[str, str, str, str]] = [
    (
        "HOME_SERVER_ID",
        "Home Server ID is missing",
        "The bot needs a Discord guild (server) ID for memory and context. "
        "Open Settings → Discord and paste your Home Server ID.\n\n"
        "Tip: Discord → Settings → Advanced → Developer Mode, then right-click "
        "your server icon → Copy Server ID.",
        "Save Settings, then press Run Bot again.",
    ),
    (
        "DISCORD_TOKEN",
        "Discord token is missing",
        "No bot token was found in secure storage or the environment.\n\n"
        "Open Settings → Discord and paste the token from the Discord Developer Portal.",
        "Leave the field blank later to keep the saved secret.",
    ),
    (
        "discord token",
        "Discord token is missing",
        "No bot token was found. Set it under Settings → Discord.",
        "Save Settings, then press Run Bot again.",
    ),
    (
        "GROQ_API_KEY",
        "Provider API key is missing",
        "Chat needs a provider API key under Settings → AI & Model "
        "(default free setup: console.groq.com). The bot can still run games "
        "and economy without it.",
        "Optional for commands; required for AI conversation.",
    ),
    (
        "install.ps1 not found",
        "Package folder not found",
        "The rebuild path does not look like the portable Eche package.",
        "Use Rebuild → Re-detect, or Browse to the folder that has install.bat.",
    ),
    (
        "No .venv found",
        "Virtual environment missing",
        "Updates rebuild with the project’s .venv Python, which was not found.",
        "Create a venv in the source folder and install dependencies first.",
    ),
]


def looks_like_traceback(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    return (
        "Traceback (most recent call last):" in t
        or t.startswith("Traceback (most recent call last)")
        or ("File \"" in t and "line " in t and ("Error:" in t or "Exception:" in t))
    )


def classify_error(text: str) -> str:
    """Return 'traceback' | 'config' | 'generic'."""
    if looks_like_traceback(text):
        return "traceback"
    low = (text or "").lower()
    for needle, *_ in _CONFIG_HINTS:
        if needle.lower() in low:
            return "config"
    if "runtimeerror:" in low or "error:" in low:
        # RuntimeError from missing config often has no traceback in child
        for needle, *_ in _CONFIG_HINTS:
            if needle.lower() in low:
                return "config"
    return "generic"


def match_config_hint(text: str) -> tuple[str, str, str] | None:
    low = (text or "").lower()
    for needle, title, message, hint in _CONFIG_HINTS:
        if needle.lower() in low:
            return title, message, hint
    return None


def show_error(
    parent,
    title: str,
    message: str,
    *,
    hint: str = "",
    details: str = "",
) -> None:
    ErrorDialog(title, message, hint=hint, details=details, parent=parent).exec()


def show_traceback(
    parent,
    traceback_text: str,
    *,
    title: str = "Code error",
    summary: str = "",
    log_fn=None,
) -> None:
    if log_fn:
        try:
            log_fn(f"[TRACEBACK]\n{traceback_text}")
        except Exception:
            pass
    TracebackDialog(title, traceback_text, summary=summary, parent=parent).exec()


def show_info(parent, title: str, body: str) -> None:
    InfoDialog(title, body, parent=parent).exec()


def present_failure(parent, text: str, *, log_fn=None, default_title: str = "Something went wrong") -> None:
    """
    Route a failure string to a styled config error or a traceback window.
    Always logs when log_fn is provided.
    """
    text = (text or "").strip()
    if not text:
        return
    if log_fn:
        try:
            log_fn(f"[ERROR] {text}")
        except Exception:
            pass

    kind = classify_error(text)
    if kind == "traceback":
        show_traceback(parent, text, log_fn=None)  # already logged
        return

    matched = match_config_hint(text)
    if matched:
        title, message, hint = matched
        # Append original line as details if different
        details = text if text not in message else ""
        show_error(parent, title, message, hint=hint, details=details)
        return

    show_error(parent, default_title, text, hint="See Logs for more context.")
