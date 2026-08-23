from future import annotations

import asyncio
import re
from typing import Any

from core.local_chat_memory import load_turns


def _strip_tags(text: str) -> str:
    text = text or ""
    # Remove XML tags completely
    text = re.sub(r'<[^>]+>', '', text)
    # Also remove any lingering formatting artifacts
    text = re.sub(r'\s+', ' ', text)
    text = text.strip().strip('[]')  # Remove potential square brackets from sanitization
    return text


def _system_prompt() -> str:
    return (
        "You are Eche in a private local desktop chat (not Discord). "
        "Reply in character as plain text only. No XML tags, no chain-of-thought, "
        "no meta commentary about prompts. Keep replies concise unless asked for detail."
    )


async def reply_local(user_text: str) -> str:
    user_text = (user_text or "").strip()
    if not user_text:
        return ""

    turns = load_turns(limit=40)
    history_lines = []
    for t in turns[-20:]:
        role = (t.get("role") or "user").upper()
        content = (t.get("content") or "").strip()
        if content:
            history_lines.append(f"{role}: {content}")

    flat = (
        _system_prompt()
        "\n\n=== LOCAL HISTORY ===\n"
        "\n".join(history_lines)
        f"\n\nUSER: {user_text}\n\nReply as Eche (plain text only):\n"
    )

    try:
        from core import client as cl

        out = await cl.call_groq(flat, user_id=None)
        if isinstance(out, tuple):
            text = out[0]
        else:
            text = out
        text = _strip_tags(str(text or ""))
        return text or "(empty model reply)"
    except Exception as e:
        return f"(local chat error: {e})"


def reply_local_sync(user_text: str) -> str:
    return asyncio.run(reply_local(user_text))