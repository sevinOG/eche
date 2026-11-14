# core/denied_modules.py
# Media-generation modules that must never load, even if a leftover file
# is still on disk after an update. Names only — no generation endpoints.
# ?asset / Imagine is allowed on this branch through the official choke
# point. ?flash stays banned.

from __future__ import annotations

DENIED_MODULES = frozenset({
    "cogs.flash",
})

_DENIED_LEAVES = frozenset({
    "flash",
})


def normalize_module(name: str) -> str:
    text = (name or "").strip()
    if text.startswith("eche_ecosystem."):
        text = text[len("eche_ecosystem."):]
    if text.endswith(".py"):
        text = text[:-3]
    return text.replace("/", ".").replace("\\", ".")


def is_denied_module(name: str) -> bool:
    text = normalize_module(name)
    if not text:
        return False
    if text in DENIED_MODULES:
        return True
    if not text.startswith(("cogs.", "core.")):
        text = "cogs." + text
        if text in DENIED_MODULES:
            return True
    leaf = text.rsplit(".", 1)[-1]
    return leaf in _DENIED_LEAVES
