# core/summarizer_prompt.py
# Editable memory-compression prompt + model (Settings → Memory & Prompts).

from __future__ import annotations

import os

DEFAULT_SUMMARIZER_PROMPT = """You are a memory compression system for a Discord assistant.

Your job:
- Read the conversation history.
- Preserve important facts, preferences, relationships, goals, and long-term context.
- Ignore small talk, filler, and one-off details that don't matter long-term.
- Write a concise but rich long-term memory summary preserving the existing data.
- DO NOT include the most recent few messages; those are kept verbatim elsewhere.

Conversation to summarize (older context, not including the most recent turns):

{combined_for_summary}

Now write a single long-term memory summary that captures everything important so far.
Do NOT mention that this is a summary. Just write the memory itself.
"""

DEFAULT_CONDENSE_PROMPT = """You are a memory condensation system for a Discord assistant.
The current long-term memory summary has grown too long.

Your job:
- Read the existing summary below.
- Condense and synthesize it into a shorter, highly compact summary that retains all critical facts, preferences, relationships, and goals.
- Remove redundancies and minor details.

Existing Summary:
{existing_summary}

Write the condensed summary:
"""


def _user_root() -> str:
    try:
        from core.paths import ensure_user_layout
        return ensure_user_layout()
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_summarizer_prompt_path() -> str:
    return os.path.join(_user_root(), "config", "summarizer_prompt.txt")


def summarizer_prompt_path(override: str | None = None) -> str:
    """
    Resolve the prompt file path.
    Settings key summarizer_prompt_path may point at a custom file;
    blank → config/summarizer_prompt.txt in the package.
    """
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


def get_summarizer_model() -> str:
    """
    Model used only for memory summarization.
    Falls back to main chat model (GROQ_MODEL), then default llama.
    """
    raw = (os.getenv("SUMMARIZER_MODEL") or "").strip()
    if not raw:
        try:
            from core.secrets import load_all
            raw = (load_all(_user_root()).get("summarizer_model") or "").strip()
        except Exception:
            raw = ""
    if raw:
        return raw
    chat = (os.getenv("GROQ_MODEL") or "").strip()
    if chat and "llama-4-scout" not in chat.lower():
        return chat
    return "llama-3.3-70b-versatile"


def build_summary_prompt(combined_for_summary: str) -> str:
    template = get_summarizer_prompt()
    if "{combined_for_summary}" in template:
        return template.replace("{combined_for_summary}", combined_for_summary)
    # User wiped the placeholder — append data so it still works
    return template.rstrip() + "\n\n" + combined_for_summary


def build_condense_prompt(existing_summary: str) -> str:
    template = DEFAULT_CONDENSE_PROMPT.strip()
    return template.replace("{existing_summary}", existing_summary)
