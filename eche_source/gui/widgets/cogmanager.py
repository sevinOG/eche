# gui/widgets/cogmanager.py
# Browse extensions, toggle load/unload, smart drop-routing for user .py files.

from __future__ import annotations

import ast
import os
import re
import shutil

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
    QFrame,
    QCheckBox,
    QAbstractItemView,
    QFileDialog,
    QScrollArea,
    QSizePolicy,
)

from gui.theme import APP_NAME

try:
    from core.paths import user_dir, bundle_dir, is_frozen as _is_frozen, ensure_user_layout
except Exception:
    def _is_frozen():
        return False

    def user_dir():
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def bundle_dir():
        return user_dir()

    def ensure_user_layout():
        root = user_dir()
        os.makedirs(os.path.join(root, "cogs"), exist_ok=True)
        return root


PROJECT_ROOT = ensure_user_layout() if callable(ensure_user_layout) else user_dir()

# Bundle / source tree cogs (read-only when frozen)
_BUNDLE_COGS = os.path.join(bundle_dir() if _is_frozen() else PROJECT_ROOT, "cogs")
if not os.path.isdir(_BUNDLE_COGS):
    _BUNDLE_COGS = os.path.join(PROJECT_ROOT, "cogs")

# Writable drop target (always under user data root so frozen installs work)
USER_COGS = os.path.join(PROJECT_ROOT, "cogs")
GAMES_FOLDER = os.path.join(USER_COGS, "games")
UTILS_FOLDER = os.path.join(USER_COGS, "utils")

_SKIP_FILES = {
    "registry.py",
    "music_player.py",
    "music_queue_storage.py",
    "remind_handler.py",
    "holdem.py",
    "showdown.py",
    "hbuilder.py",
    "entertainer_manager.py",
    "law_manager.py",
    "_core.py",
}

COG_FORMAT_HELP = """
# Adding your own cogs

Drop any `.py` file onto the drop zone. Eche inspects the source and
places it automatically:

| Detected as | Destination |
|-------------|-------------|
| **Game** | `cogs/games/yourfile.py` |
| **Utility cog** | `cogs/utils/yourfile.py` |
| Unclear | Asks / defaults to utils |

Restart or toggle the cog after adding. Games register on import via the games registry.

---

## Game format (`cogs/games/`)

Games are **not** full discord.py Cogs. They register into the bet/economy game board.

```python
from cogs.games._core import register_game

class MyGame:
    description = "One-line blurb for the bet UI."
    supports_odds = False   # or True + ODDS_OPTIONS

    @staticmethod
    async def usage(ctx):
        await ctx.send("How to play…")

    @staticmethod
    async def start(ctx, odds, betvalue, starting_balance,
                    load_callback, save_callback, message):
        # Run the game, update balances via callbacks
        ...

register_game("My Game", MyGame)
```

**Required**
- Call `register_game("Display Name", Class)` at module level
- Class with `start(...)` (async static or class method)
- Prefer `description` for the UI

**Optional**
- `supports_odds = True` and `ODDS_OPTIONS = [("Label", 1), ...]`
- `usage(ctx)` help embed

Do **not** put `async def setup(bot)` in game files — those are utility cogs.

---

## Utility cog format (`cogs/utils/` or top-level)

Standard discord.py extension:

```python
from discord.ext import commands

class MyUtil(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def hello(self, ctx):
        await ctx.send("hi")

async def setup(bot):
    await bot.add_cog(MyUtil(bot))
```

**Required**
- `async def setup(bot):` entry point
- Usually a `commands.Cog` subclass

**Tips**
- Prefix commands use the bot’s prefix (default `?`)
- Avoid absolute machine paths; use `core.paths.user_dir()` for files
- Syntax errors show a **traceback** window after load fails

---

## After dropping

1. File is copied into the right folder under your app data / project
2. Cog list refreshes
3. With the bot running, tick the checkbox to **LOAD_COG**
4. If import fails, check Logs / the traceback dialog

Frozen builds write to the install folder’s `cogs/` (next to Eche.exe), not inside `_internal`.
"""


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def classify_py_module(path: str) -> str:
    """
    Return 'game' | 'util' based on source heuristics.
    """
    src = _read_text(path)
    if not src.strip():
        return "util"

    # Strong game signals
    game_score = 0
    util_score = 0

    if re.search(r"register_game\s*\(", src):
        game_score += 5
    if "cogs.games._core" in src or "games._core" in src:
        game_score += 3
    if re.search(r"async def start\s*\(", src) and re.search(
        r"(betvalue|starting_balance|odds)", src
    ):
        game_score += 3
    if re.search(r"supports_odds\s*=", src):
        game_score += 2
    if re.search(r"ODDS_OPTIONS\s*=", src):
        game_score += 1

    # Strong util / cog signals
    if re.search(r"async def setup\s*\(\s*bot", src):
        util_score += 5
    if re.search(r"commands\.Cog", src) or re.search(r"\(commands\.Cog\)", src):
        util_score += 3
    if re.search(r"@commands\.(command|group|hybrid_command)", src):
        util_score += 2
    if "discord.ext" in src and "commands" in src:
        util_score += 1

    # AST pass for class + register_game call
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = ""
                if isinstance(fn, ast.Name):
                    name = fn.id
                elif isinstance(fn, ast.Attribute):
                    name = fn.attr
                if name == "register_game":
                    game_score += 4
                if name == "add_cog":
                    util_score += 2
    except SyntaxError:
        # Still route; load will surface traceback later
        pass

    if game_score > util_score and game_score >= 3:
        return "game"
    if util_score >= 3:
        return "util"
    if game_score > 0 and util_score == 0:
        return "game"
    return "util"


def _writable_cogs_root() -> str:
    """Prefer project/user cogs; create games + utils."""
    root = USER_COGS
    # When running from source and bundle == project, use that
    if not _is_frozen() and os.path.isdir(_BUNDLE_COGS):
        root = _BUNDLE_COGS
    os.makedirs(os.path.join(root, "games"), exist_ok=True)
    os.makedirs(os.path.join(root, "utils"), exist_ok=True)
    # Ensure utils is a package
    init_u = os.path.join(root, "utils", "__init__.py")
    if not os.path.isfile(init_u):
        with open(init_u, "w", encoding="utf-8") as f:
            f.write("# User-dropped utility cogs\n")
    init_g = os.path.join(root, "games", "__init__.py")
    if not os.path.isfile(init_g):
        with open(init_g, "w", encoding="utf-8") as f:
            f.write("# Games package\n")
    return root


class CogDropList(QListWidget):
    def __init__(self, on_drop_files, parent=None):
        super().__init__(parent)
        self._on_drop_files = on_drop_files
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setMinimumHeight(64)
        self.setMaximumHeight(88)
        item = QListWidgetItem("Drop .py files here — games & utils auto-sorted")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.addItem(item)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and path.lower().endswith(".py"):
                paths.append(path)
        if paths:
            self._on_drop_files(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class CogManagerWindow(QMainWindow):
    def __init__(self, bot_process=None, main_window=None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Cog Browser")
        self.resize(560, 680)
        self.setMinimumSize(420, 480)
        self.bot_process = bot_process
        self.main_window = main_window
        self._loaded: set[str] = set()
        self._rows: dict[str, QCheckBox] = {}
        self._badges: dict[str, QLabel] = {}

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        head_row = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Cog Browser")
        title.setObjectName("Title")
        titles.addWidget(title)
        hint = QLabel(
            "Toggle load/unload on the running bot. Drop .py files below — "
            "they are classified as games or utils and placed automatically."
        )
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        titles.addWidget(hint)
        head_row.addLayout(titles, stretch=1)

        info_btn = QPushButton("ℹ")
        info_btn.setObjectName("info")
        info_btn.setFixedSize(32, 32)
        info_btn.setToolTip("How to format games & utility cogs")
        info_btn.clicked.connect(self._show_format_help)
        head_row.addWidget(info_btn, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head_row)

        card = QFrame()
        card.setObjectName("Panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(8)

        head = QLabel("EXTENSIONS")
        head.setObjectName("PanelTitle")
        card_layout.addWidget(head)

        self.status_label = QLabel("Scan disk or start bot for live status…")
        self.status_label.setObjectName("FieldHint")
        self.status_label.setWordWrap(True)
        card_layout.addWidget(self.status_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(4)
        self.rows_layout.addStretch(1)
        self.scroll.setWidget(self.rows_host)
        card_layout.addWidget(self.scroll, stretch=1)

        self.drop_list = CogDropList(self._on_drop_files)
        card_layout.addWidget(self.drop_list)
        layout.addWidget(card, stretch=1)

        row = QHBoxLayout()
        add_btn = QPushButton("Add .py…")
        add_btn.setObjectName("ghost")
        add_btn.clicked.connect(self._browse_files)
        row.addWidget(add_btn)
        row.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("ghost")
        refresh_btn.clicked.connect(self.request_refresh)
        row.addWidget(refresh_btn)
        layout.addLayout(row)

        self.rebuild_from_disk(set())
        self.request_refresh()

    def _show_format_help(self):
        from gui.widgets.dialogs import show_info
        show_info(self, "Game & utility cog format", COG_FORMAT_HELP)

    def _cogs_scan_roots(self) -> list[str]:
        roots = []
        for r in (_BUNDLE_COGS, USER_COGS):
            if r and os.path.isdir(r) and r not in roots:
                roots.append(r)
        return roots

    def _discover_modules(self):
        modules = []
        for cogs_root in self._cogs_scan_roots():
            # Module path relative to parent of cogs/
            base = os.path.dirname(cogs_root)
            for root, _, files in os.walk(cogs_root):
                for file in files:
                    if not file.endswith(".py") or file == "__init__.py":
                        continue
                    if file in _SKIP_FILES or file.startswith("_"):
                        continue
                    full_path = os.path.join(root, file)
                    rel = os.path.relpath(full_path, base)
                    modules.append(rel.replace(os.sep, ".")[:-3])
        return sorted(set(modules))

    @staticmethod
    def _short_module(ext: str) -> str:
        ext = (ext or "").strip()
        if ext.startswith("eche_ecosystem."):
            return ext[len("eche_ecosystem.") :]
        return ext

    def _is_loaded(self, display, loaded):
        for ext in loaded:
            short = self._short_module(ext)
            if short == display or ext == display:
                return True
        return False

    def rebuild_from_disk(self, loaded):
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._rows.clear()
        self._badges.clear()

        modules = self._discover_modules()
        for ext in loaded:
            short = self._short_module(ext)
            if short not in modules:
                modules.append(short)
        modules = sorted(set(modules))

        for display in modules:
            is_on = self._is_loaded(display, loaded)
            row = QWidget()
            row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            hl = QHBoxLayout(row)
            hl.setContentsMargins(4, 2, 4, 2)

            cb = QCheckBox(display)
            cb.blockSignals(True)
            cb.setChecked(is_on)
            cb.blockSignals(False)
            cb.toggled.connect(lambda checked, m=display: self._on_toggle(m, checked))
            hl.addWidget(cb, stretch=1)

            badge = QLabel("ON" if is_on else "off")
            badge.setObjectName("FieldHint")
            hl.addWidget(badge)

            self._rows[display] = cb
            self._badges[display] = badge
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)

        self.status_label.setText(f"{len(modules)} modules · {len(loaded)} loaded")

    def apply_cog_list(self, loaded, extra=None):
        self._loaded = set(loaded or [])
        extra = extra or {}
        bits = [f"{len(self._loaded)} loaded"]
        if extra.get("music_playing"):
            bits.append("music playing")
        if extra.get("other_busy"):
            bits.append("busy")
        self.status_label.setText(" · ".join(bits))
        self.rebuild_from_disk(self._loaded)

    def request_refresh(self):
        self._send("LIST_COGS")
        self.rebuild_from_disk(self._loaded)

    def set_bot_process(self, bot_process):
        self.bot_process = bot_process
        self.request_refresh()

    def _on_toggle(self, display_module, checked):
        if not self.bot_process or self.bot_process.poll() is not None:
            self.status_label.setText("Bot not running — toggle ignored")
            cb = self._rows.get(display_module)
            if cb:
                cb.blockSignals(True)
                cb.setChecked(not checked)
                cb.blockSignals(False)
            return

        # Extension names are package-relative: cogs.foo or core.bar
        if display_module.startswith(("cogs.", "core.")):
            full = display_module
        elif display_module.startswith("eche_ecosystem."):
            full = display_module[len("eche_ecosystem.") :]
        else:
            full = "cogs." + display_module

        if checked:
            self._send(f"LOAD_COG {full}")
        else:
            self._send(f"UNLOAD_COG {full}")

        badge = self._badges.get(display_module)
        if badge:
            badge.setText("ON" if checked else "off")

    def _send(self, line):
        if not self.bot_process or self.bot_process.poll() is not None:
            return
        try:
            self.bot_process.stdin.write(line + "\n")
            self.bot_process.stdin.flush()
        except Exception as e:
            print("Failed to send bridge command:", e)
            if self.main_window:
                self.main_window.append_log(f"[cogs] send failed: {e}")

    def _browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add cog or game module(s)", "", "Python files (*.py)"
        )
        if paths:
            self._on_drop_files(paths)

    def _on_drop_files(self, paths):
        from gui.widgets.dialogs import show_error, present_failure

        cogs_root = _writable_cogs_root()
        games_dir = os.path.join(cogs_root, "games")
        utils_dir = os.path.join(cogs_root, "utils")
        os.makedirs(games_dir, exist_ok=True)
        os.makedirs(utils_dir, exist_ok=True)

        placed: list[str] = []
        errors: list[str] = []

        for src in paths:
            if not src.lower().endswith(".py"):
                continue
            name = os.path.basename(src)
            if name.startswith("_") or name in _SKIP_FILES:
                errors.append(f"{name}: reserved / skipped filename")
                continue

            kind = classify_py_module(src)
            dest_dir = games_dir if kind == "game" else utils_dir
            dest = os.path.join(dest_dir, name)

            # Syntax pre-check so users get a clear error before load
            src_text = _read_text(src)
            try:
                ast.parse(src_text)
            except SyntaxError as se:
                errors.append(f"{name}: syntax error line {se.lineno}: {se.msg}")
                if self.main_window:
                    import traceback
                    present_failure(
                        self,
                        f"SyntaxError in {name}\n  line {se.lineno}: {se.msg}\n"
                        f"File \"{src}\", line {se.lineno}\nSyntaxError: {se.msg}",
                        log_fn=self.main_window.append_log,
                        default_title="Invalid Python file",
                    )
                continue

            try:
                shutil.copy2(src, dest)
                rel = os.path.relpath(dest, cogs_root)
                placed.append(f"{name} → cogs/{rel.replace(os.sep, '/')} ({kind})")
            except Exception as e:
                errors.append(f"{name}: {e}")

        if placed:
            msg = "Placed: " + "; ".join(placed)
            self.status_label.setText(msg)
            if self.main_window:
                self.main_window.append_log("[cogs] " + msg)
            self.rebuild_from_disk(self._loaded)
            self._send("LIST_COGS")

        if errors and not placed:
            show_error(
                self,
                "Could not add files",
                "None of the dropped files could be installed.",
                hint="Check the ℹ format guide. Syntax errors open a traceback-style dialog.",
                details="\n".join(errors),
            )
        elif errors:
            show_error(
                self,
                "Some files failed",
                "Other files were placed successfully.",
                details="\n".join(errors),
            )
