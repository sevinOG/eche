"""Burn media files: overwrite, then unlink. Rejected output must not stay on disk.

`secure_delete` retries, and a path that still will not go is written to
`burn_pending.json` (paths only, never content) and retried on the next call
and at start-up. Failure is logged and never raised, and a rejected item is
never stored or shown, so a failed burn cannot reach Discord. Only the audit
row (a short sha256 of the item) is kept.

In-memory bytes that were never written are not copied to a new temp file
just to burn them; this module is for paths that already exist.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

_LOG = logging.getLogger("eche.burn")
_LOCK = threading.Lock()
CHUNK = 1 << 20


def _pending_file() -> Path:
    try:
        from core.paths import user_dir

        root = Path(user_dir())
    except Exception:
        root = Path(".")
    return root / "logs" / "burn_pending.json"


def pending() -> list[str]:
    try:
        rows = json.loads(_pending_file().read_text(encoding="utf-8"))
        return [str(item) for item in rows if item]
    except Exception:
        return []


def _write_pending(paths: list[str]) -> None:
    try:
        target = _pending_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        if paths:
            target.write_text(json.dumps(sorted(set(paths))), encoding="utf-8")
        elif target.exists():
            target.unlink()
    except Exception:
        _LOG.error("burn queue could not be written")


def _queue(path: str) -> None:
    with _LOCK:
        _write_pending(pending() + [path])


def _overwrite(path: str) -> None:
    size = os.path.getsize(path)
    with open(path, "r+b", buffering=0) as handle:
        for fill in (b"\x00", None):  # zeros, then random
            handle.seek(0)
            left = size
            while left > 0:
                n = min(CHUNK, left)
                handle.write(fill * n if fill else os.urandom(n))
                left -= n
            handle.flush()
            os.fsync(handle.fileno())
        handle.truncate(0)
        os.fsync(handle.fileno())


def _burn_once(path: str) -> None:
    if os.path.isfile(path) or os.path.islink(path):
        if os.path.islink(path):
            os.unlink(path)
            return
        try:
            _overwrite(path)
        finally:
            os.unlink(path)


def secure_delete(path, *, retries: int = 3, delay: float = 0.25, sleep=time.sleep, queue: bool = True) -> bool:
    """Overwrite then remove one file. True when it is gone and was overwritten (or never existed)."""
    path = str(path)
    for attempt in range(max(1, retries)):
        try:
            if not os.path.lexists(path):
                return True
            _burn_once(path)
            if not os.path.lexists(path):
                return True
        except Exception as exc:
            _LOG.error("burn failed (try %s): %s %s", attempt + 1, type(exc).__name__, os.path.basename(path))
            if not os.path.lexists(path):
                return False
        if attempt + 1 < retries:
            sleep(delay * (attempt + 1))
    if queue and os.path.lexists(path):
        _queue(path)
    return False


def secure_delete_tree(path, **kwargs) -> bool:
    """Burn every file under a folder, then remove the folders."""
    path = str(path)
    if not os.path.lexists(path):
        return True
    if os.path.isfile(path) or os.path.islink(path):
        return secure_delete(path, **kwargs)
    ok = True
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            ok = secure_delete(os.path.join(root, name), **kwargs) and ok
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                ok = False
    try:
        os.rmdir(path)
    except OSError:
        ok = False
        if kwargs.get("queue", True):
            _queue(path)
    return ok


def retry_pending(**kwargs) -> int:
    """Burn what an earlier attempt could not. Returns how many are still stuck."""
    with _LOCK:
        left = []
        for item in pending():
            if os.path.isdir(item):
                done = secure_delete_tree(item, queue=False, **kwargs)
            else:
                done = secure_delete(item, queue=False, **kwargs)
            if not done and os.path.lexists(item):
                left.append(item)
        _write_pending(left)
        return len(left)


def burn_rejected(sha256: str, source: str, *paths, reason: str = "") -> bool:
    """A check blocked this item: burn every file copy of it and keep only an
    audit row with the hash. True when nothing is left."""
    ok = True
    for item in paths:
        if item:
            ok = secure_delete_tree(item) and ok
    ok = retry_pending() == 0 and ok
    try:
        from core.audit import record

        record(
            "asset burned",
            source=source or "unknown",
            sha256=str(sha256)[:16],
            reason=reason,
            result="ok" if ok else "pending",
            safety=True,
        )
    except Exception:
        pass
    return ok
