from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from core.local_chat_memory import load_turns


def _strip_tags(text: str) -> str:
    text = text or ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip().strip("[]")
    return text


def _place_line() -> str:
    return "Private desktop chat, not Discord."


async def reply_local(user_text: str) -> str:
    user_text = (user_text or "").strip()
    if not user_text:
        return ""

    # Push settings (incl. Ollama mode + key) into this process for client.py
    try:
        from core.secrets import apply_to_environ, load_all
        from core.paths import ensure_user_layout
        root = ensure_user_layout()
        apply_to_environ(root, override_existing=True)
        cfg = load_all(root)
    except Exception:
        pass

    # Ensure active model matches backend
    backend = (cfg.get("provider_backend") or "cloud").strip().lower()
    if backend == "ollama":
        os.environ["ECHE_PROVIDER"] = "ollama"
        model = (cfg.get("ollama_model") or cfg.get("groq_model") or "llama3").strip()
        if model:
            os.environ["GROQ_MODEL"] = model
        # Ollama does not need a real key
        if not (os.environ.get("GROQ_API_KEY") or "").strip():
            os.environ["GROQ_API_KEY"] = "ollama"
    elif backend == "openrouter":
        os.environ["ECHE_PROVIDER"] = "openrouter"
        model = (
            cfg.get("openrouter_model") or cfg.get("groq_model") or "openrouter/free"
        ).strip()
        if model:
            os.environ["GROQ_MODEL"] = model
        or_key = (cfg.get("openrouter_api_key") or "").strip()
        if or_key:
            os.environ["OPENROUTER_API_KEY"] = or_key
    else:
        os.environ["ECHE_PROVIDER"] = "cloud"
        model = (cfg.get("cloud_model") or cfg.get("groq_model") or "").strip()
        if model:
            os.environ["GROQ_MODEL"] = model

    turns = load_turns(limit=40)
    history_lines = []
    for t in turns[-20:]:
        role = (t.get("role") or "user").upper()
        content = (t.get("content") or "").strip()
        if content:
            history_lines.append(f"{role}: {content}")

    flat = (
        _place_line()
        + "\n\n=== LOCAL HISTORY ===\n"
        + "\n".join(history_lines)
        + f"\n\nUSER: {user_text}\n"
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