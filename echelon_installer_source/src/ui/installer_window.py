"""Main installer GUI window - Echelon themed wizard (spacious layout)."""
import os
import sys
import platform
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QCheckBox, QProgressBar, QTextEdit, QFrame,
    QStackedWidget, QRadioButton, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer

from .theme import STYLESHEET, ECHELON_PALETTE, VERSION
from .widgets import StepIndicator, Card
from ..core.builder import (
    find_echelon_source,
    find_portable_app,
    find_source_tree,
    describe_trees,
    get_default_install_dir,
    get_default_source_recover_dir,
    get_program_files_dir,
    workspace_root,
)
from ..core.installer import Installer, InstallOptions
from ..core.uninstaller import Uninstaller
from ..core.registry import is_installed


def _asset_icon_paths() -> list[str]:
    """
    Resolve assets/icon.png (primary brand) + icon.ico.
    Works for source runs and frozen PyInstaller (sys._MEIPASS).
    """
    bases: list[Path] = []
    here = Path(__file__).resolve()
    # src/ui/installer_window.py -> package root
    try:
        bases.append(here.parents[2])
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        bases.extend([exe_dir, exe_dir / "_internal"])
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(Path(meipass))
    # also cwd (portable final/ layout)
    bases.append(Path.cwd())

    out: list[str] = []
    seen: set[str] = set()
    for base in bases:
        for rel in (
            ("assets", "icon.png"),
            ("assets", "icon.ico"),
            ("icon.png",),
            ("icon.ico",),
        ):
            p = base.joinpath(*rel)
            try:
                key = str(p.resolve()) if p.is_file() else ""
            except Exception:
                key = ""
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    out.sort(key=lambda p: (0 if p.lower().endswith(".png") else 1, p))
    return out


def brand_icon():
    """QIcon for window/taskbar — load PNG + ICO at several sizes."""
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QIcon

    icon = QIcon()
    paths = _asset_icon_paths()
    sizes = (16, 24, 32, 48, 64, 128, 256)
    for p in paths:
        # full file
        icon.addFile(p)
        # explicit sizes help Windows shell pick a crisp glyph
        for s in sizes:
            icon.addFile(p, QSize(s, s))
    return icon


def _apply_window_icon(widget) -> None:
    try:
        icon = brand_icon()
        if not icon.isNull():
            widget.setWindowIcon(icon)
    except Exception:
        pass


class InstallWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(bool)

    def __init__(self, options: InstallOptions):
        super().__init__()
        self.options = options
        self.installer = Installer(
            log_callback=lambda m: self.log_signal.emit(m),
            progress_callback=lambda p, m: self.progress_signal.emit(p, m)
        )

    def run(self):
        ok = self.installer.install(self.options)
        self.finished_signal.emit(ok)


class UninstallWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(bool)

    def __init__(self, install_dir: str, remove_dir: bool = True):
        super().__init__()
        self.install_dir = install_dir
        self.remove_dir = remove_dir
        self.uninstaller = Uninstaller(
            log_callback=lambda m: self.log_signal.emit(m),
            progress_callback=lambda p, m: self.progress_signal.emit(p, m)
        )

    def run(self):
        ok = self.uninstaller.uninstall(self.install_dir, self.remove_dir)
        self.finished_signal.emit(ok)


class EchelonInstallerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Echelon Installer v{VERSION}")
        self.setMinimumSize(920, 700)
        self.resize(980, 760)
        self.setStyleSheet(STYLESHEET)

        _apply_window_icon(self)

        self.steps = ["Welcome", "Location", "Options", "Install", "Finish"]
        self.current_step = 0
        self.install_success = False
        self.source_mode = "github"  # default: public hub
        self.launch_exe: str | None = None

        self.source_path, self.source_type = find_echelon_source()
        self.default_install_dir = get_default_install_dir()
        self.install_dir = str(self.default_install_dir)
        self.trees = describe_trees()

        self.existing_installed, self.existing_dir = is_installed()
        # Do not clobber a detected build path with old install dir on welcome —
        # only prefill install target when reinstalling.
        if self.existing_installed and self.existing_dir:
            self.install_dir = self.existing_dir

        # Dev source tree (echelon_source) vs portable app (echelon)
        src = find_source_tree()
        self.default_repo = src if src is not None else (workspace_root() / "echelon_source")
        portable_exe, _ = find_portable_app()
        self.default_portable = (
            Path(portable_exe).parent
            if portable_exe and Path(portable_exe).is_file()
            else workspace_root() / "echelon"
        )
        self.default_recover_dir = get_default_source_recover_dir()

        self._build_ui()
        # Apply GitHub-default path hints after widgets exist
        try:
            self._on_source_mode_toggled()
        except Exception:
            pass
        self._update_step()

    def _wrap_scroll(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        return scroll

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Header (title only) ---
        header = QFrame()
        header.setStyleSheet(
            f"background-color: {ECHELON_PALETTE['surface']}; "
            f"border-bottom: 1px solid {ECHELON_PALETTE['border']};"
        )
        header.setMinimumHeight(72)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(28, 16, 28, 16)
        header_layout.setSpacing(16)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title = QLabel("ECHELON")
        title.setObjectName("TitleLabel")
        title.setStyleSheet(
            f"background: transparent; font-size: 22px; font-weight: 800; "
            f"letter-spacing: 4px; color: {ECHELON_PALETTE['text']};"
        )
        subtitle = QLabel(f"INSTALLATION PROTOCOL  ·  v{VERSION}")
        subtitle.setObjectName("SubtitleLabel")
        subtitle.setStyleSheet(
            f"background: transparent; font-size: 11px; "
            f"color: {ECHELON_PALETTE['text_muted']}; letter-spacing: 1.5px;"
        )
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box)
        header_layout.addStretch()
        main_layout.addWidget(header)

        # Purple accent line
        glow = QFrame()
        glow.setFixedHeight(2)
        glow.setStyleSheet(f"background-color: {ECHELON_PALETTE['accent']};")
        main_layout.addWidget(glow)

        # --- Step strip (own row so header is not cramped) ---
        step_bar = QFrame()
        step_bar.setStyleSheet(
            f"background-color: {ECHELON_PALETTE['bg_alt']}; "
            f"border-bottom: 1px solid {ECHELON_PALETTE['border']};"
        )
        step_bar.setMinimumHeight(52)
        step_layout = QHBoxLayout(step_bar)
        step_layout.setContentsMargins(28, 12, 28, 12)
        self.step_indicator = StepIndicator(self.steps)
        self.step_indicator.setStyleSheet("background: transparent;")
        step_layout.addWidget(self.step_indicator)
        main_layout.addWidget(step_bar)

        # --- Pages (scrollable) ---
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, 1)

        self.stack.addWidget(self._wrap_scroll(self._build_welcome_page()))
        self.stack.addWidget(self._wrap_scroll(self._build_location_page()))
        self.stack.addWidget(self._wrap_scroll(self._build_options_page()))
        self.stack.addWidget(self._wrap_scroll(self._build_install_page()))
        self.stack.addWidget(self._wrap_scroll(self._build_finish_page()))

        # --- Nav footer ---
        nav = QFrame()
        nav.setStyleSheet(
            f"background-color: {ECHELON_PALETTE['surface']}; "
            f"border-top: 1px solid {ECHELON_PALETTE['border']};"
        )
        nav.setMinimumHeight(76)
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(28, 14, 28, 14)
        nav_layout.setSpacing(12)

        self.btn_back = QPushButton("Back")
        self.btn_back.setObjectName("SecondaryButton")
        self.btn_back.setFixedHeight(42)
        self.btn_back.setMinimumWidth(100)
        self.btn_back.clicked.connect(self._on_back)

        self.btn_next = QPushButton("Next")
        self.btn_next.setObjectName("PrimaryButton")
        self.btn_next.setFixedHeight(42)
        self.btn_next.setMinimumWidth(150)
        self.btn_next.clicked.connect(self._on_next)

        self.btn_uninstall = QPushButton("Uninstall")
        self.btn_uninstall.setObjectName("DangerButton")
        self.btn_uninstall.setFixedHeight(42)
        self.btn_uninstall.setMinimumWidth(110)
        self.btn_uninstall.clicked.connect(self._on_uninstall_clicked)
        self.btn_uninstall.hide()

        nav_layout.addWidget(self.btn_back)
        nav_layout.addStretch()
        nav_layout.addWidget(self.btn_uninstall)
        nav_layout.addWidget(self.btn_next)
        main_layout.addWidget(nav)

    def _build_welcome_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(18)

        brand = QLabel("ECHELON")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setStyleSheet(
            f"background: transparent; font-size: 28px; font-weight: 800; "
            f"letter-spacing: 8px; color: {ECHELON_PALETTE['accent']};"
        )
        layout.addWidget(brand)

        tag = QLabel("OPEN SOURCE  ·  BOT INFRASTRUCTURE  ·  LEARNING UTILITY")
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag.setStyleSheet(
            f"background: transparent; font-size: 11px; letter-spacing: 2px; "
            f"color: {ECHELON_PALETTE['text_muted']};"
        )
        layout.addWidget(tag)
        layout.addSpacing(10)

        desc = QLabel(
            "New to Discord bots, AI, and GitHub? You’re in the right place.\n\n"
            "Default path (recommended):\n"
            "  1) Download source from GitHub automatically\n"
            "  2) Install it into the folder you choose\n"
            "  3) Open START_HERE.txt — double-click Echelon.exe if present,\n"
            "     or SETUP_AND_BUILD.bat once (free Python) to create the app\n\n"
            "No admin rights needed for a normal user-folder install.\n"
            "Advanced local EXE / recovery tools are optional."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"background: transparent; color: {ECHELON_PALETTE['text_muted']}; "
            f"font-size: 13px;"
        )
        layout.addWidget(desc)
        layout.addSpacing(12)

        self.source_card = Card()
        src_title = QLabel("Detected package")
        src_title.setObjectName("SectionLabel")
        src_title.setStyleSheet(
            f"background: transparent; color: {ECHELON_PALETTE['accent']}; font-weight: 700;"
        )
        self.source_card.add_widget(src_title)

        lines = [
            "Default mode: Install from GitHub",
            "  → downloads echelon_source from sevinOG/echelon_ecosystem",
            "  → then installs that tree into your chosen folder",
            "",
            "You do not need a local copy of the project first.",
            "Local files (if any) are only used in Advanced options.",
        ]
        color = ECHELON_PALETTE["success"]
        src_text = "\n".join(lines)

        self.source_label = QLabel(src_text)
        self.source_label.setStyleSheet(
            f"background: transparent; color: {color}; font-size: 12px; "
            f"font-family: Consolas, monospace;"
        )
        self.source_label.setWordWrap(True)
        self.source_card.add_widget(self.source_label)

        if self.existing_installed and self.existing_dir:
            reinstall = QLabel(
                f"Note: An existing install was found at:\n{self.existing_dir}\n"
                "Continuing will update/overwrite that location (you can change the path next)."
            )
            reinstall.setWordWrap(True)
            reinstall.setStyleSheet(
                f"background: {ECHELON_PALETTE['surface_2']}; "
                f"border: 1px solid {ECHELON_PALETTE['accent']}; border-radius: 6px; "
                f"padding: 10px; color: {ECHELON_PALETTE['text_muted']}; font-size: 11px;"
            )
            self.source_card.add_widget(reinstall)

        layout.addWidget(self.source_card)
        layout.addStretch(1)

        footer = QLabel(f"OPEN SOURCE  ·  INSTALLER v{VERSION}")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(
            f"background: transparent; color: {ECHELON_PALETTE['text_dim']}; "
            f"font-size: 10px; letter-spacing: 2px;"
        )
        layout.addWidget(footer)
        return page

    def _build_location_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(14)

        head = QLabel("Installation Location")
        head.setStyleSheet(
            f"background: transparent; font-size: 18px; color: {ECHELON_PALETTE['text']}; "
            f"font-weight: 700;"
        )
        layout.addWidget(head)

        info = QLabel(
            "Choose the DESTINATION folder.\n"
            "App install → usually the portable echelon/ folder or LocalAppData.\n"
            "Source recovery → usually echelon_source/ (or any empty folder)."
        )
        info.setWordWrap(True)
        info.setObjectName("MutedLabel")
        layout.addWidget(info)
        layout.addSpacing(6)

        path_row = QHBoxLayout()
        path_row.setSpacing(10)
        self.path_input = QLineEdit()
        self.path_input.setText(self.install_dir)
        self.path_input.setMinimumHeight(44)
        path_row.addWidget(self.path_input, 1)

        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("SecondaryButton")
        browse_btn.setFixedHeight(44)
        browse_btn.setMinimumWidth(100)
        browse_btn.clicked.connect(self._on_browse)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        layout.addSpacing(16)
        src_head = QLabel("Install source")
        src_head.setStyleSheet(
            f"background: transparent; font-size: 15px; color: {ECHELON_PALETTE['text']}; "
            f"font-weight: 700;"
        )
        layout.addWidget(src_head)

        src_info = QLabel(
            "Default: download **Echelon application source** from the public GitHub hub "
            "(sevinOG/echelon_ecosystem → echelon_source/). "
            "Local EXE / path options are under Advanced."
        )
        src_info.setObjectName("MutedLabel")
        src_info.setWordWrap(True)
        layout.addWidget(src_info)
        layout.addSpacing(6)

        self.source_mode_card = Card()
        radio_layout = QVBoxLayout()
        radio_layout.setSpacing(10)
        radio_layout.setContentsMargins(4, 4, 4, 4)

        self.radio_github = QRadioButton(
            "Install from GitHub (recommended) — echelon_source"
        )
        self.radio_github.setChecked(True)
        self.radio_github.toggled.connect(self._on_source_mode_toggled)
        radio_layout.addWidget(self.radio_github)

        # Advanced: local / recover (collapsed by default)
        self.btn_advanced = QPushButton("Advanced local options ▸")
        self.btn_advanced.setObjectName("SecondaryButton")
        self.btn_advanced.setCheckable(True)
        self.btn_advanced.setChecked(False)
        self.btn_advanced.toggled.connect(self._toggle_advanced)
        radio_layout.addWidget(self.btn_advanced)

        self.advanced_wrap = QWidget()
        adv_l = QVBoxLayout(self.advanced_wrap)
        adv_l.setContentsMargins(12, 4, 4, 4)
        adv_l.setSpacing(8)

        self.radio_exe = QRadioButton("Install APP from portable / Echelon.exe")
        self.radio_exe.toggled.connect(self._on_source_mode_toggled)
        adv_l.addWidget(self.radio_exe)

        self.radio_source = QRadioButton("Install APP from local echelon_source tree")
        self.radio_source.toggled.connect(self._on_source_mode_toggled)
        adv_l.addWidget(self.radio_source)

        self.radio_recover = QRadioButton(
            "Recover SOURCE from portable app (two-way / repair)"
        )
        self.radio_recover.toggled.connect(self._on_source_mode_toggled)
        adv_l.addWidget(self.radio_recover)

        self.advanced_wrap.setVisible(False)
        radio_layout.addWidget(self.advanced_wrap)

        self.source_mode_card.add_layout(radio_layout)
        layout.addWidget(self.source_mode_card)

        self.repo_hint = QLabel(f"Default repo: {self.default_repo}")
        self.repo_hint.setWordWrap(True)
        self.repo_hint.setStyleSheet(
            f"font-size: 11px; color: {ECHELON_PALETTE['text_muted']}; "
            f"margin-left: 4px; margin-top: 4px;"
        )
        self.repo_hint.hide()
        layout.addWidget(self.repo_hint)

        src_row = QHBoxLayout()
        src_row.setSpacing(10)
        self.source_input = QLineEdit()
        self.source_input.setPlaceholderText(
            str(self.source_path) if self.source_path
            else "Browse to Echelon.exe or a folder containing it"
        )
        if self.source_path:
            self.source_input.setText(str(self.source_path))
        self.source_input.setMinimumHeight(44)
        src_row.addWidget(self.source_input, 1)

        src_browse = QPushButton("Browse…")
        src_browse.setObjectName("SecondaryButton")
        src_browse.setFixedHeight(44)
        src_browse.setMinimumWidth(100)
        src_browse.clicked.connect(self._on_browse_source)
        src_row.addWidget(src_browse)
        layout.addLayout(src_row)

        layout.addSpacing(16)
        quick_frame = Card()
        qlabel = QLabel("Quick Locations")
        qlabel.setObjectName("SectionLabel")
        qlabel.setStyleSheet(
            f"background: transparent; color: {ECHELON_PALETTE['accent']}; font-weight: 700;"
        )
        quick_frame.add_widget(qlabel)

        qrow = QHBoxLayout()
        qrow.setSpacing(10)
        btn_local = QPushButton("LocalAppData")
        btn_local.setObjectName("SecondaryButton")
        btn_local.setMinimumHeight(38)
        btn_local.clicked.connect(lambda: self.path_input.setText(str(get_default_install_dir())))
        btn_pf = QPushButton("Program Files")
        btn_pf.setObjectName("SecondaryButton")
        btn_pf.setMinimumHeight(38)
        btn_pf.clicked.connect(lambda: self.path_input.setText(str(get_program_files_dir())))
        qrow.addWidget(btn_local)
        qrow.addWidget(btn_pf)
        qrow.addStretch()
        quick_frame.add_layout(qrow)
        layout.addWidget(quick_frame)
        layout.addStretch(1)
        return page

    def _build_options_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(16)

        head = QLabel("Installation Options")
        head.setStyleSheet(
            f"background: transparent; font-size: 18px; color: {ECHELON_PALETTE['text']}; "
            f"font-weight: 700;"
        )
        layout.addWidget(head)

        card = Card()
        for text, attr, default in (
            ("Create desktop shortcut", "chk_desktop", True),
            ("Create Start Menu shortcut", "chk_start", True),
            ("Launch Echelon after installation", "chk_launch", True),
        ):
            cb = QCheckBox(text)
            cb.setChecked(default)
            cb.setMinimumHeight(28)
            setattr(self, attr, cb)
            card.add_widget(cb)

        sep = QFrame()
        sep.setObjectName("Separator")
        sep.setFixedHeight(1)
        card.add_widget(sep)

        info = QLabel(
            "Registered for clean uninstall via Add/Remove Programs. "
            "User-scope install does not require administrator rights."
        )
        info.setWordWrap(True)
        info.setObjectName("MutedLabel")
        card.add_widget(info)
        layout.addWidget(card)

        self.summary_card = Card()
        layout.addWidget(self.summary_card)
        layout.addStretch(1)
        return page

    def _build_install_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(14)

        head = QLabel("Deploying Echelon")
        head.setStyleSheet(
            f"background: transparent; font-size: 18px; color: {ECHELON_PALETTE['text']}; "
            f"font-weight: 700;"
        )
        layout.addWidget(head)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(28)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready to install")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(280)
        self.log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.log_view, stretch=1)

        self.btn_install_now = QPushButton("INSTALL ECHELON")
        self.btn_install_now.setObjectName("PrimaryButton")
        self.btn_install_now.setFixedHeight(48)
        self.btn_install_now.clicked.connect(self._start_install)
        layout.addWidget(self.btn_install_now)
        return page

    def _build_finish_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(18)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.finish_icon = QLabel("OK")
        self.finish_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.finish_icon.setStyleSheet(
            f"background: {ECHELON_PALETTE['success']}; color: white; font-size: 22px; "
            f"font-weight: 800; border-radius: 32px; min-width: 64px; min-height: 64px; "
            f"max-width: 64px; max-height: 64px;"
        )
        layout.addWidget(self.finish_icon, alignment=Qt.AlignmentFlag.AlignCenter)

        self.finish_title = QLabel("Installation Complete")
        self.finish_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.finish_title.setStyleSheet(
            f"background: transparent; font-size: 22px; font-weight: 800; "
            f"color: {ECHELON_PALETTE['text']};"
        )
        layout.addWidget(self.finish_title)

        self.finish_desc = QLabel(f"Echelon has been successfully deployed to\n{self.install_dir}")
        self.finish_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.finish_desc.setWordWrap(True)
        self.finish_desc.setStyleSheet(
            f"background: transparent; color: {ECHELON_PALETTE['text_muted']}; font-size: 13px;"
        )
        layout.addWidget(self.finish_desc)
        layout.addSpacing(16)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self.btn_open_folder = QPushButton("Open Install Folder")
        self.btn_open_folder.setObjectName("SecondaryButton")
        self.btn_open_folder.setFixedHeight(42)
        self.btn_open_folder.setMinimumWidth(150)
        self.btn_open_folder.clicked.connect(self._on_open_folder)

        self.btn_launch = QPushButton("Launch Echelon")
        self.btn_launch.setObjectName("PrimaryButton")
        self.btn_launch.setFixedHeight(42)
        self.btn_launch.setMinimumWidth(140)
        self.btn_launch.clicked.connect(self._on_launch)

        self.btn_close = QPushButton("Close")
        self.btn_close.setObjectName("GhostButton")
        self.btn_close.setFixedHeight(42)
        self.btn_close.setMinimumWidth(100)
        self.btn_close.clicked.connect(self.close)

        btn_row.addStretch()
        btn_row.addWidget(self.btn_open_folder)
        btn_row.addWidget(self.btn_launch)
        btn_row.addWidget(self.btn_close)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch(1)
        return page

    def _update_step(self):
        self.step_indicator.set_current(self.current_step)
        self.stack.setCurrentIndex(self.current_step)
        self.btn_back.setEnabled(self.current_step > 0 and self.current_step < 3)
        if self.current_step == 0:
            self.btn_next.setText("Next")
            self.btn_next.show()
            self.btn_uninstall.setVisible(self.existing_installed)
        elif self.current_step == 1:
            self.btn_next.setText("Next")
            self.btn_next.show()
            self.btn_uninstall.hide()
        elif self.current_step == 2:
            self._update_summary()
            self.btn_next.setText("Install")
            self.btn_next.show()
        elif self.current_step == 3:
            self.btn_next.hide()
            self.btn_uninstall.hide()
        elif self.current_step == 4:
            self.btn_back.hide()
            self.btn_next.setText("Close")
            self.btn_next.show()
            self.btn_uninstall.hide()

    def _update_summary(self):
        try:
            while self.summary_card._layout.count():
                item = self.summary_card._layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
                elif item.layout():
                    while item.layout().count():
                        i2 = item.layout().takeAt(0)
                        w2 = i2.widget()
                        if w2:
                            w2.deleteLater()

            title = QLabel("Summary")
            title.setObjectName("SectionLabel")
            title.setStyleSheet(
                f"background: transparent; color: {ECHELON_PALETTE['accent']}; font-weight: 700;"
            )
            self.summary_card.add_widget(title)

            install_path = self.path_input.text().strip() or str(self.default_install_dir)
            source = self.source_input.text().strip()

            summary_text = (
                f"Install location: {install_path}\n"
                f"Source: {source or str(self.source_path)}\n"
                f"Desktop shortcut: {'Yes' if self.chk_desktop.isChecked() else 'No'}\n"
                f"Start Menu: {'Yes' if self.chk_start.isChecked() else 'No'}\n"
                f"Launch after: {'Yes' if self.chk_launch.isChecked() else 'No'}"
            )
            lab = QLabel(summary_text)
            lab.setStyleSheet(
                f"background: transparent; color: {ECHELON_PALETTE['text_muted']}; "
                f"font-size: 12px; font-family: Consolas, monospace;"
            )
            lab.setWordWrap(True)
            self.summary_card.add_widget(lab)
        except Exception as exc:
            print(f"summary update fail {exc}")

    def _toggle_advanced(self, on: bool):
        if hasattr(self, "advanced_wrap"):
            self.advanced_wrap.setVisible(on)
        if hasattr(self, "btn_advanced"):
            self.btn_advanced.setText(
                "Advanced local options ▾" if on else "Advanced local options ▸"
            )
        if not on and hasattr(self, "radio_github"):
            self.radio_github.setChecked(True)

    def _on_source_mode_toggled(self):
        if getattr(self, "radio_github", None) and self.radio_github.isChecked():
            self.source_mode = "github"
            self.source_input.setEnabled(False)
            self.source_input.setText("github:sevinOG/echelon_ecosystem → echelon_source")
            # Default dest for source checkout
            dest = Path(self.default_install_dir)
            if dest.name.lower() in ("echelon", "echelon_app"):
                # prefer a source-named folder when installing GitHub source
                dest = dest.parent / "echelon_source"
            self.path_input.setText(str(dest))
            self.repo_hint.setText(
                "Downloads https://github.com/sevinOG/echelon_ecosystem "
                "(folder echelon_source/) into the destination path."
            )
            self.repo_hint.show()
        elif getattr(self, "radio_recover", None) and self.radio_recover.isChecked():
            self.source_mode = "recover_source"
            self.source_input.setEnabled(True)
            self.source_input.setText(str(self.default_portable))
            self.path_input.setText(str(self.default_recover_dir))
            self.repo_hint.setText(
                f"From portable: {self.default_portable}\n"
                f"Into source: {self.default_recover_dir}"
            )
            self.repo_hint.show()
        elif self.radio_source.isChecked():
            self.source_mode = "source_repo"
            self.source_input.setText(str(self.default_repo))
            self.source_input.setEnabled(False)
            self.path_input.setText(str(self.default_install_dir))
            self.repo_hint.setText(f"Source tree: {self.default_repo}")
            self.repo_hint.show()
        else:
            self.source_mode = "exe"
            self.source_input.setEnabled(True)
            self.path_input.setText(str(self.default_install_dir))
            self.repo_hint.hide()
            if self.source_path:
                self.source_input.setText(str(self.source_path))
            elif self.default_portable:
                self.source_input.setText(str(self.default_portable / "Echelon.exe"))

    def _on_back(self):
        if self.current_step > 0:
            if self.current_step == 4:
                self.btn_back.show()
            self.current_step -= 1
            self._update_step()

    def _on_next(self):
        if self.current_step == 0:
            self.current_step += 1
        elif self.current_step == 1:
            ptxt = self.path_input.text().strip()
            if not ptxt:
                return
            self.install_dir = ptxt
            if self.radio_source.isChecked():
                if not self.default_repo.exists():
                    self.status_label.setText(
                        f"Source repository not found at {self.default_repo}"
                    )
                    return
                self.source_input.setText(str(self.default_repo))
            self.current_step += 1
        elif self.current_step == 2:
            self.current_step += 1
        elif self.current_step == 3:
            self.current_step += 1
        elif self.current_step == 4:
            self.close()
            return
        self._update_step()

    def _on_browse(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Install Folder", self.path_input.text()
        )
        if dir_path:
            self.path_input.setText(dir_path)

    def _on_browse_source(self):
        start = (
            str(Path(self.source_input.text()).parent)
            if self.source_input.text()
            else os.path.expanduser("~")
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Echelon Executable",
            start,
            "Executables (*.exe);;All Files (*.*)",
        )
        if file_path:
            self.source_input.setText(file_path)
            self.source_type = "exe" if file_path.lower().endswith(".exe") else "dist_dir"
            self.radio_exe.setChecked(True)

    def _start_install(self):
        self.btn_install_now.setEnabled(False)
        self.btn_back.setEnabled(False)
        self.log_view.clear()
        self.progress_bar.setValue(0)

        source_text = self.source_input.text().strip()
        if getattr(self, "radio_github", None) and self.radio_github.isChecked():
            source_text = "github:sevinOG/echelon_ecosystem"
            stype = "github"
        elif getattr(self, "radio_recover", None) and self.radio_recover.isChecked():
            if not source_text:
                source_text = str(self.default_portable)
            stype = "recover_source"
            self.install_dir = self.path_input.text().strip() or str(self.default_recover_dir)
        elif self.radio_source.isChecked():
            source_text = str(self.default_repo)
            stype = "source_dir"
        elif not source_text:
            if self.source_path:
                source_text = str(self.source_path)
                stype = self.source_type or "exe"
            else:
                self._log("ERROR: No source selected. Cannot install.")
                self.btn_install_now.setEnabled(True)
                self.btn_back.setEnabled(True)
                return
        else:
            src_path = Path(source_text)
            if not src_path.exists():
                self._log(f"ERROR: Source path does not exist: {src_path}")
                self.btn_install_now.setEnabled(True)
                self.btn_back.setEnabled(True)
                return
            if src_path.is_file() and src_path.suffix.lower() == ".exe":
                stype = "exe"
            elif src_path.is_dir():
                if (src_path / "Echelon.exe").is_file() or (src_path / "_internal").is_dir():
                    stype = "portable_dir" if (src_path / "Echelon.exe").is_file() else "dist_dir"
                    if (src_path / "Echelon.exe").is_file():
                        # install from onedir parent of exe
                        source_text = str(src_path / "Echelon.exe")
                        stype = "exe"
                elif (src_path / "core").is_dir():
                    stype = "source_dir"
                else:
                    stype = "dist_dir" if list(src_path.glob("*.exe")) else "source_dir"
            else:
                stype = "exe"

        opts = InstallOptions(
            source_exe=source_text,
            install_dir=self.path_input.text().strip(),
            create_desktop_shortcut=self.chk_desktop.isChecked(),
            create_start_menu_shortcut=self.chk_start.isChecked(),
            launch_after=self.chk_launch.isChecked(),
            source_type=stype,
            github_subdir="echelon_source",
        )

        self.worker = InstallWorker(opts)
        self.worker.log_signal.connect(self._log)
        self.worker.progress_signal.connect(self._on_progress)
        self.worker.finished_signal.connect(self._on_install_finished)
        self.worker.start()

    def _log(self, msg: str):
        self.log_view.append(msg)
        sb = self.log_view.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _on_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        if msg:
            self.status_label.setText(msg)

    def _on_install_finished(self, success: bool):
        self.install_success = success
        self.btn_install_now.setEnabled(True)
        self.btn_back.setEnabled(True)
        if success:
            self.progress_bar.setValue(100)
            self.status_label.setText("Installation successful")
            self._log("=== INSTALL SUCCESS ===")
            install_dir = Path(self.path_input.text().strip())
            # Resolve launch target for Finish page
            self.launch_exe = self._resolve_launch_exe(install_dir)
            if self.launch_exe:
                self._log(f"Launch target: {self.launch_exe}")
            else:
                self._log("WARNING: Could not resolve Echelon.exe for Launch button")
            self.finish_desc.setText(
                f"Echelon has been successfully deployed to\n{install_dir}\n\n"
                + (f"App: {self.launch_exe}" if self.launch_exe else "Use Open Install Folder if Launch fails.")
            )
            self.btn_launch.setEnabled(bool(self.launch_exe))
            QTimer.singleShot(800, self._go_to_finish)
        else:
            self.status_label.setText("Installation failed - check log")
            self._log("=== INSTALL FAILED ===")
            self.btn_install_now.setText("RETRY INSTALL")
            self.btn_install_now.setEnabled(True)

    def _go_to_finish(self):
        self.current_step = 4
        self._update_step()
        if self.chk_launch.isChecked() and self.install_success:
            self._on_launch()

    def _on_uninstall_clicked(self):
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Uninstall Echelon",
            f"Uninstall Echelon from {self.existing_dir}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.current_step = 3
        self._update_step()
        self.btn_install_now.hide()
        self.status_label.setText("Uninstalling...")
        self.log_view.clear()
        self.progress_bar.setValue(0)

        self.uninstall_worker = UninstallWorker(self.existing_dir, remove_dir=True)
        self.uninstall_worker.log_signal.connect(self._log)
        self.uninstall_worker.progress_signal.connect(self._on_progress)
        self.uninstall_worker.finished_signal.connect(self._on_uninstall_finished)
        self.uninstall_worker.start()

    def _on_uninstall_finished(self, success: bool):
        if success:
            self.progress_bar.setValue(100)
            self.status_label.setText("Uninstall complete")
            self.finish_title.setText("Uninstall Complete")
            self.finish_desc.setText(f"Echelon has been removed from {self.existing_dir}")
            self.finish_icon.setText("X")
            self.finish_icon.setStyleSheet(
                f"background: {ECHELON_PALETTE['text_muted']}; color: white; "
                f"font-size: 22px; font-weight: 800; border-radius: 32px; "
                f"min-width: 64px; min-height: 64px; max-width: 64px; max-height: 64px;"
            )
            self.btn_launch.hide()
            self.btn_open_folder.hide()
            self.current_step = 4
            self._update_step()
        else:
            self.status_label.setText("Uninstall failed")
            self.btn_install_now.setEnabled(True)

    def _on_open_folder(self):
        path = self.path_input.text().strip() or self.install_dir
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            self._log(f"Open folder failed: {exc}")

    def _resolve_launch_exe(self, install_dir: Path) -> str | None:
        install_dir = Path(install_dir)
        marker = install_dir / ".echelon_launch_path"
        if marker.is_file():
            try:
                p = Path(marker.read_text(encoding="utf-8").strip())
                if p.is_file():
                    return str(p)
            except Exception:
                pass
        from ..core.installer import Installer
        found = Installer()._find_main_exe(install_dir)
        return str(found) if found else None

    def _on_launch(self):
        import subprocess
        try:
            install_dir = Path(self.path_input.text().strip() or self.install_dir)
            exe = self.launch_exe or self._resolve_launch_exe(install_dir)
            if not exe or not Path(exe).is_file():
                self._log(f"Launch failed — no Echelon.exe under {install_dir}")
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self,
                    "Cannot launch",
                    f"Could not find Echelon.exe in:\n{install_dir}\n\n"
                    "Use Open Install Folder and double-click Echelon.exe.",
                )
                return

            exe_path = Path(exe)
            cwd = str(exe_path.parent)
            self._log(f"Launching: {exe_path}")

            if platform.system() == "Windows":
                # Prefer subprocess so we get a real process; startfile as fallback
                try:
                    subprocess.Popen(
                        [str(exe_path)],
                        cwd=cwd,
                        close_fds=True,
                    )
                except Exception:
                    os.startfile(str(exe_path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen([str(exe_path)], cwd=cwd)
            self._log("Launch started.")
        except Exception as exc:
            self._log(f"Launch failed: {exc}")
