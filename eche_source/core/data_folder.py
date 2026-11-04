# core/data_folder.py
"""Move Eche's writable data (config/, memory, logs...) to a folder updates can't touch.

The chosen folder is recorded in the pointer file from core.paths.pointer_file(),
which lives in %LOCALAPPDATA%\\Eche, outside every install/source folder.
DPAPI secrets are tied to the Windows user, so copying them on the same user is fine.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from core import paths

# Folders copied on "Move data here". config/ is the one that matters.
DATA_DIRS = ("config", "context", "memories", "cookies", "assets")


@dataclass
class MovePlan:
    source: str
    target: str
    copies: list[tuple[str, str]] = field(default_factory=list)   # (src, dst)
    conflicts: list[str] = field(default_factory=list)            # dst paths that already exist and differ
    same: list[str] = field(default_factory=list)                 # dst identical already


def _protected_roots() -> list[str]:
    """Folders updates/builds rewrite. The data folder must not be inside these."""
    roots = []
    for fn in (paths.package_root, paths.bundle_dir):
        try:
            roots.append(fn())
        except Exception:
            pass
    try:
        src = paths.source_root(None)
        if src:
            roots.append(src)
            roots.append(os.path.join(os.path.dirname(src), "eche"))  # portable publish dir
    except Exception:
        pass
    return [r for r in roots if r]


def validate_target(target: str) -> str | None:
    """Return an error message, or None if the folder is OK to use."""
    if not target or not target.strip():
        return "Pick a folder first."
    target = os.path.abspath(os.path.expanduser(target.strip()))
    if os.path.exists(target) and not os.path.isdir(target):
        return "That path is a file, not a folder."
    for root in _protected_roots():
        if paths.is_within(target, root):
            return f"Pick a folder outside the app folder ({root}). Updates rewrite that folder."
        if paths.is_within(root, target):
            return f"That folder contains the app folder ({root}). Pick a separate folder."
    return None


def _same_file(a: str, b: str) -> bool:
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def plan_move(target: str, source: str | None = None) -> MovePlan:
    source = os.path.abspath(source or paths.user_dir())
    target = os.path.abspath(os.path.expanduser(target.strip()))
    plan = MovePlan(source=source, target=target)
    if os.path.normcase(source) == os.path.normcase(target):
        return plan  # already there
    for name in DATA_DIRS:
        base = os.path.join(source, name)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            rel = os.path.relpath(dirpath, source)
            for fn in files:
                src = os.path.join(dirpath, fn)
                dst = os.path.join(target, rel, fn)
                if os.path.exists(dst):
                    if _same_file(src, dst):
                        plan.same.append(dst)
                    else:
                        plan.conflicts.append(dst)
                        plan.copies.append((src, dst))
                else:
                    plan.copies.append((src, dst))
    return plan


def apply_move(plan: MovePlan, *, overwrite: bool = False) -> dict:
    """Copy files, then write the pointer. Without overwrite, conflicting files are kept as-is."""
    err = validate_target(plan.target)
    if err:
        raise ValueError(err)
    os.makedirs(plan.target, exist_ok=True)
    conflicts = set(plan.conflicts)
    copied = skipped = 0
    for src, dst in plan.copies:
        if dst in conflicts and not overwrite:
            skipped += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    for name in ("config", "cookies", "logs", "context", "memories", "assets"):
        os.makedirs(os.path.join(plan.target, name), exist_ok=True)
    pointer = paths.write_pointer(plan.target)
    return {"copied": copied, "skipped": skipped, "same": len(plan.same), "pointer": pointer}


def guard_paths() -> list[str]:
    """Folders that update/uninstall code must never delete: the pointed data folder."""
    out = []
    p = paths.read_pointer()
    if p:
        out.append(p)
    env = (os.environ.get("ECHE_USER_ROOT") or "").strip()
    if env and os.path.isdir(env):
        out.append(os.path.abspath(env))
    return out
