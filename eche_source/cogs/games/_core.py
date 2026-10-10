# games/_core.py
# Core registry implementation - no side effects on import
# This module is safe to import from anywhere without triggering auto-loading

GAME_REGISTRY = {}


def register_game(name, game_class):
    """Register a game class with the given name."""
    GAME_REGISTRY[name] = game_class


def release_view(view) -> None:
    """Stop a view that is leaving the message.

    Editing a message to a new view does not cancel the previous view.
    Its inactivity task keeps running and will close the live game.
    """
    if view is None:
        return
    try:
        if view.is_finished():
            return
        view.stop()
    except Exception:
        pass