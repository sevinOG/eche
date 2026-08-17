# context_summarizer.py

from __future__ import annotations

import asyncio
import re

from core.context_manager import ensure_context_channel
from core.client import call_groq_raw
from core.summarizer_prompt import (
    build_condense_prompt,
    build_summary_prompt,
    get_summarizer_model,
)

RECENT_MESSAGE_COUNT = 2
SUMMARY_CHAR_LIMIT = 1000
SUMMARY_MAX_STORE = 1500

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
    "write the condensed summary",
)


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
    t = (text or "").strip()
    # Drop accidental fences / labels
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = re.sub(r"^(summary|memory|condensed summary)\s*:\s*", "", t, flags=re.I)
    return t.strip()


async def _llm_summary(prompt: str, model: str, *, retries: int = 3) -> str | None:
    """
    Call the model; retry on rate-limit style failures.
    Returns clean summary text or None (caller keeps prior summary).
    """
    last = ""
    for attempt in range(retries):
        try:
            raw = await call_groq_raw(prompt, model=model)
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
            # Back off harder on rate limits
            delay = 3.0 * (attempt + 1) if "rate" in text.lower() or "429" in text else 1.0
            await asyncio.sleep(delay)
            continue

        return text

    print(f"[context_summarizer] All LLM attempts failed. Last={last[:200]!r}")
    return None


async def summarize_context(
    bot,
    guild,
    user_id,
    username=None,
    override_header: str | None = None,
):
    """
    Summarize all but the last few messages into a long-term summary,
    keep the most recent messages verbatim in a 'New:' block.

    Layout:

    <HEADER>
    Summary:
    <long-term summary of older messages>

    New:
    <last few USER/bot lines verbatim>
    """

    if override_header:
        from core.bot_memory import ensure_bot_memory_channel
        channel, pinned = await ensure_bot_memory_channel(bot)
    else:
        channel, pinned = await ensure_context_channel(
            bot,
            guild,
            user_id,
            username,
        )

    content = pinned.content or ""

    if override_header:
        header = override_header
    else:
        if username:
            header = f"Context for {username}:\n"
        else:
            header = "Context initialized.\n"

    if not content.startswith(header):
        content = header

    body = content[len(header) :].lstrip("\n")

    summary_block = ""
    message_lines: list[str] = []

    if "Summary:" in body and "\nNew:\n" in body:
        before_new, new_block = body.split("\nNew:\n", 1)
        if before_new.startswith("Summary:\n"):
            summary_block = before_new[len("Summary:\n") :].strip("\n")
        else:
            summary_block = before_new.strip("\n")
        message_lines = [line for line in new_block.splitlines() if line.strip()]
    else:
        message_lines = [line for line in body.splitlines() if line.strip()]

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
        new_content = (
            header
            + "Summary:\n"
            + s
            + "\n\nNew:\n"
            + ("\n".join(recent) + ("\n" if recent else ""))
        )
        if len(new_content) > 1990:
            new_content = new_content[:1990]
        try:
            await pinned.edit(content=new_content)
        except Exception as e:
            print(f"[context_summarizer] ERROR editing pinned for user {user_id}: {e}")
        return s

    if len(message_lines) <= RECENT_MESSAGE_COUNT:
        recent_lines = message_lines
        return await _write_layout(summary_block.strip() or "(none yet)", recent_lines)

    history_lines = message_lines[:-RECENT_MESSAGE_COUNT]
    recent_lines = message_lines[-RECENT_MESSAGE_COUNT:]

    if not history_lines and not summary_block:
        return await _write_layout("(none yet)", recent_lines)

    history_text = "\n".join(history_lines).strip()
    existing_summary = summary_block.strip()

    if not history_text and existing_summary:
        return await _write_layout(existing_summary, recent_lines)

    sum_model = get_summarizer_model()
    print(f"[context_summarizer] Using model={sum_model}")

    # Condense oversized summary first
    if existing_summary and len(existing_summary) > SUMMARY_CHAR_LIMIT:
        print(
            f"[context_summarizer] Summary exceeded char limit "
            f"({len(existing_summary)} > {SUMMARY_CHAR_LIMIT}), condensing..."
        )
        condensed = await _llm_summary(
            build_condense_prompt(existing_summary),
            sum_model,
        )
        if condensed:
            existing_summary = condensed
            print(
                f"[context_summarizer] Condensed summary to "
                f"{len(existing_summary)} chars."
            )

    combined_for_summary = ""
    if existing_summary and existing_summary not in ("(none yet)",):
        combined_for_summary += "Existing summary:\n" + existing_summary + "\n\n"
    if history_text:
        combined_for_summary += "Conversation details:\n" + history_text

    prompt = build_summary_prompt(combined_for_summary)

    summary_text = await _llm_summary(prompt, sum_model)
    if not summary_text:
        # Keep last good memory; never store ERROR / rate-limit / instructions
        summary_text = existing_summary or "(summary unavailable)"
        print(
            "[context_summarizer] Keeping previous summary; LLM did not return usable text"
        )

    if len(summary_text) > SUMMARY_MAX_STORE:
        summary_text = summary_text[:SUMMARY_MAX_STORE] + "\n...[truncated]"

    return await _write_layout(summary_text, recent_lines)