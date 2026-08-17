# core/summarizer_prompt.py
# Editable memory-compression prompt + model (Settings → Memory & Prompts).

from __future__ import annotations

import os

DEFAULT_SUMMARIZER_PROMPT = """You compress Discord chat into long-term memory for an assistant.

Output rules (mandatory):
- Reply with ONLY the memory summary text.
- No titles, labels, bullet lists of instructions, or phrases like "Your job", "Conversation to summarize", "Now write".
- Do not mention that this is a summary.
- Do not quote or restate these rules.
- Preserve important facts, preferences, relationships, goals, and durable context.
- Drop small talk and one-off noise.
- Keep it concise (prefer under 800 characters).

Material to compress:

{combined_for_summary}
"""

DEFAULT_CONDENSE_PROMPT = """Compress this long-term memory into a shorter form.

Output rules (mandatory):
- Reply with ONLY the condensed memory text.
- No labels, instructions, or meta commentary.
- Keep critical facts, preferences, relationships, and goals.
- Prefer under 600 characters.

Existing memory:
{existing_summary}
"""

# Prefer a live Groq model; never default to deprecated Llama 3.3 70B
DEFAULT_SUMMARIZER_MODEL = "qwen/qwen3.6-27b"
_DEPRECATED_MODEL_FRAGMENTS = (
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama-4-scout",
)


def _user_root() -> str:
    try:
        from core.paths import ensure_user_layout
        return ensure_user_layout()
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_summarizer_prompt_path() -> str:
    return os.path.join(_user_root(), "config", "summarizer_prompt.txt")


def summarizer_prompt_path(override: str | None = None) -> str:
    raw = (override or os.getenv("SUMMARIZER_PROMPT_PATH") or "").strip()
    if not raw:
        try:
            from core.secrets import load_all
            raw = (load_all(_user_root()).get("summarizer_prompt_path") or "").strip()
        except Exception:
            raw = ""
    if raw:
        if os.path.isabs(raw):
            return raw
        return os.path.join(_user_root(), raw)
    return default_summarizer_prompt_path()


def get_summarizer_prompt(override_path: str | None = None) -> str:
    path = summarizer_prompt_path(override_path)
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                return text
    except Exception:
        pass
    return DEFAULT_SUMMARIZER_PROMPT.strip()


def save_summarizer_prompt(text: str, override_path: str | None = None) -> str:
    path = summarizer_prompt_path(override_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    return path


def ensure_summarizer_prompt_file(override_path: str | None = None) -> str:
    path = summarizer_prompt_path(override_path)
    if not os.path.isfile(path):
        save_summarizer_prompt(DEFAULT_SUMMARIZER_PROMPT, override_path)
    return path


def _is_deprecated_model(name: str) -> bool:
    low = (name or "").strip().lower()
    if not low:
        return True
    return any(frag in low for frag in _DEPRECATED_MODEL_FRAGMENTS)


def get_summarizer_model() -> str:
    """
    Model used only for memory summarization.
    Order: SUMMARIZER_MODEL / settings → GROQ_MODEL → DEFAULT_SUMMARIZER_MODEL.
    Skips deprecated Groq model ids.
    """
    raw = (os.getenv("SUMMARIZER_MODEL") or "").strip()
    if not raw:
        try:
            from core.secrets import load_all
            raw = (load_all(_user_root()).get("summarizer_model") or "").strip()
        except Exception:
            raw = ""

    if raw and not _is_deprecated_model(raw):
        return raw

    chat = (os.getenv("GROQ_MODEL") or "").strip()
    if chat and not _is_deprecated_model(chat):
        return chat

    return DEFAULT_SUMMARIZER_MODEL


def build_summary_prompt(combined_for_summary: str) -> str:
    template = get_summarizer_prompt()
    if "{combined_for_summary}" in template:
        return template.replace("{combined_for_summary}", combined_for_summary)
    return template.rstrip() + "\n\n" + combined_for_summary


def build_condense_prompt(existing_summary: str) -> str:
    template = DEFAULT_CONDENSE_PROMPT.strip()
    return template.replace("{existing_summary}", existing_summary)