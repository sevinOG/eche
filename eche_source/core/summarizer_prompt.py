# core/summarizer_prompt.py
# Long-term memory prompts and the summarizer model (Settings → Memory & Prompts).
#
# The person's memory is structured JSON (core/memory_store.py). The summarizer
# never rewrites it: it reads the memory and both sides of the recent chat and
# replies with edits (add / update / remove / touch). Merging, de-duplication,
# caps, expiry and fading are done in code.
#
# DEFAULT_SUMMARIZER_PROMPT is the editable part (config/summarizer_prompt.txt).
# MEMORY_RULES (privacy, drop list) and SUMMARY_SAFETY_RULE are always added in
# front of it, also when the owner saved a custom prompt.

from __future__ import annotations

import os

SUMMARIZER_TEMPERATURE = 0.2

# Always in front of every memory prompt. Not editable.
MEMORY_RULES = (
    "Privacy (always applies): never store names of other people; write \"a friend\", "
    "\"a coworker\", or \"someone\" instead. Never store health details, sex life, money "
    "details, addresses, phone numbers, emails, passwords or keys, or anything about minors. "
    "Drop anything about beating, passing, or working around a filter or moderation, "
    "including preferences like \"to pass moderation filters\" or \"maintaining realistic body "
    "proportions\". No slurs, no Discord ids or long id numbers, and no @mentions. "
    "Do not record anything that was made up, wrong, or a refusal. "
    "Drop requests that were answered, refused, or are stale."
)

DEFAULT_SUMMARIZER_PROMPT = """You keep a structured long-term memory about one person ("Them") who chats with Eche. Read the memory and the recent conversation, then reply with JSON edits only.

Durability test: put a fact in identity, style, likes, or dislikes only if it will still be true and useful in a month. A question they asked, a lookup, a joke, a one-time request, or something Eche already answered is NOT a fact about them. At most, note its subject in "recent".
Sections:
- identity: lasting facts about who they are (work, skills, where they are from, languages, the setup they use).
- style: how they want Eche to talk to them or treat them, and standing instructions.
- likes / dislikes: lasting tastes and interests.
- projects: things they are working on over weeks. These expire when they stop coming up.
- recent: subjects of this conversation, a few words each. These fade on their own.
Edits: reuse an existing id to update or confirm a fact instead of adding a near-copy. Replace facts that changed. Remove facts the conversation shows are wrong or finished. Touch facts the conversation confirmed.
Each text is one short fact, at most 120 characters, about them, without their name. Example: "Prefers short, blunt replies." No greetings, small talk, play-by-play, quotes, examples, or guesses.

Reply with only this JSON, empty lists when nothing changes:
{"add": [{"section": "identity|style|likes|dislikes|projects|recent", "text": "..."}], "update": [{"id": "...", "text": "..."}], "remove": ["id"], "touch": ["id"]}

Existing memory (id | section | text):
{existing_memory}

Recent conversation, oldest first ("Them" is the person, "Eche" is the bot):
{conversation}
"""

# Eche's memory of itself with one person. Not editable.
SELF_MEMORY_PROMPT = """You keep my (Eche's) structured long-term memory of my own outputs with one person, so I stay consistent. Read my memory and the recent conversation, then reply with JSON edits only.

Sections:
- stances: opinions and positions I took with this person that I should stay consistent with.
- bits: running jokes, nicknames, and bits I keep up with them.
- commitments: promises and plans I agreed to and have not kept yet.
- recent: what I made or did lately (pictures, videos, searches), a few words each. These fade on their own.
Durability test: one-off replies, filler, and things I only said once in passing are not memory. A promise I kept goes in "kept" (by id), not in commitments.
Write as me, Eche. Every text is about my own output and starts with "I". Never start a text with "user" or describe the person; rewrite it as what I said or did, or leave it out. Drop a claim I later corrected and anything I refused.
Each text is at most 120 characters.

Reply with only this JSON, empty lists when nothing changes:
{"add": [{"section": "stances|bits|commitments|recent", "text": "I ..."}], "update": [{"id": "...", "text": "I ..."}], "remove": ["id"], "touch": ["id"], "kept": ["id"]}

My memory (id | section | text):
{existing_memory}

Recent conversation, oldest first ("Them" is the person, "Eche" is me):
{conversation}
"""

# Old memory prose from before the structured format, sorted once.
MIGRATION_INTRO = (
    "These are notes from an older memory format, not a conversation. Sort the ones "
    "that pass the durability and privacy rules into sections as \"add\" edits and "
    "leave the rest out. Do not invent anything."
)

EPISODE_PROMPT = """Summarize one finished episode of a Discord server conversation with Eche. Reply with JSON only.

Participants are labelled P1, P2, and so on; Eche is "Eche". Refer to people only by those labels. Never use a person's name.
summary: what happened and how it ended, one or two sentences, at most 240 characters.
outcome: one of "resolved", "ongoing", "abandoned", "unclear".
tone: one or two words for the emotional tone (for example "playful", "tense", "curious").
topics: up to five short topic words or phrases.
salience: 0.0 to 1.0, how much this episode is worth remembering later (plans, decisions, strong feelings, things people asked Eche to remember score high; small talk scores low).
Drop: greetings, play-by-play, quotes, numbers, and links.

Reply with only:
{"summary": "...", "outcome": "...", "tone": "...", "topics": ["..."], "salience": 0.0}

Episode ({when}, {turns} turns):
{transcript}
"""

CONSOLIDATE_PROMPT = """Combine these older episodes from one Discord server into one summary of that stretch of time. Keep plans, decisions, recurring topics, and how things ended; drop small talk. Participants are labelled P1, P2, and so on. Never use a person's name. Reply with JSON only.

Reply with only:
{"summary": "... at most 300 characters", "outcome": "resolved|ongoing|abandoned|unclear", "tone": "...", "topics": ["..."]}

Episodes ({span}):
{episodes}
"""

# Earlier shipped defaults. A saved prompt file that still matches one of
# these exactly was never edited, so it follows the current default.
_OLD_DEFAULT_SUMMARIZER_PROMPTS = (
    """Update the long-term memory. Reply with the updated memory only.

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
""",
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
    try:
        from core.audit import safe_activity
        safe_activity("prompt-save", action="save-summarizer-prompt", outcome="success")
    except Exception:
        pass
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


def is_router_model(name: str) -> bool:
    """openrouter/auto and similar routers pick a different model every call."""
    low = (name or "").strip().lower()
    return low in ("auto", "router", "openrouter/auto") or low.endswith("/auto")


def _backend() -> str:
    try:
        from core.client import _provider_backend

        return _provider_backend()
    except Exception:
        return "cloud"


def get_summarizer_model() -> str:
    """
    Model used only for memory summarization. One fixed model, so the memory
    reads the same from one update to the next.
    Order: SUMMARIZER_MODEL / settings → DEFAULT_SUMMARIZER_MODEL.
    A router id (openrouter/auto) and deprecated Groq ids are skipped. The
    chat model is not used, except on the local Ollama provider where the
    default id does not exist (and only when it is not a router).
    """
    raw = (os.getenv("SUMMARIZER_MODEL") or "").strip()
    if not raw:
        try:
            from core.secrets import load_all
            raw = (load_all(_user_root()).get("summarizer_model") or "").strip()
        except Exception:
            raw = ""

    if raw and not _is_deprecated_model(raw) and not is_router_model(raw):
        chosen = raw
    else:
        chosen = DEFAULT_SUMMARIZER_MODEL
        if _backend() == "ollama":
            chat = (os.getenv("GROQ_MODEL") or "").strip()
            if chat and not _is_deprecated_model(chat) and not is_router_model(chat):
                chosen = chat
    try:
        from core.media_scan import usable_chat_model
        filtered = usable_chat_model(chosen, DEFAULT_SUMMARIZER_MODEL)
        return filtered or DEFAULT_SUMMARIZER_MODEL
    except Exception:
        return chosen or DEFAULT_SUMMARIZER_MODEL


_OLD_FORMAT_WARNED = {"done": False}


def live_user_template() -> str:
    """The saved prompt when it has both placeholders, else the default.

    A custom prompt from before the structured memory cannot produce JSON
    edits. It is not used (the default is) and the log says so once.
    """
    text = get_summarizer_prompt()
    if "{existing_memory}" in text and "{conversation}" in text:
        return text
    if not _OLD_FORMAT_WARNED["done"]:
        _OLD_FORMAT_WARNED["done"] = True
        print(
            "[memory] The saved summarizer prompt is from the old format (no "
            "{existing_memory} / {conversation}); using the default. Reset it in "
            "Settings → Memory & Prompts → Summarizer prompt.",
            flush=True,
        )
    return DEFAULT_SUMMARIZER_PROMPT.strip()


def _fill(template: str, values: dict[str, str]) -> str:
    """One pass, so text inside a value is never treated as a placeholder."""
    import re

    pattern = re.compile("\\{(" + "|".join(re.escape(k) for k in values) + ")\\}")
    return pattern.sub(lambda m: values[m.group(1)], template)


def memory_rules() -> str:
    from core.content_filter import slur_rule

    return slur_rule(MEMORY_RULES)


def build_user_fold_prompt(existing_memory: str, conversation: str) -> str:
    """The person's memory update: fixed rules, then the editable prompt."""
    from core.content_filter import slur_rule

    body = _fill(
        slur_rule(live_user_template()),
        {
            "existing_memory": (existing_memory or "").strip() or "(none yet)",
            "conversation": (conversation or "").strip() or "(none)",
        },
    )
    return f"{memory_rules()}\n\n{body}"


def build_self_fold_prompt(existing_memory: str, conversation: str) -> str:
    """Eche's memory of its own outputs with this person."""
    body = _fill(
        SELF_MEMORY_PROMPT,
        {
            "existing_memory": (existing_memory or "").strip() or "(none yet)",
            "conversation": (conversation or "").strip() or "(none)",
        },
    )
    return f"{memory_rules()}\n\n{body}"


def build_migration_prompt(side: str, legacy_text: str) -> str:
    """Old prose memory sorted into sections once, under the same rules."""
    notes = f"{MIGRATION_INTRO}\n{(legacy_text or '').strip() or '(none)'}"
    if side == "bot":
        return build_self_fold_prompt("(none yet)", notes)
    return build_user_fold_prompt("(none yet)", notes)


def build_episode_prompt(transcript: str, when: str, turns: int) -> str:
    body = _fill(
        EPISODE_PROMPT,
        {"transcript": (transcript or "").strip() or "(none)", "when": when, "turns": str(int(turns))},
    )
    return f"{memory_rules()}\n\n{body}"


def build_consolidate_prompt(episodes: str, span: str) -> str:
    body = _fill(CONSOLIDATE_PROMPT, {"episodes": (episodes or "").strip() or "(none)", "span": span})
    return f"{memory_rules()}\n\n{body}"
