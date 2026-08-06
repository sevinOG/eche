# core/paths.py
# Three-tree layout:
#
#   eche/              ← portable app (Eche.exe + data) — flash-drive ready
#   eche_source/       ← this tree when developing (core/ gui/ cogs/)
#   eche_installer/    ← deploy / recover wizard
#
# Writable data always lives next to the package root (portable), never AppData
# unless the user overrides ECHE_USER_ROOT.

from __future__ import annotations

import json
import os
import sys

# Source-tree markers (dev package) — must be buildable
_SOURCE_MARKER_FILES = (
    "build_exe.spec",
    "package_portable.bat",
    "BUILD.bat",
    "eche_app.py",
)
# Portable-app markers (flash-drive package)
_PORTABLE_MARKER_FILES = (
    "Eche.exe",
    "install.json",
    "VERSION",
)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def _looks_like_source_tree(path: str | None) -> bool:
    if not path or not os.path.isdir(path):
        return False
    if not os.path.isdir(os.path.join(path, "core")):
        return False
    if not os.path.isdir(os.path.join(path, "gui")):
        return False
    for name in _SOURCE_MARKER_FILES:
        if os.path.isfile(os.path.join(path, name)):
            return True
    return False


def _looks_like_portable_app(path: str | None) -> bool:
    """Runnable app folder: Eche.exe co-located with data dirs."""
    if not path or not os.path.isdir(path):
        return False
    if os.path.isfile(os.path.join(path, "Eche.exe")):
        return True
    # onedir nested under dist/Eche
    nested = os.path.join(path, "Eche.exe")
    if os.path.isfile(nested):
        return True
    if os.path.isfile(os.path.join(path, "install.json")):
        try:
            with open(os.path.join(path, "install.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("kind") == "portable_app":
                return True
        except Exception:
            pass
    return False


def _looks_like_package(path: str | None) -> bool:
    """Any valid unit of distribution (source or portable)."""
    return _looks_like_source_tree(path) or _looks_like_portable_app(path)


def is_source_tree(path: str | None) -> bool:
    """True if path looks like an open-source checkout (may still be polluted)."""
    return _looks_like_source_tree(path)


def has_build_venv(path: str | None) -> bool:
    if not path:
        return False
    return os.path.isfile(
        os.path.join(path, ".venv", "Scripts", "python.exe")
    ) or os.path.isfile(os.path.join(path, ".venv", "bin", "python"))


def is_buildable_source(path: str | None) -> bool:
    """
    True only for a real rebuild tree:
      core/ + gui/ + build_exe.spec + BUILD.bat (or package_portable.bat)
    Rejects portable app folders (install.json kind=portable_app, or exe without venv).
    """
    if not path or not os.path.isdir(path):
        return False
    path = os.path.abspath(path)
    if not os.path.isdir(os.path.join(path, "core")):
        return False
    if not os.path.isdir(os.path.join(path, "gui")):
        return False
    if not os.path.isfile(os.path.join(path, "build_exe.spec")):
        return False
    has_bat = os.path.isfile(os.path.join(path, "BUILD.bat")) or os.path.isfile(
        os.path.join(path, "package_portable.bat")
    )
    if not has_bat:
        return False
    # Explicit portable marker
    ij = os.path.join(path, "install.json")
    if os.path.isfile(ij):
        try:
            with open(ij, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("kind") == "portable_app":
                # Only allow if this is *also* a real source tree with venv
                # (should not happen — portable publish writes this file)
                if not has_build_venv(path):
                    return False
        except Exception:
            pass
    # Folder that only has a frozen exe and no venv is not buildable source
    if os.path.isfile(os.path.join(path, "Eche.exe")) and not has_build_venv(path):
        return False
    return True


def _walk_up(start: str, max_hops: int = 8) -> str | None:
    cur = os.path.abspath(start)
    for _ in range(max_hops):
        if _looks_like_package(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def _read_install_json(base: str) -> dict:
    for path in (
        os.path.join(base, "install.json"),
        os.path.join(base, "dist", "Eche", "install.json"),
    ):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            continue
    return {}


def package_root() -> str:
    """
    Writable / portable package root.
    - Frozen portable: folder containing Eche.exe (flash-drive root)
    - Source: eche_source/ (parent of core/)
    """
    if is_frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # Prefer the directory that holds the exe (true portable root)
        if os.path.isfile(os.path.join(exe_dir, "Eche.exe")) or os.path.basename(
            sys.executable
        ).lower().startswith("eche"):
            # If markers exist higher up (legacy dist under source), still prefer
            # portable root = exe_dir for flash-drive layout
            data = _read_install_json(exe_dir)
            if data.get("kind") == "portable_app" or os.path.isfile(
                os.path.join(exe_dir, "install.json")
            ):
                return exe_dir
            found = _walk_up(exe_dir)
            if found and _looks_like_portable_app(found):
                return found
            if found and _looks_like_source_tree(found):
                # Running from source dist/Eche — package root is source tree
                return found
            return exe_dir

        found = _walk_up(exe_dir)
        if found:
            return found
        data = _read_install_json(exe_dir)
        pr = str(data.get("package_root") or data.get("project_root") or "").strip()
        if pr and os.path.isdir(pr):
            return os.path.abspath(pr)
        return exe_dir

    # Source: core/paths.py → eche_source root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_dir() -> str:
    """Read-only bundled assets (PyInstaller extract / package)."""
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    if is_frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        internal = os.path.join(exe_dir, "_internal")
        if os.path.isdir(internal):
            return internal
        return exe_dir
    return package_root()


def user_dir() -> str:
    """
    Writable data root — always the portable package root.
    Override with ECHE_USER_ROOT only for a deliberate split layout.
    """
    override = (os.environ.get("ECHE_USER_ROOT") or "").strip()
    if override and os.path.isdir(override):
        return os.path.abspath(override)
    return package_root()


def project_root() -> str:
    return user_dir()


def dist_exe() -> str | None:
    """Path to a built Eche.exe if present under source or portable tree."""
    root = package_root()
    candidates = [
        os.path.join(root, "Eche.exe"),
        os.path.join(root, "dist", "Eche", "Eche.exe"),
        os.path.join(root, "dist", "Eche.exe"),
    ]
    # sibling portable tree when running from source
    sibling = os.path.join(os.path.dirname(root), "eche", "Eche.exe")
    candidates.append(sibling)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def ensure_user_layout() -> str:
    root = user_dir()
    for name in ("config", "cookies", "logs", "context", "memories", "assets"):
        os.makedirs(os.path.join(root, name), exist_ok=True)
    return root


def context_dir(server_id: str | int | None = None) -> str:
    root = ensure_user_layout()
    base = os.path.join(root, "context")
    if server_id is None or str(server_id).strip() == "":
        os.makedirs(base, exist_ok=True)
        return base
    path = os.path.join(base, str(server_id).strip())
    os.makedirs(path, exist_ok=True)
    return path


def memories_dir(server_id: str | int | None = None) -> str:
    root = ensure_user_layout()
    base = os.path.join(root, "memories")
    if server_id is None or str(server_id).strip() == "":
        os.makedirs(base, exist_ok=True)
        return base
    path = os.path.join(base, str(server_id).strip())
    os.makedirs(path, exist_ok=True)
    return path


def _score_source_candidate(path: str) -> int:
    """Higher = better rebuild target. Negative = reject."""
    if not is_buildable_source(path):
        return -1
    score = 10
    base = os.path.basename(os.path.abspath(path)).lower()
    if base == "eche_source":
        score += 50
    elif base in ("eche-source",):
        score += 40
    elif base == "eche":
        # Often the portable install target — demote hard unless it has venv
        score -= 30
    if has_build_venv(path):
        score += 40
    if os.path.isfile(os.path.join(path, "requirements.txt")):
        score += 5
    # install.json portable_app without venv already rejected; with venv still demote
    ij = os.path.join(path, "install.json")
    if os.path.isfile(ij):
        try:
            with open(ij, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("kind") == "portable_app":
                score -= 20
        except Exception:
            pass
    return score


def source_root(stored: str | None = None) -> str | None:
    """
    Locate a *buildable* eche_source tree for one-tap rebuilds.
    Never returns a portable install folder that only looks like source
    (installer may have copied core/ into eche/).
    """
    candidates: list[str] = []

    def _add(p: str | None) -> None:
        if not p:
            return
        ap = os.path.abspath(p)
        if ap not in candidates and os.path.isdir(ap):
            candidates.append(ap)

    stored = (stored or "").strip() or None
    if stored:
        _add(stored)

    env_src = (os.environ.get("ECHE_SOURCE_ROOT") or "").strip()
    if env_src:
        _add(env_src)

    root = package_root()
    parent = os.path.dirname(root)
    # Always prefer sibling eche_source next to portable app / workspace
    for name in ("eche_source", "eche-source"):
        _add(os.path.join(parent, name))
        _add(os.path.join(root, name))
        _add(os.path.join(os.path.dirname(parent), name))

    _add(root)
    # Workspace-style: …/workspace/{eche,eche_source,eche_installer}
    for name in ("eche_source", "eche-source"):
        _add(os.path.join(parent, name))

    # Walk from cwd and from executable
    for start in (os.getcwd(), os.path.dirname(os.path.abspath(sys.executable))):
        try:
            cur = os.path.abspath(start)
            for _ in range(6):
                for name in ("eche_source", "eche-source"):
                    _add(os.path.join(cur, name))
                _add(cur)
                nxt = os.path.dirname(cur)
                if nxt == cur:
                    break
                cur = nxt
        except Exception:
            pass

    best: str | None = None
    best_score = -1
    for cand in candidates:
        sc = _score_source_candidate(cand)
        if sc > best_score:
            best_score = sc
            best = cand

    if best is not None and best_score >= 10:
        return best
    return None


def _looks_like_portable_only(path: str | None) -> bool:
    """True if this is a flash-drive / installed app root, not buildable source."""
    if not path or not os.path.isdir(path):
        return False
    if is_buildable_source(path) and has_build_venv(path):
        # Real source that happens to also have an exe
        if os.path.basename(os.path.abspath(path)).lower() == "eche_source":
            return False
    has_exe = os.path.isfile(os.path.join(path, "Eche.exe"))
    if has_exe and not has_build_venv(path):
        return True
    ij = os.path.join(path, "install.json")
    if os.path.isfile(ij):
        try:
            with open(ij, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("kind") == "portable_app":
                if not has_build_venv(path):
                    return True
        except Exception:
            pass
    return False


def resolve_source_root(stored: str | None = None) -> str:
    found = source_root(stored)
    if found:
        return found
    # Do not fall back to portable package_root — that caused BUILD in eche/
    return found or ""


def build_script_path(stored: str | None = None) -> str | None:
    """Path to eche_source/BUILD.bat if available."""
    src = source_root(stored)
    if not src:
        return None
    for name in ("BUILD.bat", "package_portable.bat"):
        p = os.path.join(src, name)
        if os.path.isfile(p):
            return p
    return None


def bundle_file(*rel_parts: str) -> str | None:
    """Resolve a read-only file from the frozen bundle or package root."""
    candidates: list[str] = []

    if hasattr(sys, "_MEIPASS"):
        meipass = sys._MEIPASS  # type: ignore[attr-defined]
        candidates.append(os.path.join(meipass, *rel_parts))
        candidates.append(os.path.join(meipass, "_internal", *rel_parts))

    if is_frozen():
        here = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(here, *rel_parts))
        candidates.append(os.path.join(here, "_internal", *rel_parts))

    root = package_root()
    candidates.append(os.path.join(root, *rel_parts))

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def readable_core_file(*rel_parts: str) -> str:
    """
    Best path to read a core/ package file (source tree or frozen bundle).
    Example: readable_core_file("client.py") → …/core/client.py
    """
    parts = ("core", *rel_parts) if rel_parts and rel_parts[0] != "core" else rel_parts
    if not parts:
        parts = ("core",)
    found = bundle_file(*parts)
    if found:
        return found
    # Prefer source tree core/
    src = source_root() or package_root()
    return os.path.join(src, *parts)


def writable_core_file(*rel_parts: str) -> str:
    """
    Writable path for core/ edits (always package / source root, never AppData).
    Frozen builds write next to the portable package when core/ exists there,
    else fall back to _internal copy path for best-effort.
    """
    parts = ("core", *rel_parts) if rel_parts and rel_parts[0] != "core" else rel_parts
    if not parts:
        parts = ("core",)
    root = source_root() or package_root()
    target = os.path.join(root, *parts)
    if os.path.isdir(os.path.dirname(target)) or is_source_tree(root):
        return target
    # portable with bundled core under _internal
    bdir = bundle_dir()
    return os.path.join(bdir, *parts)


def find_opus_dll() -> str | None:
    candidates: list[str] = []
    root = package_root()
    bdir = bundle_dir()
    candidates.extend(
        [
            os.path.join(root, "opus.dll"),
            os.path.join(root, "run", "opus.dll"),
            os.path.join(bdir, "opus.dll"),
            os.path.join(bdir, "run", "opus.dll"),
        ]
    )
    try:
        import discord

        candidates.append(os.path.join(os.path.dirname(discord.__file__), "opus.dll"))
    except Exception:
        pass
    candidates.append(
        os.path.join(root, ".venv", "Lib", "site-packages", "discord", "opus.dll")
    )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def describe_layout() -> dict[str, str | bool | None]:
    return {
        "frozen": is_frozen(),
        "package_root": package_root(),
        "user_dir": user_dir(),
        "source_root": source_root(),
        "bundle_dir": bundle_dir(),
        "dist_exe": dist_exe(),
        "is_source": is_source_tree(package_root()),
        "is_portable": _looks_like_portable_app(package_root()),
        "portable": True,
    }
