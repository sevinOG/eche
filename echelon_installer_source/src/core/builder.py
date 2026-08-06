"""Locate the three Echelon trees and resolve install sources."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Four-product layout (+ legacy aliases)
_PORTABLE_NAMES = ("echelon", "Echelon", "echelon_app")
_SOURCE_NAMES = ("echelon_source", "Echelon", "echelon-source")
_INSTALLER_NAMES = (
    "echelon_installer",
    "echelon-installer",
    "echelon_installer_source",
)
_INSTALLER_SOURCE_NAMES = ("echelon_installer_source",)

# Public hub (default install target)
GITHUB_HUB = "https://github.com/sevinOG/echelon_ecosystem"
GITHUB_DEFAULT_SUBDIR = "echelon_source"


def workspace_root() -> Path:
    """
    Parent folder that may contain echelon/, echelon_source/, echelon_installer/.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        candidates = [
            exe.parent,
            exe.parent.parent,
            Path.cwd(),
            Path.home() / ".openclaw" / "workspace",
        ]
        for base in candidates:
            if _any_tree(base):
                return base
            if base.name in _INSTALLER_NAMES and _any_tree(base.parent):
                return base.parent
        return exe.parent

    here = Path(__file__).resolve()
    for parent in here.parents:
        if _any_tree(parent):
            return parent
        if parent.name in _INSTALLER_NAMES and _any_tree(parent.parent):
            return parent.parent
    return here.parents[3]


def _any_tree(base: Path) -> bool:
    return (
        _portable_dir(base) is not None
        or _source_dir(base) is not None
        or any((base / n).is_dir() for n in _INSTALLER_NAMES)
    )


def _portable_dir(base: Path) -> Path | None:
    for name in _PORTABLE_NAMES:
        p = base / name
        if not p.is_dir():
            continue
        # Prefer real portable (has exe or install.json kind)
        if (p / "Echelon.exe").is_file():
            return p
        if (p / "install.json").is_file():
            return p
        # empty shell still counts as portable target
        if name.lower() == "echelon" and not (p / "core").is_dir():
            return p
    return None


def _source_dir(base: Path) -> Path | None:
    for name in _SOURCE_NAMES:
        p = base / name
        if not p.is_dir():
            continue
        if (p / "echelon_app.py").is_file() or (
            (p / "core").is_dir() and (p / "gui").is_dir()
        ):
            # Don't treat portable-only echelon/ as source
            if (p / "Echelon.exe").is_file() and not (p / "build_exe.spec").is_file():
                continue
            if (p / "build_exe.spec").is_file() or (p / "package_portable.bat").is_file():
                return p
            if (p / "core").is_dir() and (p / "cogs").is_dir():
                return p
    return None


def find_portable_app() -> tuple[Path | None, str]:
    """Find flash-drive portable Echelon.exe."""
    ws = workspace_root()
    portable = _portable_dir(ws)
    if portable is None:
        return None, "none"

    exe = portable / "Echelon.exe"
    if exe.is_file():
        return exe, "exe"
    nested = portable / "dist" / "Echelon" / "Echelon.exe"
    if nested.is_file():
        return nested, "exe"
    return portable, "portable_dir"


def find_source_tree() -> Path | None:
    return _source_dir(workspace_root())


def find_echelon_source() -> tuple[Path | None, str]:
    """
    Best install source for the app (prefer portable/exe over raw source).

    Returns (path, type):
      exe | dist_dir | portable_dir | source_dir | none
    """
    ws = workspace_root()

    # 1) Sibling portable tree with exe
    path, kind = find_portable_app()
    if path is not None and kind == "exe":
        return path, "exe"
    if path is not None and kind == "portable_dir":
        # shell without exe yet — fall through to source dist
        pass

    # 2) Source tree built dist
    src = _source_dir(ws)
    if src is not None:
        onedir = src / "dist" / "Echelon" / "Echelon.exe"
        if onedir.is_file():
            return onedir, "exe"
        onefile = src / "dist" / "Echelon.exe"
        if onefile.is_file():
            return onefile, "exe"
        dist = src / "dist"
        if dist.is_dir():
            for p in sorted(dist.rglob("Echelon.exe")):
                if "build" in p.parts:
                    continue
                return p, "exe"
        if (src / "echelon_app.py").is_file() or (src / "core").is_dir():
            return src, "source_dir"

    # 3) Portable shell without exe
    portable = _portable_dir(ws)
    if portable is not None:
        return portable, "portable_dir"

    return None, "none"


def get_default_install_dir() -> Path:
    """Default: sibling portable tree if present, else LocalAppData."""
    ws = workspace_root()
    portable = ws / "echelon"
    if portable.is_dir() or True:
        # Prefer publishing into workspace portable folder when running from workspace
        if (ws / "echelon_source").is_dir() or (ws / "echelon_installer").is_dir():
            return portable
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "Echelon"
    return Path(os.path.expanduser("~")) / "Echelon"


def get_default_source_recover_dir() -> Path:
    ws = workspace_root()
    return ws / "echelon_source"


def get_program_files_dir() -> Path:
    pf = os.environ.get("PROGRAMFILES")
    if pf:
        return Path(pf) / "Echelon"
    return Path("C:/Program Files/Echelon")


def describe_trees() -> dict[str, str | None]:
    ws = workspace_root()
    portable = _portable_dir(ws)
    source = _source_dir(ws)
    p_exe = None
    if portable and (portable / "Echelon.exe").is_file():
        p_exe = str(portable / "Echelon.exe")
    return {
        "workspace": str(ws),
        "portable": str(portable) if portable else None,
        "portable_exe": p_exe,
        "source": str(source) if source else None,
        "installer": str(ws / "echelon_installer")
        if (ws / "echelon_installer").is_dir()
        else None,
    }
