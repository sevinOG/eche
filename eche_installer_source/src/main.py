"""Eche Installer - Entry Point"""
import sys
import os
import argparse
from pathlib import Path

# Ensure package import works when running as script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication
from src.ui.installer_window import EcheInstallerWindow
from src.ui.theme import VERSION

def handle_cli_uninstall():
    """If run with --uninstall, do silent uninstall or GUI uninstall mode"""
    parser = argparse.ArgumentParser(description=f"Eche Installer v{VERSION}")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall Eche")
    parser.add_argument("--install-dir", type=str, help="Custom install dir for CLI mode")
    parser.add_argument("--silent", action="store_true", help="Silent mode (for uninstaller exe)")
    args, _ = parser.parse_known_args()

    if args.uninstall:
        # Try to find install dir via registry or manifest
        install_dir = args.install_dir
        if not install_dir:
            try:
                from src.core.registry import is_installed
                installed, idir = is_installed()
                if installed:
                    install_dir = idir
            except Exception:
                pass

        if not install_dir:
            # fallback to default
            from src.core.builder import get_default_install_dir
            install_dir = str(get_default_install_dir())

        if args.silent:
            # CLI uninstall, no GUI
            from src.core.uninstaller import Uninstaller
            print(f"[Eche] Silent uninstall from {install_dir}")
            u = Uninstaller()
            ok = u.uninstall(install_dir, remove_dir=True)
            sys.exit(0 if ok else 1)
        else:
            # GUI mode but pre-select uninstall
            return True, install_dir

    return False, None

def main():
    # Handle CLI uninstall first
    is_uninstall_mode, uninstall_dir = handle_cli_uninstall()

    # Windows taskbar grouping
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Eche.Installer.1"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Eche Installer")
    app.setApplicationVersion(VERSION)
    app.setStyle("Fusion")

    # Same brand as Eche: assets/icon.png (bundled in frozen builds)
    try:
        from src.ui.installer_window import brand_icon
        icon = brand_icon()
        if not icon.isNull():
            app.setWindowIcon(icon)
    except Exception:
        pass

    window = EcheInstallerWindow()

    # If launched with --uninstall and not silent, trigger uninstall UI
    if is_uninstall_mode and uninstall_dir:
        from src.core.registry import is_installed
        # Force existing detection
        window.existing_installed = True
        window.existing_dir = uninstall_dir
        # Show window
        window.show()
        # Auto trigger uninstall after short delay? We'll let user click, but we can also show dialog
        # For uninstaller exe, auto prompt
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(300, window._on_uninstall_clicked)
    else:
        window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
