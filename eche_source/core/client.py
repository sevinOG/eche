# client.py
# Groq path uses the official `groq` SDK (AsyncGroq).
# Ollama / local OpenAI-compatible path stays on raw REST (aiohttp/requests).
# Reads provider settings at call time from env (ECHE_PROVIDER, GROQ_*, OLLAMA_*).

from __future__ import annotations

import os
import re
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

DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_OLLAMA_MODEL = "llama3"

# Legacy alias
API_URL = GROQ_API_URL

# Public reply hard limit (chars)
REPLY_MAX_CHARS = 500

# If reply contains these, treat as leaked CoT / instructions
_LEAK_MARKERS = (
    "Self-Correction",
    "I'll generate",
    "I will generate",
    "Maintain Eche's persona",
    "OUTPUT FORMAT",
    "RESPONSE RULES",
    "under 1000 chars",
    "Determine Eche's Response Strategy",
    "Refinement during thought",
    "<thoughts>",
    "</thoughts>",
)

_DEPRECATED_MODELS = (
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama-4-scout",
)


# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------
def _provider_backend() -> str:
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
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if _provider_backend() == "ollama":
        return key or "ollama"
    return key


def _model() -> str:
    raw = (os.getenv("GROQ_MODEL") or "").strip()
    low = raw.lower()
    if (
        not raw
        or any(d in low for d in _DEPRECATED_MODELS)
        or low in {d.lower() for d in _DEPRECATED_MODELS}
    ):
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
    return (
        f"backend={_provider_backend()} | "
        f"url={_api_url()} | "
        f"model={_model()} | "
        f"key={'set' if (_api_key() and _api_key() != 'ollama') else ('dummy' if _provider_backend() == 'ollama' else 'MISSING')}"
    )


def _public_error(msg: str, limit: int = 180) -> str:
    """Short error string safe to show in Discord / Local chat."""
    one = " ".join(str(msg or "").split())
    if len(one) > limit:
        one = one[: limit - 1].rstrip() + "…"
    return f"Sorry, I hit a backend error: {one}"


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------
def _format_http_error(status: int, body: str, model: str, url: str) -> str:
    body = (body or "").strip()
    label = _provider_label()
    backend = _provider_backend()
    snippet = body[:400] if body else "(empty body)"

    def _is_rate_limit() -> bool:
        return status == 429 or "rate limit" in body.lower() or "rate_limit" in body.lower()

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
                f"(e.g. {DEFAULT_MODEL}). Body: {snippet}"
            )
        if backend == "ollama":
            return (
                f"{label} HTTP 404 — wrong endpoint path? "
                f"Expected something like http://localhost:11434/v1/chat/completions. "
                f"Got URL={url}. Body: {snippet}"
            )
        return f"{label} HTTP 404. model=`{model}` URL={url}. Body: {snippet}"

    if status == 429 or _is_rate_limit():
        if backend == "ollama":
            return (
                f"{label} overloaded / rate limited (HTTP 429). "
                f"Your machine may be saturated. model=`{model}`. Body: {snippet}"
            )
        return f"{label} rate limit (HTTP 429). model=`{model}`. Body: {snippet}"

    if status >= 500:
        if backend == "ollama":
            return (
                f"{label} server error (HTTP {status}). "
                f"Is the model loaded? Try `ollama run {model}`. URL={url}. Body: {snippet}"
            )
        return f"{label} server error (HTTP {status}). Body: {snippet}"

    if status == 400:
        body_l = body.lower()
        if any(x in body_l for x in ("context length", "too long", "request too large", "exceeds", "prompt is too long")):
            return (
                f"{label} prompt too long (memory + chat exceeded model limit). "
                f"Shorten your message, or the bot's memory for this user is bloated. "
                f"model=`{model}`"
            )
        return f"{label} bad request (HTTP 400). model=`{model}`. Body: {snippet}"

    return f"{label} HTTP {status} | model=`{model}` | URL={url} | Body: {snippet}"


def _format_transport_error(exc: BaseException, url: str) -> str:
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
# Section parser + sanitizer (stops CoT leaks; allows plain text)
# ---------------------------------------------------------------------------
def parse_sections(text: str) -> tuple[str, str]:
    """
    Extract <reply> and <thoughts> if present.
    Prefers the last well-formed <reply> pair.
    """
    text = text or ""

    def extract_all(tag: str) -> list[str]:
        open_t = f"<{tag}>"
        close_t = f"</{tag}>"
        parts: list[str] = []
        start = 0
        while True:
            s = text.find(open_t, start)
            if s == -1:
                break
            e = text.find(close_t, s + len(open_t))
            if e == -1:
                next_positions = [
                    p
                    for p in (
                        text.find("<reply>", s + 1),
                        text.find("<thoughts>", s + 1),
                        text.find("</reply>", s + 1),
                        text.find("</thoughts>", s + 1),
                    )
                    if p != -1
                ]
                end = min(next_positions) if next_positions else len(text)
                parts.append(text[s + len(open_t) : end].strip())
                break
            parts.append(text[s + len(open_t) : e].strip())
            start = e + len(close_t)
        return parts

    replies = extract_all("reply")
    thoughts_list = extract_all("thoughts")

    reply = replies[-1] if replies else ""
    thoughts = thoughts_list[-1] if thoughts_list else ""
    return reply, thoughts


def _looks_like_leak(text: str) -> bool:
    if not text:
        return True
    low = text.lower()
    for m in _LEAK_MARKERS:
        if m.lower() in low:
            return True
    if text.count("\n- ") >= 4 and len(text) > 400:
        return True
    return False


def _sanitize_reply(reply: str, raw: str) -> tuple[str, str]:
    """
    Returns (public_reply, thoughts_extra).
    Accepts plain text when no <reply> tags are present.
    """
    reply = (reply or "").strip()

    reply = re.sub(
        r"<thoughts>[\s\S]*?</thoughts>",
        "",
        reply,
        flags=re.IGNORECASE,
    ).strip()
    reply = re.sub(r"</?reply>", "", reply, flags=re.IGNORECASE).strip()

    if not reply or _looks_like_leak(reply):
        again, _ = parse_sections(raw or "")
        again = (again or "").strip()
        again = re.sub(
            r"<thoughts>[\s\S]*?</thoughts>",
            "",
            again,
            flags=re.IGNORECASE,
        ).strip()
        if again and not _looks_like_leak(again):
            reply = again
        else:
            raw_s = (raw or "").strip()
            raw_s = re.sub(
                r"<thoughts>[\s\S]*?</thoughts>",
                "",
                raw_s,
                flags=re.IGNORECASE,
            ).strip()
            raw_s = re.sub(r"</?reply>", "", raw_s, flags=re.IGNORECASE).strip()
            if raw_s and not _looks_like_leak(raw_s):
                reply = raw_s
            else:
                return "...", f"(Unusable model output; raw preserved.)\n\n{raw}"

    if len(reply) > REPLY_MAX_CHARS:
        reply = reply[: REPLY_MAX_CHARS - 1].rstrip() + "…"

    return reply, ""


def _finalize_sections(raw: str) -> tuple[str, str]:
    """
    Prefer <reply> if present; otherwise use full model output as the public reply.
    """
    raw = (raw or "").strip()
    if not raw:
        return "...", "(empty model output)"

    reply, thoughts = parse_sections(raw)

    # Plain-text models: no <reply> tags — use whole message
    if not (reply or "").strip():
        reply = raw

    clean, extra = _sanitize_reply(reply, raw)

    if clean.strip() in ("", "...") and raw and not _looks_like_leak(raw):
        clean = raw[:REPLY_MAX_CHARS]

    if not thoughts:
        thoughts = ""
    if extra:
        thoughts = f"{extra}\n\n{thoughts}".strip()
    return clean, thoughts


# ---------------------------------------------------------------------------
# Shared REST helpers (Ollama)
# ---------------------------------------------------------------------------
def _build_payload(messages: list, model: str, max_tokens: int, temperature: float) -> dict:
    return {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def _sync_post(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
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
# Format system prompt (plain text — no required thoughts tags)
# ---------------------------------------------------------------------------
def _format_system_prompt(memory_block: str = "") -> str:
    return (
        "Reply as plain text only (in character). No XML tags, no hidden notes, "
        "no chain-of-thought. Do not restate instructions, persona traits, or analysis. "
        "Keep replies concise unless asked for detail.\n"
        + (memory_block or "")
    )


# ---------------------------------------------------------------------------
# Groq SDK helper (cloud path only)
# ---------------------------------------------------------------------------
async def _groq_sdk_call(
    messages: list,
    *,
    model: str,
    max_completion_tokens: int = 2048,
    temperature: float = 0.6,
    top_p: float = 0.95,
    reasoning_effort: str | None = "none",
    stream: bool = False,
) -> str | dict:
    """
    Returns the full content string on success, or {"error": "..."} on failure.
    reasoning_effort defaults to "none" to reduce native CoT dumps into content.
    """
    try:
        from groq import AsyncGroq
    except ImportError:
        return {
            "error": (
                "The `groq` package is not installed. "
                "Run: pip install groq   (and add it to requirements.txt)"
            )
        }

    api_key = _api_key()
    if not api_key:
        return {"error": _missing_key_error()}

    client = AsyncGroq(api_key=api_key)

    try:
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
            "top_p": top_p,
            "stream": stream,
            "stop": None,
        }
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        completion = await client.chat.completions.create(**kwargs)

        if stream:
            parts: list[str] = []
            async for chunk in completion:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    parts.append(delta)
            return "".join(parts)

        msg = completion.choices[0].message
        content = getattr(msg, "content", None) or ""
        return content if isinstance(content, str) else str(content or "")

    except Exception as e:
        msg = str(e) or type(e).__name__
        if "reasoning_effort" in msg.lower() and reasoning_effort is not None:
            try:
                kwargs.pop("reasoning_effort", None)
                completion = await client.chat.completions.create(**kwargs)
                if stream:
                    parts = []
                    async for chunk in completion:
                        delta = chunk.choices[0].delta.content if chunk.choices else None
                        if delta:
                            parts.append(delta)
                    return "".join(parts)
                return completion.choices[0].message.content or ""
            except Exception as e2:
                msg = str(e2) or type(e2).__name__
        return {
            "error": (
                f"Groq SDK error: {msg}\n"
                f"model=`{model}` | [{_config_snapshot()}]"
            )
        }


# ---------------------------------------------------------------------------
# MAIN MODEL CALL — Discord + local chat
# ---------------------------------------------------------------------------
async def call_groq(prompt: str, user_id: int | None = None):
    """
    Main chat call. Works for both Groq (SDK) and Ollama (REST).
    Returns: (reply_text, detail_text)
    Public Discord / Local should use reply_text.
    """
    memory_block = ""
    if user_id is not None:
        try:
            summary = load_memory_summary(user_id)
            if summary:
                # Truncate to avoid "prompt too long" on Groq (keep recent/relevant)
                if len(summary) > 1200:
                    summary = summary[-1200:]
                memory_block = (
                    "The following describes the user's past interactions and traits:\n"
                    f"{summary}\n\n"
                )
        except Exception:
            pass

    system_prompt = _format_system_prompt(memory_block)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": get_personality_prompt()},
        {"role": "user", "content": prompt},
    ]

    model = _model()
    backend = _provider_backend()

    if backend == "cloud":
        result = await _groq_sdk_call(
            messages,
            model=model,
            max_completion_tokens=2048,
            temperature=0.6,
            top_p=0.95,
            reasoning_effort="none",
            stream=False,
        )

        if isinstance(result, dict) and "error" in result:
            error_msg = result["error"]
            if "rate limit" in error_msg.lower():
                return ("sorry, i'm being rate limited, check back later", "")
            return (_public_error(error_msg), f"({_provider_label()} error: {error_msg})")

        raw = result or ""
    else:
        if not _api_key() and backend != "ollama":
            err = _missing_key_error()
            return (_public_error(err), f"({err})")

        url = _api_url()
        payload = _build_payload(messages, model, max_tokens=1024, temperature=0.7)
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None,
            lambda: _sync_post(url, payload, _headers(), timeout=90),
        )

        if "error" in data:
            error_msg = data["error"]
            return (_public_error(error_msg), f"({_provider_label()} error: {error_msg})")

        raw = _extract_content(data)
        if raw is None:
            error_msg = (
                f"{_provider_label()} bad response shape — no choices[0].message.content. "
                f"keys={list(data.keys()) if isinstance(data, dict) else type(data)} "
                f"| [{_config_snapshot()}]"
            )
            return (_public_error(error_msg), f"({error_msg})")

    return _finalize_sections(raw)


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
    backend = _provider_backend()

    messages = [{"role": "user", "content": prompt}]

    if backend == "cloud":
        result = await _groq_sdk_call(
            messages,
            model=model,
            max_completion_tokens=max_tokens,
            temperature=0.9,
            top_p=0.95,
            reasoning_effort=None,
            stream=False,
        )
        if isinstance(result, dict) and "error" in result:
            return ("error", result["error"])
        text = (result or "").strip()
        if "<reply>" in text.lower():
            r, _ = parse_sections(text)
            text = r or text
        return text

    if not _api_key() and backend != "ollama":
        return ("error", _missing_key_error())

    url = _api_url()
    payload = _build_payload(messages, model, max_tokens=max_tokens, temperature=0.9)
    data = await _async_post(url, payload, _headers(), timeout=90)

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
    backend = _provider_backend()
    messages = [{"role": "user", "content": prompt}]

    if backend == "cloud":
        result = await _groq_sdk_call(
            messages,
            model=use_model,
            max_completion_tokens=1024,
            temperature=0.4,
            top_p=0.95,
            reasoning_effort=None,
            stream=False,
        )
        if isinstance(result, dict) and "error" in result:
            return f"ERROR: {result['error']}"
        return result or ""

    if not _api_key() and backend != "ollama":
        return f"ERROR: {_missing_key_error()}"

    url = _api_url()
    payload = _build_payload(messages, use_model, max_tokens=1024, temperature=0.4)
    data = await _async_post(url, payload, _headers(), timeout=90)

    if "error" in data:
        return f"ERROR: {data['error']}"

    content = _extract_content(data)
    if content is None:
        return (
            f"ERROR: {_provider_label()} bad response shape (no content). "
            f"[{_config_snapshot()}]"
        )
    return content