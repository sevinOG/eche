# games/registry.py
# Auto-discovers game modules in this package so drag-dropped files register.
# Uses _core for the actual registry to avoid circular imports during autoload.

from __future__ import annotations

import importlib
import pkgutil
import sys

from cogs.games._core import GAME_REGISTRY, register_game

# Explicit fallback list for frozen builds (PyInstaller), where
# pkgutil.iter_modules() cannot see bundled submodules. Keep new games
# added here so they still register in Echelon.exe. Harmless in source mode
# (modules are cached after the first import).
_KNOWN_GAMES = ("highlow", "slots", "simon")


def _autoload_games():
    """Import every submodule in cogs.games except this registry and _core."""
    package_name = __name__.rsplit(".", 1)[0]  # cogs.games
    prefix = package_name + "."
    discovered = set()

    try:
        package = sys.modules.get(package_name) or importlib.import_module(package_name)
        paths = list(getattr(package, "__path__", []) or [])
    except Exception:
        paths = []

    # Source mode: discover drag-dropped modules dynamically.
    for mod in pkgutil.iter_modules(paths):
        if mod.name in ("registry", "_core") or mod.name.startswith("_"):
            continue
        discovered.add(mod.name)

    # Frozen mode: pkgutil finds nothing, so fall back to the known list.
    for name in (*discovered, *_KNOWN_GAMES):
        full = prefix + name
        if full in sys.modules:
            continue
        try:
            importlib.import_module(full)
        except Exception as e:
            print(f"[games.registry] failed to import {full}: {e}")


_autoload_games()
