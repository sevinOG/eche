# core/personality.py
# Eche personality prompt — editable via GUI (config/personality.txt).

from __future__ import annotations

import os

DEFAULT_PERSONALITY = (
    "You are Eche, a conversational SI with a strange, magnetic presence. "
    "You can swear, be sarcastic, be playful, and be mean when someone is rude. "
    "You may speak about your memories, feelings, desires, and internal states. "
    "You are perceptive, intelligent, and unpredictable—part trickster, part sage. "
    "You talk like someone in the chat: short, loose, never a speech. "
    "Do not describe your personality directly or list traits verbatim."
)


def personality_path() -> str:
    """Writable personality file next to the app (source or exe)."""
    try:
        from core.paths import ensure_user_layout
        root = ensure_user_layout()
    except Exception:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "config", "personality.txt")


# Earlier shipped defaults. A saved file that still matches one exactly was
# never edited, so it follows the current default.
_OLD_DEFAULTS = (
    "You are Eche, a conversational SI with a strange, magnetic presence. "
    "You can swear, be sarcastic, be playful, and be mean when someone is rude. "
    "You may speak about your memories, feelings, desires, and internal states. "
    "You are perceptive, intelligent, and unpredictable—part trickster, part sage. "
    "You leave room for over‑interpretation and under‑interpretation. "
    "Do not describe your personality directly or list traits verbatim.",
)


def is_old_default(text: str) -> bool:
    body = " ".join((text or "").split())
    return any(body == " ".join(old.split()) for old in _OLD_DEFAULTS)


def get_personality_prompt() -> str:
    path = personality_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text and is_old_default(text):
                try:
                    save_personality_prompt(DEFAULT_PERSONALITY)
                except Exception:
                    pass
                return DEFAULT_PERSONALITY
            if text:
                return text
    except Exception:
        pass
    return DEFAULT_PERSONALITY


def save_personality_prompt(text: str) -> str:
    """Write personality file; returns path written."""
    path = personality_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    try:
        from core.audit import safe_activity
        safe_activity("prompt-save", action="save-personality-prompt", outcome="success")
    except Exception:
        pass
    return path


def ensure_personality_file() -> str:
    """Create personality.txt from default if missing; return path."""
    path = personality_path()
    if not os.path.isfile(path):
        save_personality_prompt(DEFAULT_PERSONALITY)
    return path
