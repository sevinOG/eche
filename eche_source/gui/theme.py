# gui/theme.py
# Eche visual system — purple accent matched to Eche Installer.

from __future__ import annotations

import os
import sys

APP_NAME = "Eche"
APP_VERSION = "1.1.2"
APP_TITLE = f"{APP_NAME} v{APP_VERSION}"

# Palette (installer-aligned purple + deep dark)
BG = "#0B0E14"
BG_ALT = "#14151a"
BG_ELEVATED = "#1c1d24"
BG_INPUT = "#12131a"
BORDER = "#2a2b34"
BORDER_LIGHT = "#3a3c48"
BORDER_FOCUS = "#8B5CF6"
TEXT = "#e8e8ec"
TEXT_MUTED = "#8b8c9a"
TEXT_DIM = "#6e6f7c"
TEXT_BRIGHT = "#f5f5fa"
ACCENT = "#8B5CF6"
ACCENT_HOVER = "#7C3AED"
ACCENT_SOFT = "#a78bfa"
DANGER = "#3a2228"
DANGER_BORDER = "#6b3040"
DANGER_TEXT = "#f0c0c8"
SUCCESS = "#2a4a3a"
SUCCESS_BORDER = "#3d7a5a"
SUCCESS_TEXT = "#b8e0c8"
RUN = "#2a3d2a"
RUN_BORDER = "#3d6a3d"
RUN_HOVER = "#354d35"

APP_STYLESHEET = f"""
/* ---------- Global ---------- */
* {{
    font-family: "Segoe UI", "Segoe UI Variable", sans-serif;
    font-size: 13px;
}}
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT};
}}
QToolTip {{
    background-color: {BG_ELEVATED};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 6px 8px;
}}

/* ---------- Labels ---------- */
QLabel {{
    color: {TEXT};
    background: transparent;
}}
QLabel#Title {{
    color: {TEXT_BRIGHT};
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QLabel#Subtitle {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#PanelTitle {{
    color: {ACCENT_SOFT};
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}
QLabel#CardTitle {{
    color: {TEXT_BRIGHT};
    font-size: 15px;
    font-weight: 600;
}}
QLabel#CardHint, QLabel#FieldHint {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#FieldLabel {{
    color: #c8c8d0;
    font-weight: 500;
}}
QLabel#StatusDot {{
    font-size: 12px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 10px;
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    color: {TEXT_MUTED};
}}
QLabel#StatusDot[state="online"] {{
    color: {SUCCESS_TEXT};
    border-color: {SUCCESS_BORDER};
    background-color: {SUCCESS};
}}
QLabel#StatusDot[state="offline"] {{
    color: {TEXT_MUTED};
}}
QLabel#StatusDot[state="error"] {{
    color: {DANGER_TEXT};
    border-color: {DANGER_BORDER};
    background-color: {DANGER};
}}

/* ---------- Buttons ---------- */
QPushButton {{
    background-color: #2b2d38;
    color: {TEXT};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 8px;
    padding: 8px 14px;
    min-height: 18px;
}}
QPushButton:hover {{
    background-color: #353744;
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: #22242e;
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    background-color: #1e1f26;
    border-color: {BORDER};
}}
QPushButton#primary {{
    background-color: {ACCENT};
    border-color: {ACCENT_HOVER};
    color: {TEXT_BRIGHT};
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#run {{
    background-color: {RUN};
    border-color: {RUN_BORDER};
    color: {SUCCESS_TEXT};
    font-weight: 600;
}}
QPushButton#run:hover {{
    background-color: {RUN_HOVER};
}}
QPushButton#danger {{
    background-color: {DANGER};
    border-color: {DANGER_BORDER};
    color: {DANGER_TEXT};
    font-weight: 600;
}}
QPushButton#danger:hover {{
    background-color: #4a2a32;
}}
QPushButton#ghost {{
    background-color: transparent;
    border: 1px solid {BORDER_LIGHT};
}}
QPushButton#ghost:hover {{
    background-color: #2b2d38;
    border-color: {ACCENT};
}}
QPushButton#info {{
    background-color: #252733;
    border: 1px solid {ACCENT};
    border-radius: 16px;
    color: {ACCENT_SOFT};
    font-size: 14px;
    font-weight: 700;
    padding: 0;
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
}}
QPushButton#info:hover {{
    background-color: {ACCENT};
    border-color: {ACCENT_HOVER};
    color: {TEXT_BRIGHT};
}}
QPushButton#donate {{
    background-color: transparent;
    border: 1px solid #4a3a58;
    border-radius: 8px;
    color: {TEXT_DIM};
    font-size: 11px;
    padding: 5px 10px;
    min-height: 16px;
}}
QPushButton#donate:hover {{
    color: {ACCENT_SOFT};
    border-color: {ACCENT};
    background-color: #1a1524;
}}
QPushButton#link {{
    background-color: transparent;
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT_MUTED};
    font-size: 11px;
    font-weight: 500;
    padding: 4px 10px;
    min-height: 14px;
}}
QPushButton#link:hover {{
    color: {ACCENT_SOFT};
    border-color: {ACCENT};
    background-color: #1a1524;
}}

/* ---------- Inputs ---------- */
QLineEdit, QTextEdit, QPlainTextEdit, QListWidget {{
    background-color: {BG_INPUT};
    color: {TEXT};
    border: 1px solid #2e2f3a;
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: {ACCENT};
    selection-color: {TEXT_BRIGHT};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus {{
    border: 1px solid {BORDER_FOCUS};
}}
QTextEdit, QPlainTextEdit {{
    font-family: "Consolas", "Cascadia Mono", "Courier New", monospace;
    font-size: 12px;
}}
QListWidget {{
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 8px 10px;
    border-radius: 6px;
    margin: 2px 0;
}}
QListWidget::item:selected {{
    background-color: {ACCENT};
    color: {TEXT_BRIGHT};
}}
QListWidget::item:hover {{
    background-color: #2b2d38;
}}
QListWidget#SettingsNav {{
    background: transparent;
    border: none;
    padding: 2px;
}}
QListWidget#SettingsNav::item {{
    padding: 10px 12px;
    border-radius: 8px;
    margin: 2px 0;
    color: {TEXT_MUTED};
}}
QListWidget#SettingsNav::item:selected {{
    background-color: {ACCENT};
    color: {TEXT_BRIGHT};
    font-weight: 600;
}}
QListWidget#SettingsNav::item:hover {{
    background-color: #2b2d38;
    color: {TEXT};
}}

/* ---------- Checkbox ---------- */
QCheckBox {{
    color: #b0b0bc;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #3a3c46;
    background: {BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT_HOVER};
}}

/* ---------- Splitter / Scroll / Frame ---------- */
QSplitter::handle {{
    background-color: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 2px;
}}
QSplitter::handle:vertical {{
    height: 2px;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    background: {BG};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #2e2f3a;
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT_HOVER};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {BG};
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: #2e2f3a;
    border-radius: 5px;
    min-width: 24px;
}}
QFrame#Card, QFrame#Panel {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#Toolbar {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#AccentBar {{
    background-color: {ACCENT};
    max-height: 2px;
    min-height: 2px;
    border: none;
}}
QMessageBox {{
    background-color: {BG_ELEVATED};
}}
"""


def icon_paths() -> list[str]:
    """
    Resolve brand icons from assets/icon.png (primary) and assets/icon.ico
    (Windows shell / exe). Same art in source, frozen, and onedir layouts.
    """
    candidates: list[str] = []
    bases: list[str] = []

    try:
        from core.paths import package_root, bundle_dir
        bases.extend([package_root(), bundle_dir()])
    except Exception:
        pass

    here = os.path.dirname(os.path.abspath(__file__))
    pkg = os.path.dirname(here)
    bases.append(pkg)

    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        bases.extend([exe_dir, os.path.join(exe_dir, "_internal")])
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(meipass)

    for base in bases:
        if not base:
            continue
        candidates.extend(
            [
                os.path.join(base, "assets", "icon.png"),
                os.path.join(base, "assets", "icon.ico"),
                os.path.join(base, "icon.png"),
                os.path.join(base, "icon.ico"),
            ]
        )

    out: list[str] = []
    seen: set[str] = set()
    for p in candidates:
        if p and os.path.isfile(p) and p not in seen:
            seen.add(p)
            out.append(p)
    # Prefer PNG first for crisp Qt title bars / taskbar
    out.sort(key=lambda p: (0 if p.lower().endswith(".png") else 1, p))
    return out


def brand_icon():
    """QIcon built from assets/icon.png (+ ico fallback)."""
    from PyQt6.QtGui import QIcon

    icon = QIcon()
    for p in icon_paths():
        icon.addFile(p)
    return icon


def icon_path() -> str | None:
    paths = icon_paths()
    return paths[0] if paths else None


def apply_theme(app) -> None:
    """Apply global stylesheet + window icon to QApplication."""
    # Windows taskbar grouping uses AppUserModelID; set before first window
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Eche.App.1"
            )
        except Exception:
            pass

    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    try:
        icon = brand_icon()
        if not icon.isNull():
            app.setWindowIcon(icon)
    except Exception:
        pass
