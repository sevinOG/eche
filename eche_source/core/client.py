# client.py
# REST client for Cloud (Groq) and Local (Ollama / OpenAI-compatible).
# Reads provider settings at call time from env (ECHE_PROVIDER, GROQ_*, OLLAMA_*).
# Returns clear, provider-specific errors instead of a generic "Groq REST error".

from __future__ import annotations

import os
import asyncio
import requests
import aiohttp
from dotenv import load_dotenv

from core.personality import get_personality_prompt
from core.memory_file_manager import load_memory_summary

load_dotenv()

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OLLAMA_API_URL = "http://localhost:11434/v1/chat/completions"

DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_OLLAMA_MODEL = "llama3"

# Legacy alias (older docs / Provider window)
API_URL = GROQ_API_URL


# ---------------------------------------------------------------------------
# Runtime config (always re-read — GUI may change env between calls)
# ---------------------------------------------------------------------------
def _provider_backend() -> str:
    """
    cloud  → Groq
    ollama → local OpenAI-compatible (Ollama, LM Studio, etc.)
    """
    raw = (
        os.getenv("ECHE_PROVIDER")
        or os.getenv("PROVIDER_BACKEND")
        or "cloud"
    ).strip().lower()
    if raw in ("ollama", "local", "localhost"):
        return "ollama"
    return "cloud"


def _provider_label() -> str:
    return "Ollama (local)" if _provider_backend() == "ollama" else "Groq (cloud)"


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
        # Ollama ignores the key; OpenAI-compatible clients often still want a value
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


def _config_snapshot() -> str:
    """Short debug line for error messages."""
    return (
        f"backend={_provider_backend()} | "
        f"url={_api_url()} | "
        f"model={_model()} | "
        f"key={'set' if (_api_key() and _api_key() != 'ollama') else ('dummy' if _provider_backend() == 'ollama' else 'MISSING')}"
    )


# ---------------------------------------------------------------------------
# Error formatting — provider-aware
# ---------------------------------------------------------------------------
def _format_http_error(status: int, body: str, model: str, url: str) -> str:
    body = (body or "").strip()
    label = _provider_label()
    backend = _provider_backend()
    snippet = body[:400] if body else "(empty body)"

    # --- Shared HTTP codes with tailored advice ---
    if status == 401:
        if backend == "ollama":
            return (
                f"{label} auth rejected (HTTP 401). "
                f"Ollama usually does not need a real key — leave Provider API Key blank "
                f"or set it to `ollama`. URL={url} model=`{model}`. Body: {snippet}"
            )
        return (
            f"{label} auth failed (HTTP 401). "
            f"Check Provider API Key in Settings (GROQ_API_KEY). "
            f"model=`{model}`. Body: {snippet}"
        )

    if status == 404:
        body_l = body.lower()
        if "model" in body_l or "not found" in body_l:
            if backend == "ollama":
                return (
                    f"{label} model not found: `{model}`. "
                    f"Run `ollama list` and set Model ID in Settings to an exact tag "
                    f"(e.g. llama3, mistral). Pull with `ollama pull {model}` if needed. "
                    f"URL={url}. Body: {snippet}"
                )
            return (
                f"{label} model not found: `{model}`. "
                f"Set Model ID in Settings to a live Groq model "
                f"(e.g. llama-3.3-70b-versatile). Body: {snippet}"
            )
        if backend == "ollama":
            return (
                f"{label} HTTP 404 — wrong endpoint path? "
                f"Expected something like http://localhost:11434/v1/chat/completions. "
                f"Got URL={url}. Body: {snippet}"
            )
        return f"{label} HTTP 404. model=`{model}` URL={url}. Body: {snippet}"

    if status == 429:
        if backend == "ollama":
            return (
                f"{label} overloaded / rate limited (HTTP 429). "
                f"Your machine may be saturated. model=`{model}`. Body: {snippet}"
            )
        return f"{label} rate limit (HTTP 429). model=`{model}`. Body: {snippet}"

    if status == 500 or status >= 500:
        if backend == "ollama":
            return (
                f"{label} server error (HTTP {status}). "
                f"Is the model loaded? Try `ollama run {model}`. URL={url}. Body: {snippet}"
            )
        return f"{label} server error (HTTP {status}). Body: {snippet}"

    return f"{label} HTTP {status} | model=`{model}` | URL={url} | Body: {snippet}"


def _format_transport_error(exc: BaseException, url: str) -> str:
    """Connection refused, timeout, DNS, etc."""
    label = _provider_label()
    backend = _provider_backend()
    msg = str(exc) or type(exc).__name__

    if backend == "ollama":
        hints = (
            "Is Ollama running? Try `ollama serve` and open "
            "http://localhost:11434 in a browser. "
            "If you use a custom host/port, set OLLAMA_API_URL to the full "
            "chat completions URL (…/v1/chat/completions)."
        )
        return f"{label} connection failed → {url}\n{msg}\n{hints}\n[{_config_snapshot()}]"

    return (
        f"{label} connection failed → {url}\n{msg}\n"
        f"Check network / firewall / GROQ_API_URL.\n[{_config_snapshot()}]"
    )


def _missing_key_error() -> str:
    return (
        f"GROQ_API_KEY is missing (required for {_provider_label()}). "
        f"Paste a key in Settings → Provider API Key, Save, then restart the bot. "
        f"[{_config_snapshot()}]"
    )


# ---------------------------------------------------------------------------
# Section parser
# ---------------------------------------------------------------------------
def parse_sections(text: str):
    """Extract <reply> and <thoughts> sections, repairing missing tags if needed."""

    def extract(tag: str) -> str:
        start = text.find(f"<{tag}>")
        end = text.find(f"</{tag}>")

        if start != -1 and end == -1:
            next_tag = min(
                [
                    pos
                    for pos in (
                        text.find("<reply>", start + 1),
                        text.find("<thoughts>", start + 1),
                    )
                    if pos != -1
                ]
                or [len(text)]
            )
            return text[start + len(tag) + 2 : next_tag].strip()

        if start == -1 or end == -1:
            return ""

        return text[start + len(tag) + 2 : end].strip()

    reply = extract("reply")
    thoughts = extract("thoughts")
    return reply, thoughts


# ---------------------------------------------------------------------------
# Shared request helpers
# ---------------------------------------------------------------------------
def _build_payload(messages: list, model: str, max_tokens: int, temperature: float) -> dict:
    return {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def _sync_post(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
    """Blocking POST → parsed JSON or {"error": "..." }."""
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except Exception as e:
        return {"error": _format_transport_error(e, url)}

    if response.status_code != 200:
        return {
            "error": _format_http_error(
                response.status_code,
                response.text,
                payload.get("model", "?"),
                url,
            )
        }

    try:
        return response.json()
    except Exception as e:
        return {
            "error": (
                f"{_provider_label()} returned non-JSON (HTTP {response.status_code}). "
                f"{e}. Body[:300]={response.text[:300]!r}"
            )
        }


async def _async_post(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
    """Async POST → parsed JSON or {"error": "..." }."""
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    return {
                        "error": _format_http_error(
                            resp.status,
                            text,
                            payload.get("model", "?"),
                            url,
                        )
                    }
                try:
                    return await resp.json(content_type=None)
                except Exception as e:
                    return {
                        "error": (
                            f"{_provider_label()} returned non-JSON (HTTP {resp.status}). "
                            f"{e}. Body[:300]={text[:300]!r}"
                        )
                    }
    except Exception as e:
        return {"error": _format_transport_error(e, url)}


def _extract_content(data: dict) -> str | None:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


# ---------------------------------------------------------------------------
# MAIN MODEL CALL — normal Discord replies
# ---------------------------------------------------------------------------
async def call_groq(prompt: str, user_id: int | None = None):
    """
    Main chat call (name kept for compatibility).
    Works for both Groq and Ollama based on ECHE_PROVIDER.
    Returns: (reply_text, thoughts_text)
    """
    memory_block = ""
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
    url = _api_url()
    backend = _provider_backend()

    if not _api_key() and backend != "ollama":
        return (
            "Sorry, I hit a backend error.",
            f"({_missing_key_error()})",
        )

    payload = _build_payload(messages, model, max_tokens=1024, temperature=0.7)
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(
        None, lambda: _sync_post(url, payload, _headers(), timeout=90 if backend == "ollama" else 45)
    )

    if "error" in data:
        err = data["error"]
        return (
            "Sorry, I hit a backend error.",
            f"({_provider_label()} error: {err})",
        )

    raw = _extract_content(data)
    if raw is None:
        return (
            "Sorry, I hit a backend error.",
            f"({_provider_label()} bad response shape — no choices[0].message.content. "
            f"keys={list(data.keys()) if isinstance(data, dict) else type(data)} "
            f"| [{_config_snapshot()}])",
        )

    reply, thoughts = parse_sections(raw)

    if not thoughts:
        thoughts = (
            f"(Model failed to produce <thoughts>. Raw output preserved.)\n\n{raw}"
        )

    return reply, thoughts


# ---------------------------------------------------------------------------
# SIMPLE CALL — heckles only
# ---------------------------------------------------------------------------
async def call_groq_simple(prompt: str, max_chars: int = 2000):
    """
    Simple call for heckles.
    Returns ONLY the model's text — no thoughts, no tags, no tuples.
    On failure returns ("error", message).
    """
    max_tokens = max(10, max_chars // 4)
    model = _model()
    url = _api_url()
    backend = _provider_backend()

    if not _api_key() and backend != "ollama":
        return ("error", _missing_key_error())

    payload = _build_payload(
        [{"role": "user", "content": prompt}],
        model,
        max_tokens=max_tokens,
        temperature=0.9,
    )

    data = await _async_post(
        url, payload, _headers(), timeout=90 if backend == "ollama" else 45
    )

    if "error" in data:
        return ("error", data["error"])

    content = _extract_content(data)
    if content is None:
        return (
            "error",
            f"{_provider_label()} bad response shape (no content). [{_config_snapshot()}]",
        )
    return content


# ---------------------------------------------------------------------------
# RAW TEXT CALL — summarizer, LawManager, tools
# ---------------------------------------------------------------------------
async def call_groq_raw(prompt: str, model: str | None = None) -> str:
    """
    Sends a prompt and returns ONLY the model's text.
    Optional model= overrides the chat model (memory summarizer).
    """
    use_model = (model or "").strip() or _model()
    url = _api_url()
    backend = _provider_backend()

    if not _api_key() and backend != "ollama":
        return f"ERROR: {_missing_key_error()}"

    payload = _build_payload(
        [{"role": "user", "content": prompt}],
        use_model,
        max_tokens=1024,
        temperature=0.4,
    )

    data = await _async_post(
        url, payload, _headers(), timeout=90 if backend == "ollama" else 45
    )

    if "error" in data:
        return f"ERROR: {data['error']}"

    content = _extract_content(data)
    if content is None:
        return (
            f"ERROR: {_provider_label()} bad response shape (no content). "
            f"[{_config_snapshot()}]"
        )
    return content