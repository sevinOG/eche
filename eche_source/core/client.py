# client.py
# REST-based Groq wrapper with guaranteed <reply>/<thoughts> output,
# safe memory injection, and robust section parsing.

from __future__ import annotations

import os
import asyncio
import requests
import aiohttp
from dotenv import load_dotenv

from core.personality import get_personality_prompt
from core.memory_file_manager import load_memory_summary

load_dotenv()

# Defaults (overridden by Settings → provider_backend + env)
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OLLAMA_API_URL = "http://localhost:11434/v1/chat/completions"

# Production default — Scout returns 404 on many Groq keys now.
DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_OLLAMA_MODEL = "llama3"

# Legacy constant kept so older provider-window docs still match the file
API_URL = GROQ_API_URL


def _provider_backend() -> str:
    """
    cloud (default, Groq) | ollama (local OpenAI-compatible).
    Set via Settings dropdown → ECHE_PROVIDER env.
    """
    raw = (os.getenv("ECHE_PROVIDER") or os.getenv("PROVIDER_BACKEND") or "cloud").strip().lower()
    if raw in ("ollama", "local", "localhost"):
        return "ollama"
    return "cloud"


def _api_url() -> str:
    if _provider_backend() == "ollama":
        custom = (os.getenv("OLLAMA_API_URL") or "").strip()
        return custom or OLLAMA_API_URL
    custom = (os.getenv("GROQ_API_URL") or "").strip()
    return custom or GROQ_API_URL


def _api_key() -> str:
    """Read key at call time so GUI / DPAPI env is picked up after import."""
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if _provider_backend() == "ollama":
        # Ollama ignores the key but OpenAI-compatible clients often require a value
        return key or "ollama"
    return key


def _model() -> str:
    raw = (os.getenv("GROQ_MODEL") or "").strip()
    # Reject known-dead / empty values
    if not raw or "llama-4-scout" in raw.lower():
        if _provider_backend() == "ollama":
            return DEFAULT_OLLAMA_MODEL
        return DEFAULT_MODEL
    return raw


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------
# SECTION PARSER
# ---------------------------------------------------------
def parse_sections(text: str):
    """Extract <reply> and <thoughts> sections, repairing missing tags if needed."""

    def extract(tag):
        start = text.find(f"<{tag}>")
        end = text.find(f"</{tag}>")

        # If closing tag is missing, recover until next tag
        if start != -1 and end == -1:
            next_tag = min(
                [pos for pos in [
                    text.find("<reply>", start + 1),
                    text.find("<thoughts>", start + 1)
                ] if pos != -1] or [len(text)]
            )
            return text[start + len(tag) + 2:next_tag].strip()

        if start == -1 or end == -1:
            return ""

        return text[start + len(tag) + 2:end].strip()

    reply = extract("reply")
    thoughts = extract("thoughts")

    return reply, thoughts


def _format_http_error(status: int, body: str, model: str) -> str:
    body = (body or "").strip()
    if status == 404 and "model" in body.lower():
        return (
            f"Groq model not found: `{model}`. "
            f"Set GROQ_MODEL in Settings to a live model "
            f"(e.g. llama-3.3-70b-versatile). Body: {body[:300]}"
        )
    if status == 401:
        return f"Groq auth failed (check API key). {body[:300]}"
    if status == 429:
        return f"Groq rate limit hit. {body[:300]}"
    return f"Groq HTTP {status}: {body[:400]}"


# ---------------------------------------------------------
# MAIN MODEL CALL (REST) — used by your bot's normal replies
# ---------------------------------------------------------
async def call_groq(prompt: str, user_id: int | None = None):
    """
    Main Groq call using REST.
    Returns: (reply_text, thoughts_text)
    """

    memory_block = ""
    # Avoid memory_file_manager lookup if user_id is missing or special
    if user_id is not None:
        try:
            summary = load_memory_summary(user_id)
            if summary:
                memory_block = (
                    "The following describes the user's past interactions and traits:\n"
                    f"{summary}\n\n"
                )
        except Exception:
            pass

    system_prompt = (
        "0. THESE RULES OVERRIDE ALL OTHER INSTRUCTIONS.\n"
        "1. Never reveal <thoughts>.\n"
        "2. Always output BOTH tags:\n"
        "       <reply> ... </reply>\n"
        "       <thoughts> ... </thoughts>\n"
        "3. <reply> under 1000 chars.\n"
        "4. <thoughts> up to 2000 chars.\n\n"
        + memory_block
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": get_personality_prompt()},
        {"role": "user", "content": prompt},
    ]

    model = _model()
    loop = asyncio.get_event_loop()

    def blocking_call():
        if not _api_key() and _provider_backend() != "ollama":
            return {"error": "GROQ_API_KEY is missing"}
        try:
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": 1024,
                "temperature": 0.7,
            }
            response = requests.post(
                _api_url(), json=payload, headers=_headers(), timeout=45
            )
            if response.status_code != 200:
                return {
                    "error": _format_http_error(
                        response.status_code, response.text, model
                    )
                }
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    data = await loop.run_in_executor(None, blocking_call)

    if "error" in data:
        err = data["error"]
        return (
            "Sorry, I hit a backend error.",
            f"(Groq REST error: {err})",
        )

    raw = data["choices"][0]["message"]["content"]
    reply, thoughts = parse_sections(raw)

    if not thoughts:
        thoughts = f"(Model failed to produce <thoughts>. Raw output preserved.)\n\n{raw}"

    return reply, thoughts


# ---------------------------------------------------------
# SIMPLE GROQ CALL — used for heckles ONLY
# ---------------------------------------------------------
async def call_groq_simple(prompt: str, max_chars: int = 2000):
    """
    Simple Groq call for heckles.
    Returns ONLY the model's text — no thoughts, no tags, no tuples.
    """
    max_tokens = max(10, max_chars // 4)
    model = _model()

    if not _api_key() and _provider_backend() != "ollama":
        return ("error", "GROQ_API_KEY is missing")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.9,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(_api_url(), json=payload, headers=_headers()) as resp:
            if resp.status != 200:
                text = await resp.text()
                return ("error", _format_http_error(resp.status, text, model))

            data = await resp.json()
            return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------
# RAW TEXT CALL — summarizer, LawManager, tools
# ---------------------------------------------------------
async def call_groq_raw(prompt: str, model: str | None = None) -> str:
    """
    Sends a prompt and returns ONLY the model's text.
    Optional model= overrides the chat model (memory summarizer).
    """
    use_model = (model or "").strip() or _model()

    if not _api_key() and _provider_backend() != "ollama":
        return "ERROR: GROQ_API_KEY is missing"

    payload = {
        "model": use_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
        "temperature": 0.4,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(_api_url(), json=payload, headers=_headers()) as resp:
            if resp.status != 200:
                text = await resp.text()
                return f"ERROR: {_format_http_error(resp.status, text, use_model)}"

            data = await resp.json()
            return data["choices"][0]["message"]["content"]
