# core/denied_modules.py
# Media-generation modules that must never load, even if a leftover file
# is still on disk after an update. Names only — no generation endpoints.

from __future__ import annotations

DENIED_MODULES = frozenset({
    "cogs.asset",
    "cogs.flash",
    "core.imagine",
    "core.tools_asset",
    "core.asset_message",
})

_DENIED_LEAVES = frozenset({
    "asset",
    "flash",
    "imagine",
    "tools_asset",
    "asset_message",
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
