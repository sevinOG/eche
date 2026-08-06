# games/_core.py
# Core registry implementation - no side effects on import
# This module is safe to import from anywhere without triggering auto-loading

GAME_REGISTRY = {}


def register_game(name, game_class):
    """Register a game class with the given name."""
    GAME_REGISTRY[name] = game_class