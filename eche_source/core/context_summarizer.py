# context_summarizer.py

from __future__ import annotations

import asyncio
import re

from core.context_manager import (
    ensure_context_channel,
    fit_memory_pin,
    parse_pin_sections,
    split_memory_pin,
)
from core.client import call_groq_raw, drop_safety_preamble, is_quota_error
from core.summarizer_prompt import (
    build_condense_prompt,
    build_merge_prompt,
    get_summarizer_model,
)

RECENT_MESSAGE_COUNT = 3
# Fold this many stored recent lines into long-term memory, then clear New:.
# One line is one turn on that pin: a USER line, or a BOT line.
# Used for both the user's context and the bot's self context.
ARCHIVE_AFTER_MESSAGES = 3
# Discord rejects a pin edit over 2000 characters. Compact the cloud before
# that, down to a size that still leaves room for the next short lines.
_PIN_SOFT = 1700
_CLOUD_TARGET = 900
_NEXT_LINES_RESERVE = 320

# Reject API failures and instruction-echo so they never become "memory"
_BAD_SUMMARY_MARKERS = (
    "rate limit",
    "http 429",
    "http 401",
    "http 404",
    "http 500",
    "groq sdk error",
    "groq (cloud) error",
    "package is not installed",
    "model not found",
    "your job:",
    "conversation to summarize",
    "now write a single",
    "material to compress",
    "output rules",
    "do not mention that this is a summary",
    "existing summary:",
    "existing memory:",
    "write the condensed summary",
    "do not retell",
    "one short line per subject",
    "do not drop a subject",
)


def pin_would_crowd(header: str, summary: str) -> bool:
    """True when the cloud should be compacted before Discord rejects the pin."""
    size = len(header or "") + len((summary or "").strip()) + _NEXT_LINES_RESERVE
    return size > _PIN_SOFT


_DROP_WORDS = frozenset(
    """
    a an the and or but of to for in on at by is are was were be been being
    has have had do does did with from that this these those it its they them
    their user bot said says told about into also just like some very
    not you your our his her she him who what when where which will would
    can could should than then there here over under out all any each
    """.split()
)


def _memory_terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {word for word in words if len(word) >= 4 and word not in _DROP_WORDS}


def summary_drops_existing(existing: str, updated: str) -> bool:
    """True when a merge kept less than about two fifths of the stored facts.

    A shorter wording that still names the same subjects is kept. A rewrite
    that only retells the new lines is rejected so the recent block stays.
    """
    old = _memory_terms(existing)
    if len(old) < 8:
        return False
    kept = old & _memory_terms(updated)
    return len(kept) * 5 < len(old) * 2


def partition_recent(message_lines: list[str], keep: int) -> tuple[list[str], list[str]] | None:
    """Split New: lines into (history to fold, lines to leave).

    None means there is nothing to fold yet, so the caller keeps every line.
    keep=0 folds every line and leaves the recent block empty.
    """
    if keep > 0 and len(message_lines) <= keep:
        return None
    if keep <= 0:
        return list(message_lines), []
    return list(message_lines[:-keep]), list(message_lines[-keep:])


def _is_bad_llm_output(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if t.upper().startswith("ERROR"):
        return True
    low = t.lower()
    if any(m in low for m in _BAD_SUMMARY_MARKERS):
        return True
    # Mostly a copy of the prompt job list
    if low.count("- ") >= 5 and "preserve" in low and "ignore" in low:
        return True
    return False


def _clean_summary_text(text: str) -> str:
    t = drop_safety_preamble(text or "")
    # Drop accidental fences / labels
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = re.sub(r"^(summary|memory|condensed summary)\s*:\s*", "", t, flags=re.I)
    return t.strip()


async def _llm_summary(prompt: str, model: str, *, retries: int = 3) -> str | None:
    """
    Call the model. A quota error is not retried here; the Groq client already
    waited once. Returns clean summary text or None (caller keeps prior summary).
    """
    last = ""
    for attempt in range(retries):
        try:
            raw = await call_groq_raw(prompt, model=model, max_completion_tokens=512)
        except Exception as e:
            print(f"[context_summarizer] LLM exception (try {attempt + 1}): {e}")
            last = str(e)
            await asyncio.sleep(1.5 * (attempt + 1))
            continue

        text = _clean_summary_text(raw or "")
        if _is_bad_llm_output(text):
            print(
                f"[context_summarizer] Rejecting bad LLM output (try {attempt + 1}): "
                f"{text[:160]!r}"
            )
            last = text
            # The chat client already waited and retried a quota error once.
            if is_quota_error(text):
                return None
            await asyncio.sleep(1.0)
            continue

        return text

    print(f"[context_summarizer] All LLM attempts failed. Last={last[:200]!r}")
    return None


async def compact_cloud(summary: str) -> str:
    """Shorten the long-term cloud. A failed pass keeps the text it was given."""
    text = (summary or "").strip()
    if not text or text in ("(none yet)", "(summary unavailable)"):
        return text or "(none yet)"
    condensed = await _llm_summary(
        build_condense_prompt(text, _CLOUD_TARGET),
        get_summarizer_model(),
    )
    if (
        condensed
        and len(condensed) < len(text)
        and not summary_drops_existing(text, condensed)
    ):
        print(
            f"[context_summarizer] Compacted cloud from {len(text)} "
            f"to {len(condensed)} chars."
        )
        return condensed
    print("[context_summarizer] Cloud compact kept the existing summary.")
    return text


async def summarize_context(
    bot,
    guild,
    user_id,
    username=None,
    override_header: str | None = None,
    keep_recent: int | None = None,
):
    """
    Fold older New: lines into the long-term summary.

    keep_recent defaults to RECENT_MESSAGE_COUNT (leave that many verbatim).
    keep_recent=0 folds every stored line and leaves New: empty. The caller
    does that after a reply once three short lines are stored. Those lines
    become sentences in the cloud. The cloud is compacted before the pin
    reaches Discord's limit, and that pass keeps instructions and user info.

    Layout:

    <HEADER>
    Summary:
    <long-term summary of older messages>

    New:
    <last few USER/bot lines verbatim>
    """

    if override_header:
        from core.bot_memory import ensure_bot_memory_channel
        channel, pinned = await ensure_bot_memory_channel(bot, user_id, username)
    else:
        channel, pinned = await ensure_context_channel(
            bot,
            guild,
            user_id,
            username,
        )

    if not channel or not pinned:
        print("[context_summarizer] No memory channel — skipping archive.")
        return None

    from core.discord_store import refresh_record

    pinned = await refresh_record(pinned)
    content = pinned.content or ""

    if override_header:
        fallback = override_header
    elif username:
        fallback = f"Context for {username}:\n"
    else:
        fallback = "Context initialized.\n"

    # Keep the pin's own title when it already has a Summary block.
    _header, _body, recognized = split_memory_pin(content, fallback)
    if not recognized:
        print(
            f"[context_summarizer] Pin for {user_id} has no summary block — "
            "leaving it unchanged."
        )
        return None

    label, summary_block, message_lines = parse_pin_sections(content, fallback)

    # If prior summary was an error dump, treat as empty so we can recover
    if _is_bad_llm_output(summary_block) or summary_block.strip() in (
        "(none yet)",
        "(summary unavailable)",
    ):
        if _is_bad_llm_output(summary_block):
            print("[context_summarizer] Clearing previously stored bad summary text")
        summary_block = ""

    async def _write_layout(summary_text: str, recent: list[str]) -> str:
        s = (summary_text or "").strip() or "(none yet)"
        new_content = fit_memory_pin(label, s, recent)
        try:
            from core.discord_store import edit_record
            await edit_record(pinned, new_content)
        except Exception as e:
            print(f"[context_summarizer] ERROR editing pinned for user {user_id}: {e}")
        return s

    def _keep_prior(reason: str) -> tuple[str, list[str]]:
        # A failed fold puts the lines back into New: so they are not dropped.
        print(f"[context_summarizer] {reason}")
        # Every stored line goes back. The tail alone would drop the lines
        # that failed to fold when the caller left the last few in New:.
        return existing_summary or "(summary unavailable)", list(message_lines)

    keep = RECENT_MESSAGE_COUNT if keep_recent is None else max(0, int(keep_recent))
    parted = partition_recent(message_lines, keep)
    if parted is None:
        return await _write_layout(summary_block.strip() or "(none yet)", message_lines)

    history_lines, recent_lines = parted
    history_text = "\n".join(history_lines).strip()
    existing_summary = drop_safety_preamble(summary_block.strip())

    if not history_text:
        return await _write_layout(existing_summary or "(none yet)", recent_lines)

    sum_model = get_summarizer_model()
    print(f"[context_summarizer] Using model={sum_model}")

    # Merge first. Do not shorten the old cloud before the new facts land.
    summary_text = await _llm_summary(
        build_merge_prompt(existing_summary, history_text),
        sum_model,
    )
    if not summary_text or summary_drops_existing(existing_summary, summary_text):
        summary_text, recent_lines = _keep_prior(
            "Keeping previous summary; the merge missed stored facts"
            if summary_text
            else "Keeping previous summary; LLM did not return usable text"
        )
        return await _write_layout(summary_text, recent_lines)

    # Compact before the pin reaches Discord's limit, not after a line is cut.
    if pin_would_crowd(label, summary_text):
        print(
            f"[context_summarizer] Cloud is {len(summary_text)} chars, "
            f"near the pin limit. Compacting toward {_CLOUD_TARGET}."
        )
        summary_text = await compact_cloud(summary_text)

    return await _write_layout(summary_text, recent_lines)