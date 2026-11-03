# core/context_caps.py
# Every limit on how much history, memory, and context goes into a prompt.
#
# Each cap is a whole number in config/settings.json (Settings → Memory &
# Prompts → Context & Memory). Blank uses the default, which is the value
# Eche used before these were settings. An ECHE_* env var is the fallback
# when settings.json has no value. Out-of-range values are clamped.

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Cap:
    key: str          # settings.json key (also in core.secrets.PUBLIC_KEYS)
    label: str        # Settings label
    default: int
    low: int
    high: int
    step: int
    env: str          # env fallback
    unit: str         # "chars", "messages", "lines", "turns"
    hint: str         # one line under the slider
    zero_label: str = ""  # shown when the value is 0 (for caps where 0 means "no cap")


# Order is the order on the Settings page.
CAPS: tuple[Cap, ...] = (
    # --- What the reply prompt sees -------------------------------------
    Cap("memory_prompt_chars", "Whole memory prompt", 3500, 1000, 16000, 250,
        "ECHE_MEMORY_PROMPT_CHARS", "chars",
        "Total size of the memory part of a reply prompt. Summaries shrink first, the newest message is kept."),
    Cap("memory_summary_prompt_chars", "Long-term summary in the prompt", 600, 100, 1900, 50,
        "ECHE_MEMORY_SUMMARY_PROMPT_CHARS", "chars",
        "How much of each long-term summary (about the person, and about you with them) the reply sees."),
    Cap("episode_prompt_chars", "Server episode in the prompt", 600, 100, 1900, 50,
        "ECHE_EPISODE_PROMPT_CHARS", "chars",
        "How much of this server's episode summary the reply sees."),
    Cap("memory_recent_messages", "Recent messages kept", 3, 1, 12, 1,
        "ECHE_MEMORY_RECENT_MESSAGES", "messages",
        "Full recent messages kept word for word for each side (theirs, and your replies)."),
    Cap("memory_message_chars", "One remembered message", 0, 0, 4000, 50,
        "ECHE_MEMORY_MESSAGE_CHARS", "chars",
        "Longest single message (yours or theirs) kept in the recent-message buffer the prompt sees. 0 keeps whole messages.",
        zero_label="whole"),
    Cap("media_prompt_chars", "Attachments & embeds in the prompt", 1600, 200, 6000, 100,
        "ECHE_MEDIA_PROMPT_CHARS", "chars",
        "Text read from attachments, embeds, replies, and forwards for one turn."),
    Cap("attachment_text_chars", "One text file", 1000, 200, 8000, 100,
        "ECHE_ATTACHMENT_TEXT_CHARS", "chars",
        "How much of one attached text file is read."),
    Cap("media_memory_chars", "Attachment note in memory", 500, 50, 2000, 50,
        "ECHE_MEDIA_MEMORY_CHARS", "chars",
        "How much attachment text is stored with the message in memory."),
    # --- What the summarizer keeps ---------------------------------------
    Cap("memory_fold_every", "Messages per summary", 3, 1, 12, 1,
        "ECHE_MEMORY_FOLD_EVERY", "messages",
        "New messages before the long-term summary is updated. Higher means fewer summarizer calls."),
    Cap("memory_summary_chars", "Long-term summary length", 1900, 300, 1900, 50,
        "ECHE_MEMORY_SUMMARY_CHARS", "chars",
        "Size target for each person's long-term summary pin. Discord's limit is 2000."),
    Cap("episode_fold_after", "Server lines per episode summary", 5, 2, 20, 1,
        "ECHE_EPISODE_FOLD_AFTER", "lines",
        "Server lines collected before the episode summary is updated."),
    Cap("episode_summary_chars", "Server episode length", 1900, 600, 1900, 50,
        "ECHE_EPISODE_SUMMARY_CHARS", "chars",
        "Size target for each server's episode pin."),
    # --- Threads and the desktop chat --------------------------------------
    Cap("thread_notes_chars", "Thread search notes", 2000, 500, 8000, 250,
        "ECHE_THREAD_NOTES_CHARS", "chars",
        "Lookup results carried into each thread post. The newest text is kept."),
    Cap("thread_posted_chars", "Thread posts carried forward", 0, 0, 16000, 250,
        "ECHE_THREAD_POSTED_CHARS", "chars",
        "Earlier posts each new thread post sees. 0 sends all of them; otherwise the newest text is kept.",
        zero_label="all"),
    Cap("thread_topic_scan", "Thread topic look-back", 8, 1, 50, 1,
        "ECHE_THREAD_TOPIC_SCAN", "messages",
        "Earlier channel messages searched for the subject of a \"more room\" thread."),
    Cap("local_history_turns", "Desktop chat history", 20, 0, 100, 1,
        "ECHE_LOCAL_HISTORY_TURNS", "turns",
        "Earlier turns of the desktop chat sent with each message."),
)

BY_KEY: dict[str, Cap] = {cap.key: cap for cap in CAPS}
KEYS: tuple[str, ...] = tuple(cap.key for cap in CAPS)

# Reading settings.json on every call is cheap, but a reply reads several
# caps. Cache per (settings path, mtime, env value).
_CACHE: dict[str, tuple[tuple, int]] = {}


def clamp(key: str, raw) -> int:
    """A whole number inside the cap's range. Blank or junk is the default."""
    cap = BY_KEY[key]
    text = str(raw if raw is not None else "").strip()
    if not text:
        return cap.default
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        return cap.default
    return max(cap.low, min(cap.high, value))


def _settings_stamp() -> tuple[str, float | None]:
    try:
        from core.paths import user_dir
        from core.secrets import settings_path

        path = settings_path(user_dir())
        return path, (os.path.getmtime(path) if os.path.isfile(path) else None)
    except Exception:
        return "", None


def raw_value(key: str) -> str:
    """Saved text for a cap: settings.json, else the env var. Blank when neither."""
    cap = BY_KEY[key]
    raw = ""
    try:
        from core.secrets import public_value

        raw = public_value(key)
    except Exception:
        raw = ""
    if not raw:
        raw = (os.environ.get(cap.env) or "").strip()
    return raw


def get(key: str) -> int:
    """The live value of one cap."""
    cap = BY_KEY[key]
    path, stamp = _settings_stamp()
    env_raw = (os.environ.get(cap.env) or "").strip()
    stamp_key = (path, stamp, env_raw)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == stamp_key and path:
        return cached[1]
    value = clamp(key, raw_value(key))
    _CACHE[key] = (stamp_key, value)
    return value


def clear_cache() -> None:
    _CACHE.clear()


# English runs about four characters a token. The summarizer's output cap
# matches its character target with a little headroom, so the prompt's length
# target, not the token cap, decides where a summary ends.
SUMMARY_CHARS_PER_TOKEN = 4
_SUMMARY_TOKEN_HEADROOM = 16
SUMMARY_TOKEN_MIN = 32


def summary_tokens(char_target: int) -> int:
    """Summarizer max output tokens for a character target."""
    try:
        chars = max(0, int(char_target))
    except (TypeError, ValueError):
        chars = 0
    return max(SUMMARY_TOKEN_MIN, chars // SUMMARY_CHARS_PER_TOKEN + _SUMMARY_TOKEN_HEADROOM)
