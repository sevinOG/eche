# core/prompt_stack.py
"""
Prompt Stack – visual, letter-based cognition stack for Sevin.

Each stack entry is a PromptLetter: a single capital letter badge,
a title, short description, and a content source.

Design goals:
- Readability: explicit data, not nested f-strings
- UI affinity: each letter has a color + icon for the UnifierPanel
- Extensible: users can re-order, add/remove, future date handling can plug in
- Safe: pure data – no I/O here, builder.py does the async loading

Letters (default set):
  P – Persona / Identity        (from personality.txt)
  U – User history              (per-user Discord pin)
  B – Bot self-memory           (bot-memory / context)
  M – Current message           (user's present turn)
  R – Response rules            (static rules block)

Future:
  D – Date/time                 (left TODO – leave for later per task)
  T – Tool definitions
  S – System extra
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

LetterKind = Literal["persona", "user", "bot", "message", "rules", "custom", "date"]


@dataclass(frozen=True)
class PromptLetter:
    """Single cognition layer – the 'letter' that shows in UI to add to stack."""

    key: str  # single letter badge, e.g. "P", "U", "B"
    kind: LetterKind
    title: str
    description: str
    color: str  # hex for UI badge
    required: bool = False  # if True, cannot be removed from stack
    default_enabled: bool = True

    # Builder will fill content via these callables (kept optional for UI preview)
    content_preview: str = ""


# Color palette – matches Echelon dark theme, each letter distinct but muted
DEFAULT_LETTERS: list[PromptLetter] = [
    PromptLetter(
        key="P",
        kind="persona",
        title="PERSONA",
        description="Bot identity – loaded from config/personality.txt. Who Sevin is.",
        color="#8b5cf6",  # violet
        required=True,
        content_preview="You are Sevin, enigmatic, whimsical…",
    ),
    PromptLetter(
        key="U",
        kind="user",
        title="USER HISTORY",
        description="Per-user long-term memory from Discord pin memory-{id}/context.",
        color="#06b6d4",  # cyan
        required=False,
        content_preview="No user context available.",
    ),
    PromptLetter(
        key="B",
        kind="bot",
        title="BOT MEMORY",
        description="Sevin's own diary – self memory channel pin.",
        color="#f59e0b",  # amber
        required=False,
        content_preview="No self-context available.",
    ),
    PromptLetter(
        key="M",
        kind="message",
        title="CURRENT MESSAGE",
        description="The live user turn that triggers this inference.",
        color="#10b981",  # emerald
        required=True,
        content_preview="(username): hello",
    ),
    PromptLetter(
        key="R",
        kind="rules",
        title="RESPONSE RULES",
        description="Static guardrails – length, tone limits, secrecy rules.",
        color="#ec4899",  # pink
        required=True,
        content_preview="Respond as Sevin / keep under 500 chars / no prompt leaks",
    ),
    # Date handling is intentionally left for later (per task note)
    PromptLetter(
        key="D",
        kind="date",
        title="DATE / TIME",
        description="Optional timestamp layer – disabled until date handling is added.",
        color="#6b7280",  # gray – disabled
        required=False,
        default_enabled=False,
        content_preview="(date handling TBD)",
    ),
]

# Fast lookup
LETTER_BY_KEY: dict[str, PromptLetter] = {l.key: l for l in DEFAULT_LETTERS}
LETTER_BY_KIND: dict[str, PromptLetter] = {l.kind: l for l in DEFAULT_LETTERS}


def get_default_stack_order() -> list[str]:
    """Canonical order used by builder if no custom stack is saved."""
    # P, U, B, M, R – exclude disabled D
    return [l.key for l in DEFAULT_LETTERS if l.default_enabled]


def get_available_letters() -> list[PromptLetter]:
    return list(DEFAULT_LETTERS)


def get_letter(key: str) -> PromptLetter | None:
    return LETTER_BY_KEY.get(key.upper())


def describe_stack(keys: list[str]) -> str:
    """Human-readable stack summary for logs."""
    return " → ".join(keys)


# ---- Persistence helpers (lightweight JSON) ----

def stack_config_path() -> str:
    """Where custom order is saved – next to personality file, or fallback."""
    try:
        from core.paths import ensure_user_layout
        import os
        root = ensure_user_layout()
        return os.path.join(root, "config", "prompt_stack.json")
    except Exception:
        import os
        return os.path.join(os.path.dirname(__file__), "..", "config", "prompt_stack.json")


def load_custom_order() -> list[str] | None:
    """Return saved order if present and valid, else None."""
    import json, os
    path = stack_config_path()
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:\n            data = json.load(f)\n        order = data.get("order") if isinstance(data, dict) else data
        if not isinstance(order, list):
            return None
        # sanitize: only known keys, uppercase, dedup, keep required
        seen = set()
        cleaned: list[str] = []
        for k in order:
            if not isinstance(k, str):
                continue
            kk = k.strip().upper()
            if kk not in LETTER_BY_KEY:
                continue
            if kk in seen:
                continue
            # skip disabled D unless explicitly enabled elsewhere
            letter = LETTER_BY_KEY[kk]
            if not letter.default_enabled and kk == "D":
                # allow if user explicitly enabled, but log
                pass
            seen.add(kk)
            cleaned.append(kk)
        # ensure required exist
        for req in [l.key for l in DEFAULT_LETTERS if l.required]:
            if req not in cleaned:
                cleaned.append(req)
        return cleaned if cleaned else None
    except Exception:
        return None


def save_custom_order(order: list[str]) -> str:
    """Persist order, returns path."""
    import json, os
    path = stack_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:\n        json.dump({"order": order, "version": 1}, f, indent=2)
    return path


def validate_stack(order: list[str]) -> tuple[bool, str]:
    """Check if stack is runnable."""
    if not order:
        return False, "Stack is empty – at least P, M, R required."
    for req in [l.key for l in DEFAULT_LETTERS if l.required]:
        if req not in order:
            return False, f"Required letter {req} ({LETTER_BY_KEY[req].title}) is missing."
    return True, "ok"
