# context_summarizer.py

from core.context_manager import ensure_context_channel
from core.client import call_groq_raw
from core.summarizer_prompt import (
    build_condense_prompt,
    build_summary_prompt,
    get_summarizer_model,
)

# How many most-recent lines to keep verbatim in "New:" block
RECENT_MESSAGE_COUNT = 2  # last few turns (USER/SEVIN)


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
    <last few USER/SEVIN lines verbatim>
    """

    # ---------------------------------------------------------
    # 1. Ensure channel + pinned exist
    # ---------------------------------------------------------
    # If summarizing Sevin, use the Sevin memory channel
    if override_header:
        from core.bot_memory import ensure_bot_memory_channel
        channel, pinned = await ensure_bot_memory_channel(bot)
    else:
        channel, pinned = await ensure_context_channel(
            bot,
            guild,
            user_id,
            username
        )


    content = pinned.content or ""

    # ---------------------------------------------------------
    # 2. Determine header
    # ---------------------------------------------------------
    if override_header:
        header = override_header
    else:
        if username:
            header = f"Context for {username}:\n"
        else:
            header = "Context initialized.\n"

    if not content.startswith(header):
        # If corrupted or different, reset to clean header
        content = header

    # Strip header from the rest
    body = content[len(header):].lstrip("\n")

    # ---------------------------------------------------------
    # 3. Parse existing content into summary + messages
    # ---------------------------------------------------------
    # We expect something like:
    #
    # Summary:
    # <existing summary>
    #
    # New:
    # USER: ...
    # SEVIN: ...
    #
    # If it's not in this format, we treat everything as "messages"
    # and let this run once to normalize it.

    summary_block = ""
    message_lines: list[str] = []

    if "Summary:" in body and "\nNew:\n" in body:
        # Split into summary + new
        before_new, new_block = body.split("\nNew:\n", 1)

        # Remove leading "Summary:" from before_new
        if before_new.startswith("Summary:\n"):
            summary_block = before_new[len("Summary:\n") :].strip("\n")
        else:
            summary_block = before_new.strip("\n")

        # New block lines
        message_lines = [line for line in new_block.splitlines() if line.strip()]
    else:
        # No recognizable structure yet; treat entire body as raw messages
        # and let this run once to normalize.
        message_lines = [line for line in body.splitlines() if line.strip()]

    # ---------------------------------------------------------
    # 4. Separate "history" vs "recent" messages
    # ---------------------------------------------------------
    # For Sevin or general context, if override_header is provided (Sevin), we keep RECENT_MESSAGE_COUNT (2).
    # Otherwise, we check if we should summarize. But wait, user requirement states:
    # "Triggers the summarizer every third message" and "save the two most recent messages in a rolling buffer".
    # When triggered every 3rd message, exactly 1 message rolls out of the buffer (or more depending on accumulation).
    # Let's ensure RECENT_MESSAGE_COUNT is 2 and we only summarize when len(message_lines) > RECENT_MESSAGE_COUNT (i.e. >= 3).
    if len(message_lines) <= RECENT_MESSAGE_COUNT:
        # Not enough lines yet — normalize layout only
        recent_lines = message_lines
        history_lines = []
        if not history_lines and not summary_block:
            new_content = (
                header
                + "Summary:\n"
                + (summary_block.strip() or "(none yet)")
                + "\n\nNew:\n"
                + ("\n".join(recent_lines) + ("\n" if recent_lines else ""))
            )
            if len(new_content) > 1990:
                new_content = new_content[:1990]
            try:
                await pinned.edit(content=new_content)
            except Exception:
                pass
            return summary_block.strip() or "(none yet)"
    history_lines = message_lines[:-RECENT_MESSAGE_COUNT]
    recent_lines = message_lines[-RECENT_MESSAGE_COUNT:]

    # If there's nothing to summarize yet, just normalize layout and return
    if not history_lines and not summary_block:
        new_content = (
            header
            + "Summary:\n"
            + "(none yet)\n\n"
            + "New:\n"
            + ("\n".join(recent_lines) + ("\n" if recent_lines else ""))
        )

        if len(new_content) > 1990:
            new_content = new_content[:1990]

        await pinned.edit(content=new_content)
        return "(none yet)"

    # ---------------------------------------------------------
    # 5. Build summarization prompt (with condensing check if summary is too long)
    # ---------------------------------------------------------
    history_text = "\n".join(history_lines).strip()
    existing_summary = summary_block.strip()

    if not history_text and existing_summary:
        # Nothing new to summarize, just normalize layout
        new_content = (
            header
            + "Summary:\n"
            + existing_summary
            + "\n\nNew:\n"
            + ("\n".join(recent_lines) + ("\n" if recent_lines else ""))
        )

        if len(new_content) > 1990:
            new_content = new_content[:1990]

        await pinned.edit(content=new_content)
        return existing_summary or "(none yet)"

    # Check if existing summary exceeds character limit (e.g., 1000 chars)
    # If so, ask LLM to condense/compress the entire summary first.
    SUMMARY_CHAR_LIMIT = 1000
    sum_model = get_summarizer_model()
    if existing_summary and len(existing_summary) > SUMMARY_CHAR_LIMIT:
        print(
            f"[context_summarizer] Summary exceeded char limit "
            f"({len(existing_summary)} > {SUMMARY_CHAR_LIMIT}), condensing..."
        )
        condense_prompt = build_condense_prompt(existing_summary)
        try:
            condensed_text = await call_groq_raw(condense_prompt, model=sum_model)
            if (
                condensed_text
                and len(condensed_text.strip()) > 0
                and not condensed_text.startswith("ERROR")
            ):
                existing_summary = condensed_text.strip()
                print(
                    f"[context_summarizer] Successfully condensed summary to "
                    f"{len(existing_summary)} chars."
                )
        except Exception as e:
            print(f"[context_summarizer] ERROR condensing summary: {e}")

    # Combine existing summary + new history into a single text to compress
    combined_for_summary = ""
    if existing_summary and existing_summary != "(none yet)":
        combined_for_summary += "Existing summary:\n" + existing_summary + "\n\n"
    if history_text:
        combined_for_summary += "Conversation details:\n" + history_text

    # Prompt template lives in config/summarizer_prompt.txt (editable in Settings)
    prompt = build_summary_prompt(combined_for_summary)

    # ---------------------------------------------------------
    # 6. Call LLM to get summary (optional dedicated model)
    # ---------------------------------------------------------
    try:
        summary_text = await call_groq_raw(prompt, model=sum_model)
        summary_text = (summary_text or "").strip()
    except Exception as e:
        print(f"[context_summarizer] ERROR calling LLM for user {user_id}: {e}")
        # Fallback: keep existing summary if any, otherwise minimal
        summary_text = existing_summary or "(summary unavailable)"

    if not summary_text:
        summary_text = existing_summary or "(summary unavailable)"

    # Truncate summary if needed to stay under Discord limit
    if len(summary_text) > 1500:
        summary_text = summary_text[:1500] + "\n...[truncated]"

    # ---------------------------------------------------------
    # 7. Rebuild pinned content with new summary + recent messages
    # ---------------------------------------------------------
    new_content = (
        header
        + "Summary:\n"
        + summary_text
        + "\n\nNew:\n"
        + ("\n".join(recent_lines) + ("\n" if recent_lines else ""))
    )

    if len(new_content) > 1990:
        new_content = new_content[:1990]

    try:
        await pinned.edit(content=new_content)
    except Exception as e:
        print(f"[context_summarizer] ERROR editing pinned for user {user_id}: {e}")

    return summary_text
