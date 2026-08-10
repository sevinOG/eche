"""Locate the three Eche trees and resolve install sources."""
from __future__ import annotations
import os
import sys
from pathlib import Path

_PORTABLE_NAMES = ("eche", "Eche", "eche_app")
_SOURCE_NAMES = ("eche_source", "Eche", "eche-source")
_INSTALLER_NAMES = ("eche_installer", "eche-installer", "eche_installer_source",)

GITHUB_HUB = "https://github.com/sevinOG/eche"
GITHUB_DEFAULT_SUBDIR = "eche_source"

def workspace_root() -> Path:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        candidates = [exe.parent, exe.parent.parent, Path.cwd()]
        for base in candidates:
            if _any_tree(base):
                return base
        return exe.parent
    here = Path(__file__).resolve()
    for parent in here.parents:
        if _any_tree(parent):
            return parent
    return here.parents[3]

def _any_tree(base: Path) -> bool:
    return _portable_dir(base) is not None or _source_dir(base) is not None

def _portable_dir(base: Path) -> Path | None:
    for name in _PORTABLE_NAMES:
        p = base / name
        if not p.is_dir(): continue
        if (p / "Eche.exe").is_file():
            return p
        if (p / "install.json").is_file():
            return p
        if name.lower() == "eche" and not (p / "core").is_dir():
            return p
    return None

def _source_dir(base: Path) -> Path | None:
    for name in _SOURCE_NAMES:
        p = base / name
        if not p.is_dir(): continue
        if (p / "Eche.exe").is_file() and not (p / "build_exe.spec").is_file():
            continue
        if (p / "eche_app.py").is_file() or ((p / "core").is_dir() and (p / "gui").is_dir()):
            return p
    return None

def find_portable_app() -> tuple[Path | None, str]:
    ws = workspace_root()
    portable = _portable_dir(ws)
    if portable is None: return None, "none"
    exe = portable / "Eche.exe"
    if exe.is_file(): return exe, "exe"
    return portable, "portable_dir"

def find_source_tree() -> Path | None:
    return _source_dir(workspace_root())

def find_eche_source() -> tuple[Path | None, str]:
    ws = workspace_root()
    path, kind = find_portable_app()
    if path is not None and kind == "exe":
        return path, "exe"
    src = _source_dir(ws)
    if src is not None:
        onedir = src / "dist" / "Eche" / "Eche.exe"
        if onedir.is_file(): return onedir, "exe"
        if (src / "eche_app.py").is_file():
            return src, "source_dir"
    portable = _portable_dir(ws)
    if portable is not None:
        return portable, "portable_dir"
    return None, "none"

def get_default_install_dir() -> Path:
    # FIX: For frozen installer, ALWAYS use LocalAppData, never workspace
    if getattr(sys, "frozen", False):
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            return Path(localappdata) / "Eche"
    ws = workspace_root()
    portable = ws / "eche"
    if (ws / "eche_source").is_dir() or (ws / "eche_installer").is_dir():
        # dev mode - keep in workspace
        return portable
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "Eche"
    return Path(os.path.expanduser("~")) / "Eche"

def get_default_source_recover_dir() -> Path:
    return workspace_root() / "eche_source"

def get_program_files_dir() -> Path:
    pf = os.environ.get("PROGRAMFILES")
    return Path(pf) / "Eche" if pf else Path("C:/Program Files/Eche")

def describe_trees() -> dict[str, str | None]:
    ws = workspace_root()
    portable = _portable_dir(ws)
    source = _source_dir(ws)
    p_exe = str(portable / "Eche.exe") if portable and (portable / "Eche.exe").is_file() else None
    return {"workspace": str(ws), "portable": str(portable) if portable else None, "portable_exe": p_exe, "source": str(source) if source else None}