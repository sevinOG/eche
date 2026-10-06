# client.py
# Groq path uses the official `groq` SDK (AsyncGroq).
# Ollama / local OpenAI-compatible path stays on raw REST (aiohttp/requests).
# Reads provider settings at call time from env (ECHE_PROVIDER, GROQ_*, OLLAMA_*).

from __future__ import annotations

import os
import re
import json
import asyncio
from dataclasses import dataclass, field
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
# Groq counts this reservation against tokens-per-minute before it writes.
# A normal reply is cut to 500 characters, so 256 is enough. The 2000-character
# override needs a larger reservation.
CHAT_COMPLETION_TOKENS = 256
CHAT_LONG_COMPLETION_TOKENS = 768

# Sent only when the Discord turn includes tools. Kept off the normal call_groq path.
def tool_use_note() -> str:
    """Fresh each call so the date is the day the message arrives."""
    from core.today import today_stamp
    return (
        f"Today is {today_stamp()}. "
        "When the speaker asks a question you would otherwise guess — a fact, "
        "score, news, date, definition, or current event — call duckduckgo "
        "with their question as query. Do not answer that kind of question "
        "from memory. Call other tools only when they ask you to do what that "
        "tool does. Never print a command, JSON, or tool-call markup."
    )


def lookup_prompt(question: str, notes: str) -> str:
    """Second-pass prompt. The notes are the lookup. The reply is the chat answer."""
    from core.today import today_stamp
    asked = " ".join((question or "").split())
    if len(asked) > 500:
        asked = asked[:499].rstrip() + "…"
    source = (notes or "").strip() or "(no notes)"
    if len(source) > 1500:
        source = source[:1499].rstrip() + "…"
    return (
        f"Today is {today_stamp()}.\n\n"
        f"The speaker asked:\n{asked}\n\n"
        f"Lookup notes:\n{source}\n\n"
        "Answer in character. Be direct and informative. "
        "Use only the notes. If they do not contain the answer, say you could not find it. "
        "Under 500 characters. Do not mention tools, searches, or these notes."
    )

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


def is_quota_error(msg: str) -> bool:
    """Groq uses 429 for TPM/RPM, including the 'Request too large' wording."""
    low = (msg or "").lower()
    return any(
        s in low
        for s in (
            "429",
            "rate limit",
            "rate_limit",
            "request too large",
            "tokens per minute",
            "tokens per day",
            "too many requests",
        )
    )


def quota_retry_seconds(msg: str) -> float | None:
    """Seconds to wait before one retry. None when a retry cannot help."""
    if not is_quota_error(msg):
        return None
    low = (msg or "").lower()
    if "per day" in low or "tpd" in low:
        return None
    match = re.search(r"try again in ([0-9]+(?:\.[0-9]+)?)\s*s", low)
    if match:
        return min(20.0, max(2.0, float(match.group(1)) + 0.5))
    return 8.0


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
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # qwen3.5 and granite 4.2 think by default. The trace spends max_tokens
    # and the visible content comes back empty, so chat and memory summaries
    # fail. "none" asks for the answer only. Granite 4.1 ignores it.
    if _provider_backend() == "ollama":
        payload["reasoning_effort"] = "none"
    return payload


def _reasoning_effort_rejected(error: str) -> bool:
    return "reasoning_effort" in (error or "").lower()


def _sync_post(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
    for attempt in range(2):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except Exception as e:
            return {"error": _format_transport_error(e, url)}

        if response.status_code != 200:
            err = _format_http_error(
                response.status_code,
                response.text,
                payload.get("model", "?"),
                url,
            )
            if (
                attempt == 0
                and "reasoning_effort" in payload
                and _reasoning_effort_rejected(err)
            ):
                payload.pop("reasoning_effort", None)
                continue
            return {"error": err}

        try:
            return response.json()
        except Exception as e:
            return {
                "error": (
                    f"{_provider_label()} returned non-JSON (HTTP {response.status_code}). "
                    f"{e}. Body[:300]={response.text[:300]!r}"
                )
            }
    return {"error": f"{_provider_label()} request failed. model=`{payload.get('model', '?')}`"}


async def _async_post(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
    for attempt in range(2):
        try:
            timeout_cfg = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        err = _format_http_error(
                            resp.status,
                            text,
                            payload.get("model", "?"),
                            url,
                        )
                        if (
                            attempt == 0
                            and "reasoning_effort" in payload
                            and _reasoning_effort_rejected(err)
                        ):
                            payload.pop("reasoning_effort", None)
                            continue
                        return {"error": err}
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
    return {"error": f"{_provider_label()} request failed. model=`{payload.get('model', '?')}`"}


def _extract_content(data: dict) -> str | None:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _extract_message(data: dict) -> dict | None:
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    return msg if isinstance(msg, dict) else None


def _parse_tool_arguments(arguments) -> dict:
    if isinstance(arguments, dict):
        return arguments
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, str):
        try:
            loaded = json.loads(arguments)
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _tool_calls_from_message(msg) -> list[dict]:
    """OpenAI-style tool_calls from a Groq SDK message or a REST message dict."""
    if isinstance(msg, dict):
        raw_calls = msg.get("tool_calls")
    else:
        raw_calls = getattr(msg, "tool_calls", None)
    out: list[dict] = []
    for tc in raw_calls or []:
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            arguments = fn.get("arguments") if isinstance(fn, dict) else None
        else:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) if fn is not None else None
            arguments = getattr(fn, "arguments", None) if fn is not None else None
        if not name:
            continue
        out.append({"name": str(name), "arguments": _parse_tool_arguments(arguments)})
    return out


def _tools_unsupported(err: str) -> bool:
    """True when the provider rejected the tools parameter, not the prompt."""
    low = (err or "").lower()
    if "tool" not in low and "function" not in low:
        return False
    return any(
        marker in low
        for marker in (
            "not support",
            "unsupported",
            "unknown parameter",
            "unrecognized",
            "extra fields",
            "invalid",
            "does not",
            "wasn't",
            "was not",
        )
    )


def _relax_request(kwargs: dict, err: str) -> bool:
    """Drop one unsupported option so the same chat can be retried."""
    changed = False
    low = (err or "").lower()
    if "reasoning_effort" in low and "reasoning_effort" in kwargs:
        kwargs.pop("reasoning_effort", None)
        changed = True
    # A rejection of this flag is not a rejection of tools themselves.
    if "parallel_tool_calls" in kwargs and "parallel_tool_calls" in low:
        kwargs.pop("parallel_tool_calls", None)
        changed = True
        low = low.replace("parallel_tool_calls", "")
    if "tools" in kwargs and _tools_unsupported(low):
        kwargs.pop("tools", None)
        kwargs.pop("tool_choice", None)
        kwargs.pop("parallel_tool_calls", None)
        changed = True
    return changed


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
    tools: list | None = None,
) -> str | dict:
    """
    Returns the full content string on success, or {"error": "..."} on failure.
    When tools are accepted, success is {"content": str, "tool_calls": list}
    instead of a bare string. reasoning_effort defaults to "none" to reduce
    native CoT dumps into content.
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
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
        kwargs["parallel_tool_calls"] = False

    async def _create(kw: dict) -> str | dict:
        completion = await client.chat.completions.create(**kw)
        if kw.get("stream"):
            parts: list[str] = []
            async for chunk in completion:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    parts.append(delta)
            return "".join(parts)
        msg = completion.choices[0].message
        content = getattr(msg, "content", None) or ""
        if not isinstance(content, str):
            content = str(content or "")
        if kw.get("tools"):
            return {"content": content, "tool_calls": _tool_calls_from_message(msg)}
        return content

    try:
        return await _create(kwargs)
    except Exception as e:
        err = str(e) or type(e).__name__
        # Drop reasoning_effort or tools if this model rejects them, then
        # keep the existing single quota retry. A tools fallback still answers.
        for _ in range(2):
            if not _relax_request(kwargs, err):
                break
            try:
                return await _create(kwargs)
            except Exception as e2:
                err = str(e2) or type(e2).__name__
        wait = quota_retry_seconds(err)
        if wait is not None:
            await asyncio.sleep(wait)
            try:
                return await _create(kwargs)
            except Exception as e3:
                err = str(e3) or type(e3).__name__
        return {
            "error": (
                f"Groq SDK error: {err}\n"
                f"model=`{model}` | [{_config_snapshot()}]"
            )
        }


# ---------------------------------------------------------------------------
# MAIN MODEL CALL — Discord + local chat
# ---------------------------------------------------------------------------
@dataclass
class ModelTurn:
    """One chat completion. tool_calls is empty on a normal reply."""

    reply: str
    thoughts: str
    tool_calls: list = field(default_factory=list)
    quota: bool = False


def _split_completion(result) -> tuple[str, list]:
    """Content plus native tool calls. Error dicts are handled by the caller."""
    if isinstance(result, dict) and "tool_calls" in result and "error" not in result:
        content = result.get("content") or ""
        if not isinstance(content, str):
            content = str(content or "")
        return content, list(result.get("tool_calls") or [])
    if isinstance(result, str):
        return result, []
    return "", []


def _quota_turn() -> ModelTurn:
    return ModelTurn("sorry, i'm being rate limited, check back later", "", [], True)


def _error_turn(error_msg: str) -> ModelTurn:
    return ModelTurn(
        _public_error(error_msg),
        f"({_provider_label()} error: {error_msg})",
        [],
        False,
    )


async def call_groq_turn(
    prompt: str,
    user_id: int | None = None,
    max_completion_tokens: int = CHAT_COMPLETION_TOKENS,
    tools: list | None = None,
    extra_system: str | None = None,
) -> ModelTurn:
    """
    Chat completion for Discord. `tools` is an OpenAI-style tool list.
    A quota miss returns no tool calls, so the caller does not run tools
    or start another request on that turn.
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
    if tools:
        messages.append({"role": "system", "content": tool_use_note()})
    # Admin markdown is passed only when that toggle is on. Empty stays out.
    admin_note = (extra_system or "").strip()
    if admin_note:
        messages.append({"role": "system", "content": admin_note})

    model = _model()
    backend = _provider_backend()
    raw = ""
    calls: list = []

    if backend == "cloud":
        result = await _groq_sdk_call(
            messages,
            model=model,
            max_completion_tokens=max_completion_tokens,
            temperature=0.6,
            top_p=0.95,
            reasoning_effort="none",
            stream=False,
            tools=tools or None,
        )

        if isinstance(result, dict) and "error" in result:
            error_msg = result["error"]
            if is_quota_error(error_msg):
                return _quota_turn()
            return _error_turn(error_msg)

        raw, calls = _split_completion(result)
    else:
        if not _api_key() and backend != "ollama":
            err = _missing_key_error()
            return ModelTurn(_public_error(err), f"({err})", [], False)

        url = _api_url()
        payload = _build_payload(
            messages, model, max_tokens=max_completion_tokens, temperature=0.7
        )
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None,
            lambda: _sync_post(url, payload, _headers(), timeout=90),
        )

        if "error" in data and tools and _tools_unsupported(data["error"]):
            payload.pop("tools", None)
            payload.pop("tool_choice", None)
            data = await loop.run_in_executor(
                None,
                lambda: _sync_post(url, payload, _headers(), timeout=90),
            )

        if "error" in data:
            error_msg = data["error"]
            if is_quota_error(error_msg):
                return _quota_turn()
            return _error_turn(error_msg)

        msg = _extract_message(data)
        calls = _tool_calls_from_message(msg) if msg and payload.get("tools") else []
        raw = None if msg is None else msg.get("content")
        if raw is None and not calls:
            error_msg = (
                f"{_provider_label()} bad response shape — no choices[0].message.content. "
                f"keys={list(data.keys()) if isinstance(data, dict) else type(data)} "
                f"| [{_config_snapshot()}]"
            )
            return _error_turn(error_msg)
        raw = raw or ""

    if not calls and (raw or "").strip():
        # Model typed the call instead of using the tool API. Capture it
        # before the reply sanitizer can cut or replace that text.
        try:
            from core.tools import calls_from_model_text, registered_names
            calls = calls_from_model_text(raw, registered_names())
        except Exception:
            calls = []

    reply, thoughts = _finalize_sections(raw)
    quota = reply.strip().lower().startswith("sorry, i'm being rate limited")
    if quota:
        calls = []
    return ModelTurn(reply, thoughts, calls, quota)


async def call_groq(
    prompt: str,
    user_id: int | None = None,
    max_completion_tokens: int = CHAT_COMPLETION_TOKENS,
):
    """
    Main chat call. Works for both Groq (SDK) and Ollama (REST).
    Returns: (reply_text, detail_text)
    Public Discord / Local should use reply_text.
    """
    turn = await call_groq_turn(
        prompt,
        user_id=user_id,
        max_completion_tokens=max_completion_tokens,
    )
    return turn.reply, turn.thoughts


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
async def call_groq_raw(
    prompt: str,
    model: str | None = None,
    max_completion_tokens: int = 512,
) -> str:
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
            max_completion_tokens=max_completion_tokens,
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
    payload = _build_payload(
        messages, use_model, max_tokens=max_completion_tokens, temperature=0.4
    )
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