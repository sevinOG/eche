# core/summarizer_prompt.py
# Editable memory-compression prompt + model (Settings → Memory & Prompts).

from __future__ import annotations

import os

DEFAULT_SUMMARIZER_PROMPT = """Update the long-term memory. Reply with the updated memory only.

Keep: who the person is, their preferences, how they want to be treated, instructions they gave, decisions, and open threads.
Drop: greetings, small talk, play-by-play of the chat, quotes, examples, jokes, and guesses.
Drop anything about beating, passing, or working around a filter or moderation, including preferences like "to pass moderation filters" or "maintaining realistic body proportions".
No slurs, ids, or @mentions. Skip anything made up, wrong, or refused, and requests that were answered or are stale.
- Merge the new lines in. One bullet per subject; add to a matching bullet instead of starting a new one.
- Replace facts that changed. Keep older facts that still hold.
- No dates unless the date itself matters.
Format: short "- " bullets, each ending with a period. No headings, no intro.
Length: at most {char_limit} characters.

Existing memory:
{existing_summary}

New lines:
{new_lines}
"""

DEFAULT_CONDENSE_PROMPT = """Shorten this memory to at most {char_limit} characters. Reply with the shortened memory only.

Keep: who the person is, preferences, how they want to be treated, instructions they gave, decisions, and open threads.
Merge duplicates. Drop one-off chatter, examples, and repeated wording.
Drop anything about beating, passing, or working around a filter or moderation, including "to pass moderation filters" and "maintaining realistic body proportions".
No slurs, no Discord ids or long id numbers, and no @mentions. Drop refusals, made-up facts, and requests that were answered, refused, or are stale.
Format: short "- " bullets, each ending with a period. No headings, no intro.

Memory:
{existing_summary}
"""

# Earlier shipped defaults. A saved prompt file that still matches one of
# these exactly was never edited, so it follows the current default.
_OLD_DEFAULT_SUMMARIZER_PROMPTS = (
    """Update the long-term memory. Reply with the updated memory only.

Keep: who the person is, their preferences, how they want to be treated, instructions they gave, decisions, and open threads.
Drop: greetings, small talk, play-by-play of the chat, quotes, examples, jokes, and guesses.
No slurs, ids, or @mentions. Skip anything made up, wrong, or refused, and requests that were answered or are stale.
- Merge the new lines in. One bullet per subject; add to a matching bullet instead of starting a new one.
- Replace facts that changed. Keep older facts that still hold.
- No dates unless the date itself matters.
Format: short "- " bullets, each ending with a period. No headings, no intro.
Length: at most {char_limit} characters.

Existing memory:
{existing_summary}

New lines:
{new_lines}
""",
    """Update the long-term memory. Reply with the updated memory only.

Keep: who the person is, their preferences, how they want to be treated, instructions they gave, decisions, and open threads.
Drop: greetings, small talk, play-by-play of the chat, quotes, examples, jokes, and guesses.
- Merge the new lines in. One bullet per subject; add to a matching bullet instead of starting a new one.
- Replace facts that changed. Keep older facts that still hold.
- No dates unless the date itself matters.
Format: short "- " bullets, each ending with a period. No headings, no intro.
Length: at most {char_limit} characters.

Existing memory:
{existing_summary}

New lines:
{new_lines}
""",
    """Update the long-term memory cloud. Reply with only the cloud.

Rules:
- The new lines are short notes. Fold them into a few sentences. Do not copy the notes one by one.
- Combine notes about what was said into one sentence.
- Example: notes say the user talked about polar bears, linux, and a bar. Write "user talked about the location of polar bears, the security of linux OS, and whether a local bar was closed".
- Example: notes say eche talked about those topics. Write "eche talked about polar bears, cyber security, and local bar closing times".
- Keep who the person is, how they want to be treated, and any instruction they gave. Do not drop those to make room.
- If a new note is about a subject already in the cloud, add it on that sentence. Do not start a second sentence for the same subject.
- Keep earlier sentences that later talks still need.
- No dates, unless the date itself matters. No bullet list.

Existing memory:
{existing_summary}

New lines:
{new_lines}
""",
)

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


def is_old_default(text: str) -> bool:
    """True for an unedited copy of an earlier default prompt."""
    body = " ".join((text or "").split())
    return any(body == " ".join(old.split()) for old in _OLD_DEFAULT_SUMMARIZER_PROMPTS)


def get_summarizer_prompt(override_path: str | None = None) -> str:
    path = summarizer_prompt_path(override_path)
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text and is_old_default(text):
                # Never edited: move it to the current default.
                try:
                    save_summarizer_prompt(DEFAULT_SUMMARIZER_PROMPT, override_path)
                except Exception:
                    pass
                return DEFAULT_SUMMARIZER_PROMPT.strip()
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
    from core.content_filter import slur_rule

    template = slur_rule(get_summarizer_prompt())
    if "{combined_for_summary}" in template:
        return template.replace("{combined_for_summary}", combined_for_summary)
    return template.rstrip() + "\n\n" + combined_for_summary


def build_merge_prompt(
    existing_summary: str,
    new_lines: str,
    char_limit: int = 1400,
) -> str:
    """Ask for an in-place update. Older sheets still receive both parts.

    {char_limit} in a custom prompt file is the length target.
    """
    from core.content_filter import clean_memory_text, slur_rule

    template = slur_rule(get_summarizer_prompt()).replace("{char_limit}", str(int(char_limit)))

    existing = clean_memory_text((existing_summary or "").strip()) or "(none yet)"
    fresh = clean_memory_text((new_lines or "").strip()) or "(none)"
    if "{existing_summary}" in template or "{new_lines}" in template:
        return (
            template.replace("{existing_summary}", existing).replace("{new_lines}", fresh)
        )
    combined = "Existing memory:\n" + existing + "\n\nNew lines:\n" + fresh
    return build_summary_prompt(combined)


def build_condense_prompt(existing_summary: str, char_limit: int = 1400) -> str:
    from core.content_filter import clean_memory_text, slur_rule

    template = slur_rule(DEFAULT_CONDENSE_PROMPT.strip())

    text = template.replace(
        "{existing_summary}", clean_memory_text((existing_summary or "").strip())
    )
    return text.replace("{char_limit}", str(int(char_limit)))