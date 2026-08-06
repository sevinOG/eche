"""Uninstaller logic"""
import json
import shutil
import os
import time
from pathlib import Path
from typing import Callable, List

from .shortcut import remove_shortcut
from .registry import unregister_uninstall
from .installer import MANIFEST_NAME

class Uninstaller:
    def __init__(self, log_callback: Callable[[str], None] = None, progress_callback: Callable[[int, str], None] = None):
        self.log_cb = log_callback or (lambda m: print(m))
        self.progress_cb = progress_callback or (lambda p, m: None)

    def log(self, msg: str):
        self.log_cb(msg)

    def progress(self, pct: int, msg: str = ""):
        self.progress_cb(pct, msg)

    def uninstall(self, install_dir: str, remove_dir: bool = True) -> bool:
        try:
            install_path = Path(install_dir)
            if not install_path.exists():
                self.log(f"Install dir not found: {install_dir} - cleaning registry only")
                unregister_uninstall()
                return True

            self.log(f"[Eche] Uninstalling from {install_path}")
            self.progress(10, "Reading manifest...")

            manifest_path = install_path / MANIFEST_NAME
            files_to_remove: List[str] = []
            shortcuts: List[str] = []

            if manifest_path.exists():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    files_to_remove = data.get("files", [])
                    shortcuts = data.get("shortcuts", [])
                    self.log(f"Manifest: {len(files_to_remove)} files, {len(shortcuts)} shortcuts")
                except Exception as exc:
                    self.log(f"Manifest read error: {exc}")
            else:
                self.log("No manifest found - will remove entire directory")

            self.progress(30, "Removing shortcuts...")
            for sc in shortcuts:
                try:
                    p = Path(sc)
                    if p.exists():
                        p.unlink()
                        self.log(f"Removed shortcut: {sc}")
                except Exception as exc:
                    self.log(f"Shortcut remove failed {sc}: {exc}")

            common_shortcuts = [
                Path(os.path.expanduser("~/Desktop/Eche.lnk")),
                Path(os.path.expanduser("~/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Eche/Eche.lnk")),
                Path(os.path.expanduser("~/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Eche/Uninstall Eche.lnk")),
            ]
            for sc in common_shortcuts:
                try:
                    if sc.exists():
                        sc.unlink()
                        self.log(f"Removed common shortcut: {sc}")
                except Exception:
                    pass

            self.progress(50, "Unregistering...")
            if unregister_uninstall():
                self.log("Removed from Add/Remove Programs")
            else:
                self.log("Registry cleanup done / skipped")

            self.progress(70, "Removing files...")
            for f in files_to_remove:
                try:
                    p = Path(f)
                    try:
                        p.relative_to(install_path)
                    except ValueError:
                        continue
                    if p.exists() and p.is_file():
                        p.unlink()
                except Exception as exc:
                    self.log(f"File remove warning {f}: {exc}")

            if remove_dir:
                self.log(f"Removing directory tree {install_path}")
                try:
                    import sys
                    current_exe = Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else None
                    if current_exe and install_path in current_exe.parents:
                        self.log("Uninstaller is inside install dir - scheduling deletion via batch")
                        self._schedule_self_delete(install_path)
                        self.progress(100, "Uninstall scheduled")
                        return True
                except Exception as exc:
                    self.log(f"Self-check warning: {exc}")

                try:
                    shutil.rmtree(install_path, ignore_errors=False)
                    self.log(f"Removed directory {install_path}")
                except Exception as exc:
                    self.log(f"Directory remove warning: {exc}. Some files may remain.")
                    try:
                        shutil.rmtree(install_path, ignore_errors=True)
                    except Exception:
                        pass

            self.progress(100, "Uninstall complete")
            self.log("[SUCCESS] Eche uninstalled")
            return True

        except Exception as exc:
            self.log(f"[FATAL] Uninstall failed: {exc}")
            import traceback
            self.log(traceback.format_exc())
            return False

    def _schedule_self_delete(self, install_path: Path):
        try:
            import tempfile
            bat_path = Path(tempfile.gettempdir()) / f"eche_uninstall_{int(time.time())}.bat"
            bat_content = f"""@echo off
timeout /t 2 /nobreak >nul
:loop
rmdir /s /q "{install_path}" 2>nul
if exist "{install_path}" (
  timeout /t 1 /nobreak >nul
  goto loop
)
del "%~f0"
"""
            bat_path.write_text(bat_content, encoding="utf-8")
            import subprocess
            subprocess.Popen(["cmd", "/c", str(bat_path)], creationflags=subprocess.DETACHED_PROCESS if os.name == 'nt' else 0)
        except Exception as exc:
            self.log(f"Self-delete schedule failed: {exc}")
