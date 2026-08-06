"""Optional debug logging. Enable with environment variable ECHE_DEBUG=1."""
from __future__ import annotations

import os
import sys


def debug_enabled() -> bool:
    v = (os.environ.get("ECHE_DEBUG") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def dprint(*args, **kwargs) -> None:
    """print() only when ECHE_DEBUG is set. Always flushes for bot subprocess logs."""
    if not debug_enabled():
        return
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


def dprint_err(*args, **kwargs) -> None:
    if not debug_enabled():
        return
    kwargs.setdefault("file", sys.stderr)
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)
