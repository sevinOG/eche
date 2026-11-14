# games/registry.py
# Import only the known bundled games. Do not auto-import every file in
# this folder: a drop-in module could be a leftover generator.
from __future__ import annotations

import importlib
import sys

from cogs.games._core import GAME_REGISTRY, register_game

# Bundled games. New first-party games must be added here explicitly.
_KNOWN_GAMES = ("highlow", "slots", "simon", "holdem")


def _autoload_games():
    """Import the explicit bundled game list after a media/denylist check."""
    package_name = __name__.rsplit(".", 1)[0]  # cogs.games
    prefix = package_name + "."
    try:
        from core.media_scan import module_is_blocked
    except Exception:
        module_is_blocked = lambda _name: ""

    for name in _KNOWN_GAMES:
        full = prefix + name
        if full in sys.modules:
            continue
        blocked = module_is_blocked(full)
        if blocked:
            print(f"[games.registry] refused to import {full}: {blocked}")
            continue
        try:
            importlib.import_module(full)
        except Exception as e:
            print(f"[games.registry] failed to import {full}: {e}")


_autoload_games()
