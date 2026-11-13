# client.py
# Groq path uses the official `groq` SDK (AsyncGroq).
# OpenRouter uses the OpenAI-compatible REST path. Ollama uses its native
# /api/chat so the request can set a small context window.
# Reads provider settings at call time from env (ECHE_PROVIDER, GROQ_*, OLLAMA_*, OPENROUTER_*).

from __future__ import annotations

import os
import re
import json
import asyncio
import contextvars
from dataclasses import dataclass, field
import requests
import aiohttp
from dotenv import load_dotenv

from core.personality import get_personality_prompt
from core.prompt_text import system_text, tool_catalog

load_dotenv()

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OLLAMA_API_URL = "http://localhost:11434/v1/chat/completions"

# Recent request text (persona/cards/latest user) for the output check.
_output_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "eche_output_context", default=""
)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_OLLAMA_MODEL = "llama3"
# Free router. A paid model id from openrouter.ai/models can replace it.
DEFAULT_OPENROUTER_MODEL = "openrouter/free"
# A Discord turn fits here. The Modelfile window is much larger and slow to load.
# Settings → Provider & Model can move it (ollama_num_ctx).
_OLLAMA_NUM_CTX = 8192
# Thinking tokens share the reply cap. Leave the answer room beside them.
# Settings → Provider & Model can move it (reasoning_budget).
_THINK_ROOM = 1024


def _ollama_num_ctx() -> int:
    try:
        from core.ollama_thinking import num_ctx
        return num_ctx()
    except Exception:
        return _OLLAMA_NUM_CTX


def _think_room() -> int:
    try:
        from core.ollama_thinking import think_budget
        return think_budget()
    except Exception:
        return _THINK_ROOM

# Legacy alias
API_URL = GROQ_API_URL

# Discord rejects a message at 2000 characters. Sends stay under this.
REPLY_MAX_CHARS = 1900
# One completion token can be several characters. Budgets use this so a
# finished message cannot reach REPLY_MAX_CHARS.
CHARS_PER_TOKEN = 6
# Channel reply: two or three sentences, one paragraph. A longer answer asks
# for a thread instead of posting the rest here.
CHAT_COMPLETION_TOKENS = 64
# One thread post. Further posts are separate completions, each under the same cap.
THREAD_COMPLETION_TOKENS = max(16, REPLY_MAX_CHARS // CHARS_PER_TOKEN)
# Settings can move either budget. These are the edges of that control.
COMPLETION_TOKEN_MIN = 16
COMPLETION_TOKEN_MAX = 8192
_TOKEN_CACHE: dict = {}


def clamp_completion_tokens(raw, default: int) -> int:
    """A settings value inside the output-token range. Blank or junk stays at default."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)
    if value < COMPLETION_TOKEN_MIN:
        return COMPLETION_TOKEN_MIN
    if value > COMPLETION_TOKEN_MAX:
        return COMPLETION_TOKEN_MAX
    return value


def _completion_setting(key: str, env_name: str, default: int) -> int:
    """Saved output budget. The settings file wins over the env copied at launch."""
    path = ""
    stamp = None
    try:
        from core.paths import user_dir
        from core.secrets import settings_path

        path = settings_path(user_dir())
        stamp = os.path.getmtime(path) if os.path.isfile(path) else None
    except Exception:
        path = ""
        stamp = None
    env_raw = (os.environ.get(env_name) or "").strip()
    cache_key = (path, stamp, env_raw)
    cached = _TOKEN_CACHE.get(key)
    if cached is None or cached[0] != cache_key:
        raw = ""
        if path:
            try:
                from core.paths import user_dir
                from core.secrets import public_value

                raw = public_value(key, user_dir())
            except Exception:
                raw = ""
        if not raw:
            raw = env_raw
        value = default if not raw else clamp_completion_tokens(raw, default)
        _TOKEN_CACHE[key] = (cache_key, value)
        cached = _TOKEN_CACHE[key]
    return cached[1]


def channel_completion_tokens() -> int:
    """Tokens for one channel reply. Settings → Provider & Model, else 64."""
    return _completion_setting(
        "channel_completion_tokens",
        "ECHE_CHANNEL_TOKENS",
        CHAT_COMPLETION_TOKENS,
    )


def thread_completion_tokens() -> int:
    """Tokens for one thread post. Settings → Provider & Model, else 316."""
    return _completion_setting(
        "thread_completion_tokens",
        "ECHE_THREAD_TOKENS",
        THREAD_COMPLETION_TOKENS,
    )


def tokens_for(char_budget: int) -> int:
    """Completion tokens that stay under char_budget at CHARS_PER_TOKEN."""
    return max(16, int(char_budget) // CHARS_PER_TOKEN)


def discord_chunks(text: str, limit: int | None = None) -> list[str]:
    """Split text into Discord messages.

    Each piece stays under the limit. Breaks prefer a paragraph, then a line,
    then the end of a sentence, then a space. Nothing is dropped.
    """
    if limit is None:
        limit = REPLY_MAX_CHARS
    rest = (text or "").strip()
    if not rest:
        return []
    if limit < 1:
        limit = REPLY_MAX_CHARS
    pieces: list[str] = []
    while rest:
        if len(rest) <= limit:
            pieces.append(rest)
            break
        window = rest[:limit]
        cut = 0
        for sep in ("\n\n", "\n", ". ", "! ", "? ", " "):
            idx = window.rfind(sep)
            if idx >= limit // 5:
                cut = idx + len(sep)
                break
        if cut <= 0:
            cut = limit
        piece = rest[:cut].strip()
        rest = rest[cut:].strip()
        if piece:
            pieces.append(piece)
    return pieces


_SENTENCE_END = re.compile(r"[.!?…]+(?:[\"')\]]+)?")
# Three dots, or the ellipsis character, are a trailing-off tail.
_ELLIPSIS_TAIL = re.compile(r"(?:\.{3,}|…+)(?:[\"')\]]+)?$")


def reply_was_cut(reason: str) -> bool:
    """True when the provider stopped because the token cap was hit."""
    return (reason or "").strip().lower() in {"length", "max_tokens"}


def _is_ellipsis_mark(token: str) -> bool:
    core = re.sub(r"[\"')\]]+$", "", token or "")
    if "…" in core:
        return True
    return bool(re.fullmatch(r"\.{3,}", core))


def complete_sentences(text: str) -> str:
    """Text through the last finished sentence. A cut tail is left off.

    A trailing ellipsis is not a finished ending. A reply the model chose
    to stop is not passed here, so a short line with no period stays.
    """
    body = (text or "").strip()
    if not body or not re.search(r"\w", body):
        return ""
    if _ELLIPSIS_TAIL.search(body):
        body = _ELLIPSIS_TAIL.sub("", body).rstrip()
        if not body or not re.search(r"\w", body):
            return ""
    if re.search(r"[.!?](?:[\"')\]]+)?$", body):
        return body
    last = None
    for match in _SENTENCE_END.finditer(body):
        if _is_ellipsis_mark(match.group(0)):
            continue
        last = match
    if last is None:
        return ""
    return body[: last.end()].strip()


def last_sentence(text: str) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", body)
    return (parts[-1] if parts else body).strip()


def tool_use_note(tools: list | None = None) -> str:
    """Channel tool slot. The date and the work stay on the user turn."""
    return tool_catalog(tools)


# Posted as the tool result when DuckDuckGo does not return a page. The
# second completion still answers the person. These lines are not a search.
_LOOKUP_FAILED = (
    "DuckDuckGo blocked that lookup.",
    "I couldn't reach DuckDuckGo.",
)


def lookup_failed(notes: str) -> bool:
    """True when the lookup produced a status line instead of notes."""
    return (notes or "").strip() in _LOOKUP_FAILED


def lookup_prompt(prompt: str, notes: str) -> str:
    """Same turn again, with the lookup notes appended. The reply is the chat answer."""
    source = (notes or "").strip() or "(no notes)"
    if len(source) > 1500:
        source = source[:1499].rstrip() + "…"
    base = (prompt or "").strip()
    if lookup_failed(source):
        return (
            f"{base}\n\n"
            f"Lookup notes:\n{source}\n\n"
            "The lookup did not go through. "
            "Answer the most recent message from what you already know. "
            "Say that the lookup did not go through. "
            "Do not invent search results, scores, dates, or news."
        )
    return (
        f"{base}\n\n"
        f"Lookup notes:\n{source}\n\n"
        "Send the finished answer to the most recent message from these notes. "
        "If the notes do not contain the answer, say you could not find it."
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
    if raw in ("openrouter", "open-router"):
        return "openrouter"
    return "cloud"


def _provider_label() -> str:
    backend = _provider_backend()
    if backend == "ollama":
        return "Ollama (local)"
    if backend == "openrouter":
        return "OpenRouter"
    return "Groq (cloud)"


def _api_url() -> str:
    backend = _provider_backend()
    if backend == "ollama":
        custom = (os.getenv("OLLAMA_API_URL") or "").strip()
        return custom or OLLAMA_API_URL
    if backend == "openrouter":
        custom = (os.getenv("OPENROUTER_API_URL") or "").strip()
        return custom or OPENROUTER_API_URL
    custom = (os.getenv("GROQ_API_URL") or "").strip()
    return custom or GROQ_API_URL


def _api_key() -> str:
    if _provider_backend() == "openrouter":
        return (os.getenv("OPENROUTER_API_KEY") or "").strip()
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if _provider_backend() == "ollama":
        return key or "ollama"
    return key


def _model_is_unset(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return True
    if low in {d.lower() for d in _DEPRECATED_MODELS}:
        return True
    return any(d in low for d in _DEPRECATED_MODELS)


def _model() -> str:
    raw = (os.getenv("GROQ_MODEL") or "").strip()
    backend = _provider_backend()
    if backend == "ollama":
        fallback = DEFAULT_OLLAMA_MODEL
    elif backend == "openrouter":
        fallback = DEFAULT_OPENROUTER_MODEL
    else:
        fallback = DEFAULT_MODEL
    try:
        from core.media_scan import usable_chat_model
        raw = usable_chat_model(raw, "")
    except Exception:
        pass
    if _model_is_unset(raw):
        return fallback
    return raw


def _chat_fallback() -> str:
    backend = _provider_backend()
    if backend == "ollama":
        return DEFAULT_OLLAMA_MODEL
    if backend == "openrouter":
        return DEFAULT_OPENROUTER_MODEL
    return DEFAULT_MODEL


def _safe_chat_model(model: str) -> str:
    """Never send an image/video generator id. Used by every request path."""
    raw = (model or "").strip()
    try:
        from core.media_scan import usable_chat_model
        raw = usable_chat_model(raw, "")
    except Exception:
        # If the filter cannot load, do not send the raw id.
        return _chat_fallback()
    if _model_is_unset(raw):
        return _chat_fallback()
    return raw


def _headers() -> dict:
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    if _provider_backend() == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/sevinOG/eche"
        headers["X-Title"] = "Eche"
    return headers


def _config_snapshot() -> str:
    return (
        f"backend={_provider_backend()} | "
        f"url={_api_url()} | "
        f"model={_model()} | "
        f"key={'set' if (_api_key() and _api_key() != 'ollama') else ('dummy' if _provider_backend() == 'ollama' else 'MISSING')}"
    )


def _error_text(err, depth: int = 0) -> str:
    """Provider errors arrive as text or as {"message": "..."}."""
    if err is None or depth > 4:
        return "" if err is None else str(err)
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        for key in ("message", "error", "detail", "msg"):
            if key not in err:
                continue
            text = _error_text(err.get(key), depth + 1)
            if text.strip():
                return text
        try:
            return json.dumps(err, ensure_ascii=False)
        except Exception:
            return str(err)
    if isinstance(err, (list, tuple)):
        return " ".join(part for part in (_error_text(item, depth + 1) for item in err) if part)
    return str(err)


def is_quota_error(msg: str) -> bool:
    """Groq uses 429 for TPM/RPM, including the 'Request too large' wording."""
    low = _error_text(msg).lower()
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
    low = _error_text(msg).lower()
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
        one = "the provider returned an error"
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
        if backend == "openrouter":
            return (
                f"{label} auth failed (HTTP 401). "
                f"Check the OpenRouter API Key in Settings → Provider & Model. "
                f"model=`{model}`. Body: {snippet}"
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
            if backend == "openrouter":
                return (
                    f"{label} model not found: `{model}`. "
                    f"Paste an id from openrouter.ai/models, such as {DEFAULT_OPENROUTER_MODEL}. "
                    f"Body: {snippet}"
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

    if backend == "openrouter":
        return (
            f"{label} connection failed → {url}\n{msg}\n"
            f"Check the network, or set OPENROUTER_API_URL to the full "
            f"chat completions URL.\n[{_config_snapshot()}]"
        )

    return (
        f"{label} connection failed → {url}\n{msg}\n"
        f"Check network / firewall / GROQ_API_URL.\n[{_config_snapshot()}]"
    )


def _missing_key_error() -> str:
    if _provider_backend() == "openrouter":
        return (
            "OPENROUTER_API_KEY is missing (required for OpenRouter). "
            "Paste a key in Settings → Provider & Model → OpenRouter API Key, Save, "
            "then restart the bot. "
            f"[{_config_snapshot()}]"
        )
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

    return reply, ""


def _finalize_sections(raw: str) -> tuple[str, str]:
    """Prefer <reply> if present; otherwise use the finished model output."""
    original = raw or ""
    raw = _visible_reply(original)
    if not raw:
        # A classifier label is not a reply. Leave it blank so chat does not
        # post the label or a placeholder dot.
        if _SAFETY_LABEL_LINE.search(original):
            return "", "(safety label dropped)"
        return "...", "(empty model output)"

    reply, thoughts = parse_sections(raw)

    # Plain-text models: no <reply> tags — use whole message
    if not (reply or "").strip():
        reply = raw

    clean, extra = _sanitize_reply(reply, raw)

    if clean.strip() in ("", "...") and raw and not _looks_like_leak(raw):
        clean = raw

    if not thoughts:
        thoughts = ""
    if extra:
        thoughts = f"{extra}\n\n{thoughts}".strip()
    return clean, thoughts


# ---------------------------------------------------------------------------
# Shared REST helpers (Ollama)
# ---------------------------------------------------------------------------
def _reasoning_for(payload: dict | None) -> bool:
    """Chat uses the settings switch. A forced call does not."""
    forced = (payload or {}).get("_reasoning")
    if forced == "off":
        return False
    if forced == "on":
        return True
    return reasoning_enabled()


def _request_output_context(messages: list) -> str:
    """Persona, character cards, and the latest user text for the output check."""
    from core.blocklist import split_transcript
    from core.safety import is_footer_message, is_user_guard, message_text

    parts: list[str] = []
    last_user = ""
    for message in messages or []:
        if is_footer_message(message) or is_user_guard(message):
            continue
        text = message_text(message) if isinstance(message, dict) else str(message or "")
        role = (message.get("role") if isinstance(message, dict) else "") or ""
        history, latest = split_transcript(text)
        if latest is not None:
            last_user = latest
            continue
        if role.lower() == "system":
            parts.append(text)
        elif role.lower() == "user":
            last_user = text
    if last_user.strip():
        parts.append(last_user)
    return "\n".join(p for p in parts if p and p.strip())


def _sanitize_outgoing(messages: list) -> list:
    """Word-list input check and history quarantine. Sync, no classifier."""
    from core.blocklist import note, sanitize_messages
    from core.safety import MinorsBlocked, prepare_llm_messages

    prepared = prepare_llm_messages(messages)
    prepared, check = sanitize_messages(prepared)
    if check:
        note(check)
        raise MinorsBlocked(check)
    _output_context.set(_request_output_context(prepared))
    return prepared



def _drop_or_rebuild_history(prepared: list, drop_indices: set[int]) -> list:
    """Drop pure history messages; rebuild combined turns so the latest stays.

    History and the latest user line often share one message, split by
    === MOST RECENT MESSAGE ===. Flagged or unclassified history must not
    take that latest turn with it. An empty turn is never sent.
    """
    from core.blocklist import split_transcript
    from core.safety import message_text

    rebuilt: list = []
    for i, message in enumerate(prepared):
        if i not in drop_indices:
            rebuilt.append(message)
            continue
        text = message_text(message) if isinstance(message, dict) else str(message or "")
        _hist, latest = split_transcript(text)
        if latest is None:
            continue
        kept = (latest or "").strip()
        if not kept:
            # Never send an empty user turn; skip this message instead.
            continue
        if isinstance(message, dict):
            rebuilt.append({
                **message,
                "content": "=== MOST RECENT MESSAGE ===\n" + kept,
            })
        else:
            rebuilt.append(kept)
    return rebuilt


def _outgoing_messages(messages: list) -> list:
    """Apply the hardcoded safety footer and the minors input check.

    Sync callers get history quarantine and the fail-closed classifier
    when no event loop is running. On a running loop this refuses
    rather than skip the classifier; production uses
    _outgoing_messages_async so the loop is never blocked.
    """
    prepared = _sanitize_outgoing(messages)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_classify_outgoing(prepared))
    from core.blocklist import note
    from core.safety import MinorsBlocked

    note("llm-input-classifier-error")
    raise MinorsBlocked("llm-input-classifier-error")


async def _outgoing_messages_async(messages: list) -> list:
    prepared = _sanitize_outgoing(messages)
    return await _classify_outgoing(prepared)


async def _classify_outgoing(prepared: list) -> list:
    from core.blocklist import note, split_transcript
    from core.classifier import classify_async, extract_images, HISTORY_WINDOW
    from core.safety import MinorsBlocked, is_footer_message, is_user_guard, message_text

    _output_context.set(_request_output_context(prepared))
    images = extract_images(prepared)

    hard_parts: list[str] = []
    history_parts: list[str] = []
    last_user = -1
    scan_rows: list[tuple[int, str, str]] = []
    for i, message in enumerate(prepared):
        if is_footer_message(message) or is_user_guard(message):
            continue
        role = (message.get("role") if isinstance(message, dict) else "") or ""
        text = message_text(message) if isinstance(message, dict) else str(message or "")
        scan_rows.append((i, role, text))
        if role.lower() == "user":
            last_user = len(scan_rows) - 1

    def _hard(role: str, text: str, index: int) -> bool:
        from core.blocklist import _looks_like_persona_or_card
        return (
            index == last_user
            or _looks_like_persona_or_card(text, role)
            or (role.lower() == "system" and "persona" in (text or "").lower())
        )

    # (index, history text, combined-with-latest?)
    history_entries: list[tuple[int, str, bool]] = []
    history_indices: list[int] = []
    for scan_i, (i, role, text) in enumerate(scan_rows):
        if not str(text).strip():
            continue
        hist, latest = split_transcript(text)
        if latest is not None:
            hard_parts.append(latest)
            if hist.strip():
                history_parts.append(hist)
                history_entries.append((i, hist, True))
                history_indices.append(i)
            continue
        if _hard(role, text, scan_i):
            hard_parts.append(text)
        else:
            history_parts.append(text)
            history_entries.append((i, text, False))
            history_indices.append(i)

    stale_entries = history_entries[:-HISTORY_WINDOW] if len(history_entries) > HISTORY_WINDOW else []
    window_entries = history_entries[-HISTORY_WINDOW:]
    # Compute both drop sets against the ORIGINAL list, then apply once.
    # Rebuilding first would shift later indices and drop the wrong
    # messages (including the trailing footer and user guard).
    drop_stale = {i for i, _hist, _combined in stale_entries}
    drop_window: set[int] = set()
    window = [hist for _i, hist, _combined in window_entries]
    hard_text = "\n".join(p for p in hard_parts if p and str(p).strip())
    history_text = "\n".join(window)
    combined = hard_text
    if history_text.strip():
        combined = (hard_text + "\n\nHISTORY:\n" + history_text).strip()

    verdict = await classify_async(combined or hard_text or " ", images=images or None)
    if verdict:
        hard_verdict = await classify_async(hard_text or " ", images=images or None)
        if hard_verdict:
            check = f"llm-input-{hard_verdict}"
            note(check)
            raise MinorsBlocked(check)
        if window_entries:
            note("llm-history-quarantine")
            drop_window = {i for i, _hist, _combined in window_entries}

    drop = drop_stale | drop_window
    if drop:
        prepared = _drop_or_rebuild_history(prepared, drop)
    from core.safety import prepare_llm_messages
    prepared = prepare_llm_messages(prepared)
    _output_context.set(_request_output_context(prepared))
    return prepared


async def _output_blocked(*texts, tool_calls=None) -> bool:
    """True when a model reply or tool call depicts or sexualizes a minor.

    The reply is checked on its own and together with recent RP / card
    context so school or young cues in the scene are not missed.
    Replies are classified as an assistant turn against the latest user text.
    """
    from core.blocklist import is_blocked, match_info, note, split_transcript
    from core.classifier import classify_response_async
    import json as _json

    parts = [str(t) for t in texts if t]
    for call in tool_calls or []:
        if not isinstance(call, dict):
            parts.append(str(call))
            continue
        parts.append(str(call.get("name") or ""))
        args = call.get("arguments")
        if isinstance(args, str):
            parts.append(args)
        elif args:
            try:
                parts.append(_json.dumps(args, ensure_ascii=False))
            except Exception:
                parts.append(str(args))
    reply = "\n".join(parts)
    context = _output_context.get("")
    combined = (context + "\n" + reply).strip() if context else reply
    if is_blocked(*parts) or (combined and match_info(combined, force_context=True)[1]):
        note("llm-output")
        return True
    # Latest user line from the stored request context, if present.
    prompt = ""
    if context:
        _hist, latest = split_transcript(context)
        prompt = (latest or context).strip()
    check = await classify_response_async(reply, prompt=prompt)
    if check:
        note(check)
        return True
    return False


async def _build_payload(
    messages: list,
    model: str,
    max_tokens: int,
    temperature: float,
    *,
    reasoning: bool | None = None,
) -> dict:
    messages = await _outgoing_messages_async(messages)
    payload = {
        "model": _safe_chat_model(model),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if reasoning is not None:
        payload["_reasoning"] = "on" if reasoning else "off"
    # Off is the settings default. "none" asks for the answer only so a trace
    # does not spend the reply budget or land in chat. On leaves the provider's
    # own thinking in place, and the trace is still kept out of the reply.
    if not _reasoning_for(payload):
        payload["reasoning_effort"] = "none"
        if _provider_backend() == "openrouter":
            payload["reasoning"] = {"effort": "none", "exclude": True}
    return payload


def reasoning_enabled() -> bool:
    """Live settings read. Blank stays off for the provider that is running."""
    key = {
        "openrouter": "reasoning_openrouter",
        "ollama": "reasoning_ollama",
    }.get(_provider_backend(), "reasoning_cloud")
    stored = ""
    try:
        from core.secrets import public_value
        stored = public_value(key)
    except Exception:
        stored = ""
    from core.admin_tools import flag_on
    return flag_on(stored, "")


def _reasoning_request_rejected(payload: dict, error: str) -> bool:
    low = (error or "").lower()
    if "reasoning_effort" in payload and "reasoning_effort" in low:
        return True
    return "reasoning" in payload and "reasoning" in low


def _reasoning_must_stay_on(error: str) -> bool:
    """True when this model rejects a request that turns reasoning off."""
    low = (error or "").lower()
    if "cannot be disabled" in low:
        return True
    return "mandatory" in low and "reasoning" in low


def _enable_reasoning(payload: dict, visible: int) -> None:
    """Keep reasoning on and leave room for the visible answer."""
    payload.pop("reasoning_effort", None)
    payload["max_tokens"] = max(int(payload.get("max_tokens") or 0), int(visible) + 1024)
    payload["reasoning"] = {"enabled": True}


def _relax_reasoning(payload: dict, visible: int) -> None:
    """Second attempt after a thinking trace ate the visible reply.

    OpenRouter bills the trace against max_tokens. Add room for the answer
    and cap the trace. Ollama just drops the field it refused.
    """
    if _provider_backend() == "openrouter":
        payload.pop("reasoning_effort", None)
        payload["max_tokens"] = int(visible) + 1024
        payload["reasoning"] = {"max_tokens": 256, "exclude": True}
        return
    payload.pop("reasoning_effort", None)


def _message_text(msg: dict | None) -> str:
    """Visible reply. A think tag, or content that is only the trace, is empty."""
    if not isinstance(msg, dict):
        return ""
    text = _as_text(msg.get("content")).strip()
    reasoning = _reasoning_text(msg)
    if text and reasoning and text == reasoning:
        return ""
    return text


def _swallowed_answer(data: dict) -> bool:
    """True when OpenRouter spent the cap on a thinking trace and left no reply."""
    if not isinstance(data, dict) or data.get("error"):
        return False
    msg = _extract_message(data)
    if not isinstance(msg, dict):
        return False
    if _message_text(msg) or _tool_calls_from_message(msg):
        return False
    if _reasoning_text(msg):
        return True
    raw = _raw_content_text(msg.get("content"))
    if raw and raw != _as_text(msg.get("content")).strip():
        return True
    details = msg.get("reasoning_details")
    if isinstance(details, list) and details:
        return True
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    token_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage.get("completion_tokens_details"), dict)
        else {}
    )
    for raw in (token_details.get("reasoning_tokens"), usage.get("reasoning_tokens")):
        try:
            if int(raw or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return _choice_finish_reason(data).strip().lower() in {"length", "max_tokens"}


def _quiet_reasoning(payload: dict) -> None:
    """Ask again for the reply only. Do not turn a long trace on."""
    payload["reasoning_effort"] = "none"
    if _provider_backend() == "openrouter":
        payload["reasoning"] = {"effort": "none", "exclude": True}
        return
    payload.pop("reasoning", None)


def _ollama_think_rejected(error: str) -> bool:
    """True when this model or server refuses the think field."""
    low = (error or "").lower()
    if "think" not in low:
        return False
    return any(bit in low for bit in ("not support", "unsupported", "unknown", "invalid", "does not"))


def _after_http_error(payload: dict, visible: int, stage: int, error: str) -> int | None:
    """Next attempt stage, or None when this error should be returned."""
    if (
        _provider_backend() == "ollama"
        and stage == 0
        and payload.get("_ollama_think") != "omit"
        and _ollama_think_rejected(error)
    ):
        payload["_ollama_think"] = "omit"
        try:
            from core import ollama_thinking
            ollama_thinking.forget_thinking(_api_url(), str(payload.get("model") or ""))
        except Exception:
            pass
        return 1
    if payload.get("_reasoning") == "off" or not reasoning_enabled():
        if stage > 0:
            return None
        if _reasoning_must_stay_on(error) or _reasoning_request_rejected(payload, error):
            _quiet_reasoning(payload)
            return 1
        return None
    if _provider_backend() == "openrouter" and _reasoning_must_stay_on(error):
        reasoning = payload.get("reasoning")
        if isinstance(reasoning, dict) and reasoning.get("enabled") is True:
            return None
        _enable_reasoning(payload, visible)
        return 1
    if stage == 0 and _reasoning_request_rejected(payload, error):
        _relax_reasoning(payload, visible)
        return 1
    if stage == 1 and _provider_backend() == "openrouter" and "reasoning" in (error or "").lower():
        payload.pop("reasoning", None)
        payload.pop("reasoning_effort", None)
        return 2
    return None


def _after_http_ok(payload: dict, visible: int, stage: int, data: dict) -> int | None:
    """Retry once when the visible reply was lost to a thinking trace."""
    if stage != 0 or not _swallowed_answer(data):
        return None
    if _provider_backend() == "ollama":
        # Only a request that sent think can be asked again with it off.
        # A model that thinks inline without the field cannot be turned off.
        if not payload.get("_ollama_sent_think"):
            return None
        # The trace is already in the window. One more call writes the reply.
        # Think stays off so this pass cannot spend the cap on another trace.
        payload["_reasoning"] = "off"
        return 1
    if payload.get("_reasoning") == "off" or not reasoning_enabled():
        if _provider_backend() != "openrouter":
            return None
        _quiet_reasoning(payload)
        return 1
    if _provider_backend() == "openrouter":
        _relax_reasoning(payload, visible)
        return 1
    return None


def _request_body(payload: dict) -> dict:
    # Never request image/video modalities. Providers that accept a
    # modalities field would otherwise generate pictures.
    return {
        key: value
        for key, value in payload.items()
        if not str(key).startswith("_")
        and str(key) not in {
            "modal" + "ities",
            "response_" + "modalities",
            "response" + "Modalities",
        }
    }


def _ollama_native_url(url: str) -> str | None:
    """Native /api/chat for an Ollama URL. None when the URL is some other
    OpenAI-style endpoint (a proxy), which keeps the /v1 request shape."""
    text = (url or "").strip().rstrip("/")
    suffix = "/v1/chat/completions"
    if text.endswith(suffix):
        return text[: -len(suffix)] + "/api/chat"
    if text.endswith("/api/chat"):
        return text
    return None


def _ollama_v1_url(url: str) -> str:
    text = (url or "").strip().rstrip("/")
    if text.endswith("/api/chat"):
        return text[: -len("/api/chat")] + "/v1/chat/completions"
    return url


def _ollama_capabilities(url: str, model: str):
    """Cached /api/show capabilities. None when unknown."""
    try:
        from core import ollama_thinking
        return ollama_thinking.capabilities(url, model)
    except Exception:
        return None


def _ollama_reasoning_mode(payload: dict) -> str:
    """auto / on / off / level for this request.

    A forced call (background jobs, the reply-only retry) wins. Otherwise
    the model's saved mode, else the Reasoning switch (on = auto).
    """
    forced = (payload or {}).get("_reasoning")
    if forced == "off":
        return "off"
    if forced == "on":
        return "on"
    from core import ollama_thinking
    return ollama_thinking.model_mode(str(payload.get("model") or ""), reasoning_enabled())


def _ollama_think_value(url: str, payload: dict):
    """think for this request: True, False, a level, or None to leave it out."""
    if payload.get("_ollama_think") == "omit":
        return None
    from core import ollama_thinking
    caps = _ollama_capabilities(url, str(payload.get("model") or ""))
    return ollama_thinking.resolve_think(_ollama_reasoning_mode(payload), caps)


def _thinking_sampling(payload: dict) -> dict:
    """Sampling for a thinking turn. Qwen-family defaults, then saved overrides."""
    temperature = payload.get("temperature", 0.7)
    try:
        from core.ollama_thinking import thinking_sampling
        return thinking_sampling(str(payload.get("model") or ""), temperature)
    except Exception:
        return {"temperature": temperature}


def _ollama_native_gone(status: int, body: str) -> bool:
    """True when /api/chat itself is missing (a proxy), not the model."""
    if status not in (404, 405):
        return False
    low = (body or "").lower()
    return "model" not in low


def _native_message(msg: dict) -> dict:
    """One OpenAI message as an Ollama /api/chat message."""
    role = str(msg.get("role") or "user")
    content = msg.get("content")
    if isinstance(content, list):
        texts: list[str] = []
        images: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                texts.append(str(part.get("text") or ""))
                continue
            if part.get("type") != "image_url":
                continue
            image = part.get("image_url")
            raw = str(image.get("url") or "") if isinstance(image, dict) else ""
            if raw.lower().startswith("data:") and "," in raw:
                images.append(raw.split(",", 1)[1])
        out = {"role": role, "content": "\n".join(texts).strip()}
        if images:
            out["images"] = images
        return out
    return {"role": role, "content": "" if content is None else str(content)}


def _ollama_request(url: str, payload: dict) -> tuple[str, dict]:
    """Request body for this provider. Ollama gets a small context window."""
    if _provider_backend() != "ollama":
        return url, _request_body(payload)
    think = _ollama_think_value(url, payload)
    payload["_ollama_sent_think"] = think
    native_url = None if payload.get("_ollama_api") == "v1" else _ollama_native_url(url)
    if native_url is None:
        # OpenAI-style fallback. No num_ctx here; think is reasoning_effort.
        body = _request_body(payload)
        body.pop("reasoning", None)
        if think is None:
            body.pop("reasoning_effort", None)
        elif think is False:
            body["reasoning_effort"] = "none"
        else:
            body.pop("reasoning_effort", None)
            if isinstance(think, str):
                body["reasoning_effort"] = think
            opts = _thinking_sampling(payload)
            body["temperature"] = opts.get("temperature", body.get("temperature"))
            if "top_p" in opts:
                body["top_p"] = opts["top_p"]
        return _ollama_v1_url(url), body
    body: dict = {
        "model": payload.get("model") or "",
        "messages": [
            _native_message(msg)
            for msg in (payload.get("messages") or [])
            if isinstance(msg, dict)
        ],
        "stream": False,
        "options": {
            "num_ctx": _ollama_num_ctx(),
            "temperature": payload.get("temperature", 0.7),
            "num_predict": int(payload.get("max_tokens") or 64),
        },
    }
    if payload.get("tools"):
        body["tools"] = payload["tools"]
    # Off asks for the reply in content. On separates the trace. Thinking and
    # the reply share num_predict, so a trace that uses the whole cap is
    # followed by one reply-only call. A model whose /api/show does not list
    # thinking never gets the field.
    if think is not None:
        body["think"] = think
        if think is not False:
            body["options"]["num_predict"] += _think_room()
            body["options"].update(_thinking_sampling(payload))
    return native_url, body


def _openai_from_native(data: dict) -> dict:
    """Ollama /api/chat JSON as the choices shape the rest of the client reads."""
    if not isinstance(data, dict) or "choices" in data:
        return data
    if data.get("error") and not isinstance(data.get("message"), dict):
        return data
    msg = data.get("message") if isinstance(data.get("message"), dict) else {}
    message: dict = {
        "role": msg.get("role") or "assistant",
        "content": msg.get("content") or "",
    }
    thinking = str(msg.get("thinking") or "").strip()
    if thinking:
        message["reasoning"] = thinking
    calls = []
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        args = fn.get("arguments")
        if not isinstance(args, str):
            try:
                args = json.dumps(args or {})
            except TypeError:
                args = "{}"
        calls.append({
            "id": str(tc.get("id") or ""),
            "type": "function",
            "function": {"name": str(fn.get("name") or ""), "arguments": args},
        })
    if calls:
        message["tool_calls"] = calls
    return {
        "choices": [{
            "message": message,
            "finish_reason": str(data.get("done_reason") or ""),
        }]
    }


def _sync_post(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
    visible = int(payload.get("max_tokens") or 256)
    stage = 0
    for _ in range(3):
        send_url, body = _ollama_request(url, payload)
        try:
            response = requests.post(
                send_url, json=body, headers=headers, timeout=timeout
            )
        except Exception as e:
            return {"error": _format_transport_error(e, send_url)}

        if response.status_code != 200:
            if (
                _provider_backend() == "ollama"
                and payload.get("_ollama_api") != "v1"
                and _ollama_native_url(url) is not None
                and _ollama_native_gone(response.status_code, response.text)
            ):
                payload["_ollama_api"] = "v1"
                continue
            err = _format_http_error(
                response.status_code,
                response.text,
                payload.get("model", "?"),
                send_url,
            )
            nxt = _after_http_error(payload, visible, stage, err)
            if nxt is None:
                return {"error": err}
            stage = nxt
            continue

        try:
            data = _openai_from_native(response.json())
        except Exception as e:
            return {
                "error": (
                    f"{_provider_label()} returned non-JSON (HTTP {response.status_code}). "
                    f"{e}. Body[:300]={response.text[:300]!r}"
                )
            }
        if payload.get("_reasoning") != "off":
            _keep_reasoning(data, payload.get("model", ""))
        nxt = _after_http_ok(payload, visible, stage, data)
        if nxt is None:
            if isinstance(data, dict) and data.get("error") and not data.get("choices"):
                return {"error": _error_text(data.get("error"))}
            return data
        stage = nxt
    return {"error": f"{_provider_label()} request failed. model=`{payload.get('model', '?')}`"}


async def _async_post(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
    visible = int(payload.get("max_tokens") or 256)
    stage = 0
    if _provider_backend() == "ollama":
        # /api/show is a blocking lookup. Run it off the event loop once;
        # the request below reads the cached answer.
        model = str(payload.get("model") or "")
        await asyncio.get_running_loop().run_in_executor(
            None, _ollama_capabilities, url, model
        )
    for _ in range(3):
        send_url = url
        try:
            timeout_cfg = aiohttp.ClientTimeout(total=timeout)
            send_url, body = _ollama_request(url, payload)
            async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
                async with session.post(
                    send_url, json=body, headers=headers
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        if (
                            _provider_backend() == "ollama"
                            and payload.get("_ollama_api") != "v1"
                            and _ollama_native_url(url) is not None
                            and _ollama_native_gone(resp.status, text)
                        ):
                            payload["_ollama_api"] = "v1"
                            continue
                        err = _format_http_error(
                            resp.status,
                            text,
                            payload.get("model", "?"),
                            send_url,
                        )
                        nxt = _after_http_error(payload, visible, stage, err)
                        if nxt is None:
                            return {"error": err}
                        stage = nxt
                        continue
                    try:
                        data = await resp.json(content_type=None)
                    except Exception as e:
                        return {
                            "error": (
                                f"{_provider_label()} returned non-JSON (HTTP {resp.status}). "
                                f"{e}. Body[:300]={text[:300]!r}"
                            )
                        }
        except Exception as e:
            return {"error": _format_transport_error(e, send_url)}
        data = _openai_from_native(data)
        if payload.get("_reasoning") != "off":
            _keep_reasoning(data, payload.get("model", ""))
        nxt = _after_http_ok(payload, visible, stage, data)
        if nxt is None:
            if isinstance(data, dict) and data.get("error") and not data.get("choices"):
                return {"error": _error_text(data.get("error"))}
            return data
        stage = nxt
    return {"error": f"{_provider_label()} request failed. model=`{payload.get('model', '?')}`"}


_REASONING_PARTS = {
    "reasoning",
    "thinking",
    "thought",
    "redacted_thinking",
    "reasoning_text",
}
_THINK_CLOSERS = (
    "</think>",
    "</thinking>",
    "</thought>",
    "</thoughts>",
    "</reasoning>",
    "</analysis>",
)
_THINK_CLOSED = re.compile(
    r"<(?:think|thinking|thought|thoughts|reasoning|analysis)\b[^>]*>"
    r"[\s\S]*?"
    r"</(?:think|thinking|thought|thoughts|reasoning|analysis)>",
    re.IGNORECASE,
)
_THINK_OPEN = re.compile(
    r"<(?:think|thinking|thought|thoughts|reasoning|analysis)\b[^>]*>[\s\S]*$",
    re.IGNORECASE,
)
_ANSWER_LABEL = re.compile(
    r"(?im)^(?:response|final answer|finished reply)\s*:\s*",
)
_SCRATCH_LINE = re.compile(
    r"^(?:"
    r"the user\b|"
    r"okay,\s+(?:the|so|i|let)|"
    r"let me (?:think|check the|look at|see|figure)|"
    r"i need to\b|"
    r"i should\b|"
    r"looking at (?:the|this|their)|"
    r"my (?:plan|response|reply)\b|"
    r"reasoning:|"
    r"thoughts?:"
    r")",
    re.IGNORECASE,
)
# A fill that echoes its own worksheet. A bare Title: / Plan: line is phase 1 and stays.
_WORKSHEET_OPEN = re.compile(
    r"^(?:here(?:'s|’s| is) a thinking process\b|thinking process\s*:|"
    r"(?:\d+[.)]\s+)?(?:\*\*)?analyze user input\b)",
    re.IGNORECASE,
)
_TODAY_STAMP = re.compile(
    r"^Today:\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
    re.IGNORECASE,
)
_THREAD_LABEL = re.compile(r"^Thread:\s*\S", re.IGNORECASE)
_INSTRUCTION_ECHO = re.compile(
    r"^(?:"
    r"write the next part of the assignment\b|"
    r"do not announce a thread\b|"
    r"call duckduckgo when\b|"
    r"every search has to name\b|"
    r"do not invent reviews\b|"
    r"a writing assignment is written\b|"
    r"if the notes are empty\b|"
    r"end on a complete sentence\b|"
    r"when the plan is finished\b|"
    r"talk to write about\s*:|"
    r"the reply is only the next post\b|"
    r"do not describe your steps\b|"
    r"do not name anyone who is not\b|"
    r"assignment to name and plan\b|"
    r"if you cannot call the tool\b|"
    r"answer the message under most recent message\b|"
    r"write the next point that is not in already posted\b|"
    r"do not put done in that post\b|"
    r"if every planned point is already posted\b|"
    r"do not add a topic\b|"
    r"do not invent scores, dates, or news\b|"
    r"if those results are empty, still write the plan\b|"
    r"do not post a lookup status\b|"
    r"when continue after is a sentence\b|"
    r"when continue after says no finished sentence\b|"
    r"do not repeat these labels\b"
    r")",
    re.IGNORECASE,
)
_FIELD_BULLET = re.compile(
    r"^(?:subject|plan|already posted|notes|instructions|user input|title|search results|continue after)\b",
    re.IGNORECASE,
)
# Fill-prompt headers on their own line. A phase-1 "Plan:" line does not match.
_LABEL_ONLY = re.compile(
    r"^(?:subject|already posted|notes|search results|continue after)\s*:\s*"
    r"(?:|\(none\)|\(nothing yet\)|\(no finished sentence\))\s*$|"
    r"^plan\.\s+stay on it\s*:?\s*$",
    re.IGNORECASE,
)
# Nemotron content-safety models print this instead of an answer.
# openrouter/free lands on one of those models at random.
_SAFETY_LABEL_LINE = re.compile(
    r"^(?:(?:user|response)\s+safety|safety\s+categories)\s*:",
    re.IGNORECASE,
)
# The memory model rewrites that label into a sentence and stores it.
_USER_IS_SAFE = re.compile(
    r"(?:^|(?<=[.!?])\s+|\n)\s*(?:the )?user is safe\.\s*",
    re.IGNORECASE,
)


def drop_safety_labels(text: str) -> str:
    """Drop a safety-classifier line. It is not a reply."""
    kept = [
        line
        for line in (text or "").splitlines()
        if not _SAFETY_LABEL_LINE.match(line.strip())
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def drop_safety_preamble(text: str) -> str:
    """Drop the classifier line and the one-sentence rewrite of it."""
    cleaned = _USER_IS_SAFE.sub(" ", drop_safety_labels(text))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", cleaned)).strip()


def _parts_text(content, *, keep_reasoning: bool) -> str:
    """Join content parts. Reasoning parts stay out of the posted reply."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                kind = str(item.get("type") or "text").strip().lower()
                if not keep_reasoning and kind in _REASONING_PARTS:
                    continue
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(content)


def _strip_think(text: str) -> str:
    """Drop think tags. Text after the last closer is the reply."""
    if not text:
        return ""
    lower = text.lower()
    cut = -1
    closer_len = 0
    for closer in _THINK_CLOSERS:
        idx = lower.rfind(closer)
        if idx > cut:
            cut = idx
            closer_len = len(closer)
    if cut != -1:
        text = text[cut + closer_len :]
    text = _THINK_CLOSED.sub("", text)
    text = _THINK_OPEN.sub("", text)
    return text.strip()


def _as_text(content) -> str:
    """Model content as text. OpenRouter may return a list of parts."""
    return _strip_think(_parts_text(content, keep_reasoning=False))


def _raw_content_text(content) -> str:
    """Content before think-stripping, so a typed tool call can still be read."""
    return _parts_text(content, keep_reasoning=True).strip()


def _content_reasoning(content) -> str:
    """Reasoning parts OpenRouter puts beside the reply text."""
    if not isinstance(content, list):
        return ""
    bits: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip().lower()
        if kind not in _REASONING_PARTS:
            continue
        text = _strip_think(
            _parts_text(item.get("text") or item.get("content"), keep_reasoning=True)
        ).strip()
        if text and text not in bits:
            bits.append(text)
    return "\n".join(bits).strip()


def _reasoning_text(msg: dict | None) -> str:
    if not isinstance(msg, dict):
        return ""
    chunks: list[str] = []
    for key in ("reasoning", "reasoning_content"):
        text = _strip_think(_parts_text(msg.get(key), keep_reasoning=True)).strip()
        if text and text not in chunks:
            chunks.append(text)
    extra = _content_reasoning(msg.get("content"))
    if extra and extra not in chunks:
        chunks.append(extra)
    details = msg.get("reasoning_details")
    if isinstance(details, list):
        for item in details:
            if isinstance(item, str) and item.strip() and item.strip() not in chunks:
                chunks.append(item.strip())
            elif isinstance(item, dict):
                bit = item.get("text") or item.get("summary") or item.get("content")
                text = _strip_think(_parts_text(bit, keep_reasoning=True)).strip()
                if text and text not in chunks:
                    chunks.append(text)
    return "\n".join(chunks).strip()


_THINK_BODY = re.compile(
    r"<(?:think|thinking|thought|thoughts|reasoning|analysis)\b[^>]*>"
    r"([\s\S]*?)"
    r"</(?:think|thinking|thought|thoughts|reasoning|analysis)>",
    re.IGNORECASE,
)


def _trace_text(msg: dict | None) -> str:
    """Reasoning fields plus any think block written into the reply."""
    chunks: list[str] = []
    reasoned = _reasoning_text(msg)
    if reasoned:
        chunks.append(reasoned)
    raw_bits: list[str] = []
    if isinstance(msg, dict):
        raw_bits.append(_raw_content_text(msg.get("content")))
        for key in ("reasoning", "reasoning_content"):
            raw_bits.append(_parts_text(msg.get(key), keep_reasoning=True))
    for raw in raw_bits:
        closed = False
        for inner in _THINK_BODY.findall(raw or ""):
            bit = inner.strip()
            if bit and bit not in chunks:
                chunks.append(bit)
                closed = True
        if closed:
            continue
        match = _THINK_UNCLOSED.search(raw or "")
        if not match:
            continue
        bit = match.group(1).strip()
        if bit and bit not in chunks:
            chunks.append(bit)
    return "\n\n".join(chunks).strip()


_THINK_UNCLOSED = re.compile(
    r"<(?:think|thinking|thought|thoughts|reasoning|analysis)\b[^>]*>([\s\S]*)$",
    re.IGNORECASE,
)


def _keep_reasoning(data: dict, model: str) -> None:
    """Hand a separate trace to the reasoning window. The reply stays in chat."""
    if not isinstance(data, dict) or data.get("error"):
        return
    msg = _extract_message(data)
    text = _trace_text(msg)
    if not text:
        return
    if len(text) > 8000:
        text = text[:7999].rstrip() + "…"
    try:
        from core.gui_bridge import reasoning
        reasoning(_provider_label(), str(model or ""), text)
    except Exception:
        pass


def _without_scratchpad(text: str) -> str:
    """Drop a leading planning paragraph when a later paragraph is the reply."""
    raw = (text or "").strip()
    if not raw:
        return ""
    parts = [part.strip() for part in re.split(r"\n\s*\n", raw) if part.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        if len(parts[0]) > 80 and _SCRATCH_LINE.match(parts[0]):
            return ""
        return raw
    if all(_SCRATCH_LINE.match(part) for part in parts):
        return ""
    kept = list(parts)
    while len(kept) > 1 and _SCRATCH_LINE.match(kept[0]):
        kept.pop(0)
    return "\n\n".join(kept).strip()


def _bare_prompt_line(line: str) -> str:
    """Drop a bullet, a number, and surrounding emphasis from one echoed line."""
    text = (line or "").strip()
    text = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", text)
    return text.strip("*_` ").strip()


def _worksheet_continue(line: str) -> bool:
    """Lines that belong to a thinking-process block, not the post."""
    if not line.strip():
        return True
    if not re.match(r"^(?:[-*+]|\d+[.)])\s+", line.strip()):
        return False
    bare = _bare_prompt_line(line)
    if not bare:
        return True
    if _INSTRUCTION_ECHO.match(bare) or _FIELD_BULLET.match(bare):
        return True
    if _WORKSHEET_OPEN.match(bare):
        return True
    return False


def _subject_echo_cut(lines: list[str]) -> int | None:
    """Index of a Today: weekday line that is the thread-subject template."""
    for index, line in enumerate(lines):
        if not _TODAY_STAMP.match(line.strip()):
            continue
        for nxt in lines[index + 1 : index + 4]:
            stripped = nxt.strip()
            if not stripped:
                continue
            if _THREAD_LABEL.match(stripped):
                return index
            break
    return None


def _drop_worksheet(text: str) -> str:
    """Drop an echoed fill worksheet. Phase 1 Title: and Plan: lines stay."""
    raw = (text or "").strip()
    if not raw:
        return ""
    lines = raw.splitlines()
    cut = _subject_echo_cut(lines)
    if cut is not None:
        lines = lines[:cut]
    kept: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if _WORKSHEET_OPEN.match(stripped) or _WORKSHEET_OPEN.match(_bare_prompt_line(stripped)):
            index += 1
            while index < len(lines) and _worksheet_continue(lines[index]):
                index += 1
            continue
        bare = _bare_prompt_line(stripped)
        if stripped and (_INSTRUCTION_ECHO.match(bare) or _LABEL_ONLY.match(bare)):
            index += 1
            continue
        kept.append(lines[index])
        index += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _visible_reply(text: str) -> str:
    """The finished reply. Think tags, a scratchpad, and an echoed worksheet stay out."""
    text = _strip_think(text or "")
    match = None
    for match in _ANSWER_LABEL.finditer(text):
        pass
    if match is not None:
        tail = text[match.end() :].strip()
        if tail:
            text = tail
    return drop_safety_labels(_drop_worksheet(_without_scratchpad(text)))


def _extract_content(data: dict) -> str | None:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if content is None:
        return None
    return _as_text(content)


def _choice_finish_reason(data: dict) -> str:
    try:
        return str(data["choices"][0].get("finish_reason") or "")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _extract_message(data: dict) -> dict | None:
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None
    # Ignore provider image-generation payloads (OpenRouter message.images).
    if "images" in msg:
        msg = {key: value for key, value in msg.items() if key != "images"}
    return msg


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
    low = _error_text(err).lower()
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
    low = _error_text(err).lower()
    if "reasoning_effort" in kwargs and (
        "reasoning_effort" in low or _reasoning_must_stay_on(low)
    ):
        kwargs.pop("reasoning_effort", None)
        changed = True
    if "reasoning_format" in kwargs and "reasoning_format" in low:
        kwargs.pop("reasoning_format", None)
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


def _messages_have_images(messages) -> bool:
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                return True
    return False


def _images_rejected(err: str) -> bool:
    """Drop pictures and retry the same turn as text.

    A text model answers 400 when the user message contains a picture.
    Ollama also answers 400 when the picture's vision tokens overflow the
    loaded context (often 4096). That body is rewritten into the
    "prompt too long" line before this check runs, so the status is gone.
    """
    low = _error_text(err).lower()
    if any(s in low for s in ("429", "rate limit", "rate_limit", "tokens per", "request too large")):
        return False
    if any(s in low for s in ("image", "vision", "multimodal", "image_url")):
        return True
    if any(
        s in low
        for s in (
            "prompt too long",
            "exceeded model limit",
            "exceeds the available context",
            "exceed_context_size",
            "available context size",
        )
    ):
        return True
    return "400" in low or "bad request" in low


def _flatten_image_messages(messages) -> bool:
    """Turn image parts back into the text transcript. Returns True if any changed."""
    if not isinstance(messages, list):
        return False
    changed = False
    for index, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        texts = [
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        messages[index] = {**msg, "content": "\n".join(texts).strip()}
        changed = True
    return changed


def _attach_images(messages: list, images: list) -> list:
    if not images:
        return messages
    attached = False
    out = []
    for msg in messages:
        if (
            not attached
            and isinstance(msg, dict)
            and msg.get("role") == "user"
            and isinstance(msg.get("content"), str)
        ):
            out.append(
                {
                    **msg,
                    "content": [
                        {"type": "text", "text": msg["content"]},
                        *images,
                    ],
                }
            )
            attached = True
        else:
            out.append(msg)
    return out


# ---------------------------------------------------------------------------
# Format system prompt (plain text — no required thoughts tags)
# ---------------------------------------------------------------------------
def _job_name(job: str | None, concise: bool) -> str:
    """A missing job is a channel turn. Local chat passes plain itself."""
    del concise
    picked = (job or "").strip()
    return picked or "channel"


def _format_system_prompt(
    memory_block: str = "",
    *,
    job: str = "channel",
    tool_note: str = "",
    extra: str = "",
    voice: str | None = None,
    tools: list | None = None,
) -> str:
    """One system message. The user turn is the work, so memory is not repeated here."""
    del memory_block, tool_note
    spoken = get_personality_prompt() if voice is None else voice
    return system_text(
        job=(job or "channel").strip() or "channel",
        tools=tools,
        voice=spoken or "",
        admin=extra or "",
        room=REPLY_MAX_CHARS,
    )


def chat_messages(
    prompt: str,
    *,
    memory_block: str = "",
    tools: list | None = None,
    extra_system: str | None = None,
    include_tool_note: bool = True,
    concise: bool = True,
    job: str | None = None,
    images: list | None = None,
    voice: str | None = None,
) -> list:
    """System message first, then the user turn. Pictures stay on the user turn."""
    chosen = _job_name(job, concise)
    system_prompt = _format_system_prompt(
        memory_block,
        job=chosen,
        tools=tools if tools and include_tool_note else None,
        extra=extra_system or "",
        voice=voice,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    return _attach_images(messages, list(images or []))


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
    reasoning_format: str | None = None,
    stream: bool = False,
    tools: list | None = None,
) -> str | dict:
    """
    On success, returns {"content", "finish_reason"} and, when tools were
    sent, "tool_calls". A stream returns the joined text. Failure is
    {"error": "..."}. reasoning_effort defaults to "none" so native
    chain-of-thought stays out of the reply.
    """
    messages = await _outgoing_messages_async(messages)
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
        "model": _safe_chat_model(model),
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
        "top_p": top_p,
        "stream": stream,
        "stop": None,
    }
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    if reasoning_format:
        kwargs["reasoning_format"] = reasoning_format
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
            text = "".join(parts)
            from core.blocklist import REFUSAL
            return REFUSAL if await _output_blocked(text) else text
        choice = completion.choices[0]
        msg = choice.message
        content = getattr(msg, "content", None) or ""
        if not isinstance(content, str):
            content = str(content or "")
        reasoned = getattr(msg, "reasoning", None) or ""
        if not isinstance(reasoned, str):
            reasoned = str(reasoned or "")
        out = {
            "content": content,
            "reasoning": reasoned.strip(),
            "finish_reason": getattr(choice, "finish_reason", None) or "",
        }
        if kw.get("tools"):
            out["tool_calls"] = _tool_calls_from_message(msg)
        from core.blocklist import REFUSAL
        if await _output_blocked(out.get("content"), tool_calls=out.get("tool_calls")):
            out["content"] = REFUSAL
            out["tool_calls"] = []
        return out

    try:
        return await _create(kwargs)
    except Exception as e:
        err = str(e) or type(e).__name__
        # Drop reasoning_effort, pictures, or tools if this model rejects
        # them, then keep the existing single quota retry.
        if _messages_have_images(kwargs.get("messages")) and _images_rejected(err):
            if _flatten_image_messages(kwargs.get("messages")):
                try:
                    return await _create(kwargs)
                except Exception as e_image:
                    err = str(e_image) or type(e_image).__name__
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
    # True when the provider stopped on the token cap. The reply may end mid-sentence.
    cut: bool = False


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
    max_completion_tokens: int | None = None,
    tools: list | None = None,
    extra_system: str | None = None,
    images: list | None = None,
    include_tool_note: bool = True,
    concise: bool = True,
    job: str | None = None,
) -> ModelTurn:
    """
    Chat completion for Discord. `tools` is an OpenAI-style tool list.
    A quota miss returns no tool calls, so the caller does not run tools
    or start another request on that turn.
    """
    from core.blocklist import REFUSAL
    from core.safety import MinorsBlocked

    if max_completion_tokens is None:
        max_completion_tokens = channel_completion_tokens()
    else:
        max_completion_tokens = clamp_completion_tokens(
            max_completion_tokens, CHAT_COMPLETION_TOKENS
        )
    # user_id stays so callers do not change. The pins are already on the user turn.
    messages = chat_messages(
        prompt,
        memory_block="",
        tools=tools,
        extra_system=extra_system,
        include_tool_note=include_tool_note,
        concise=concise,
        job=job,
        images=images,
    )

    model = _model()
    backend = _provider_backend()
    raw = ""
    original = ""
    calls: list = []
    reason = ""
    msg = None

    if backend == "cloud":
        effort = "none"
        parsed = None
        budget = max_completion_tokens
        if reasoning_enabled():
            # Parsed keeps the trace out of the reply text. The extra room
            # is for that trace, so the short reply can still finish.
            effort = None
            parsed = "parsed"
            budget = max_completion_tokens + _think_room()
        try:
            result = await _groq_sdk_call(
                messages,
                model=model,
                max_completion_tokens=budget,
                temperature=0.6,
                top_p=0.95,
                reasoning_effort=effort,
                reasoning_format=parsed,
                stream=False,
                tools=tools or None,
            )
        except MinorsBlocked:
            return ModelTurn(REFUSAL, "", [], False)

        if isinstance(result, dict) and "error" in result:
            error_msg = result["error"]
            if is_quota_error(error_msg):
                return _quota_turn()
            return _error_turn(error_msg)

        if isinstance(result, str):
            msg = {"role": "assistant", "content": result}
            calls = []
            reason = ""
        else:
            msg = {
                "role": "assistant",
                "content": result.get("content") or "",
                "reasoning": result.get("reasoning") or "",
            }
            calls = list(result.get("tool_calls") or [])
            reason = str(result.get("finish_reason") or "")
        _keep_reasoning({"choices": [{"message": msg}]}, model)
        original = _raw_content_text(msg.get("content"))
        raw = _message_text(msg)
        if not original.strip():
            original = raw
    else:
        if not _api_key() and backend != "ollama":
            err = _missing_key_error()
            return ModelTurn(_public_error(err), f"({err})", [], False)

        url = _api_url()
        try:
            payload = await _build_payload(
                messages, model, max_tokens=max_completion_tokens, temperature=0.7
            )
        except MinorsBlocked:
            return ModelTurn(REFUSAL, "", [], False)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None,
            lambda: _sync_post(url, payload, _headers(), timeout=90),
        )

        if (
            isinstance(data, dict)
            and "error" in data
            and _messages_have_images(payload.get("messages"))
            and _images_rejected(data["error"])
            and _flatten_image_messages(payload.get("messages"))
        ):
            data = await loop.run_in_executor(
                None,
                lambda: _sync_post(url, payload, _headers(), timeout=90),
            )

        if isinstance(data, dict) and "error" in data and tools and _tools_unsupported(data["error"]):
            payload.pop("tools", None)
            payload.pop("tool_choice", None)
            data = await loop.run_in_executor(
                None,
                lambda: _sync_post(url, payload, _headers(), timeout=90),
            )

        if isinstance(data, dict) and "error" in data:
            error_msg = _error_text(data["error"])
            if is_quota_error(error_msg):
                return _quota_turn()
            return _error_turn(error_msg)

        reason = _choice_finish_reason(data)
        msg = _extract_message(data)
        calls = _tool_calls_from_message(msg) if msg and payload.get("tools") else []
        if msg is None and not calls:
            error_msg = (
                f"{_provider_label()} bad response shape — no choices[0].message.content. "
                f"keys={list(data.keys()) if isinstance(data, dict) else type(data)} "
                f"| [{_config_snapshot()}]"
            )
            return _error_turn(error_msg)
        original = _raw_content_text(msg.get("content")) if msg else ""
        raw = _message_text(msg) if msg else ""
        if msg and not original.strip():
            original = raw

    if not calls and (original or "").strip():
        # Model typed the call instead of using the tool API. Read it from
        # the raw text, before think tags are taken out of the reply.
        try:
            from core.tools import calls_from_model_text, registered_names
            calls = calls_from_model_text(original, registered_names())
        except Exception:
            calls = []

    if not (raw or "").strip() and _trace_text(msg):
        # The trace belongs in the reasoning window. A dot is not the reply,
        # and a second generation would only think again.
        reply, thoughts = "", ""
        reason = ""
    else:
        reply, thoughts = _finalize_sections(raw)
    quota = reply.strip().lower().startswith("sorry, i'm being rate limited")
    if quota:
        calls = []
    cut = reply_was_cut(reason) and not calls and not quota
    if await _output_blocked(reply, thoughts, tool_calls=calls):
        return ModelTurn(REFUSAL, "", [], False)
    return ModelTurn(reply, thoughts, calls, quota, cut)


async def settle_cut_reply(
    turn: ModelTurn,
    prompt: str,
    user_id: int | None = None,
    *,
    max_completion_tokens: int | None = None,
    images: list | None = None,
    job: str | None = None,
) -> ModelTurn:
    """Post only finished sentences when the token cap stopped the model.

    One repair call when the cut reply contains no finished sentence.
    """
    if max_completion_tokens is None:
        max_completion_tokens = channel_completion_tokens()
    if turn.quota or turn.tool_calls or not turn.cut:
        return turn
    finished = complete_sentences(turn.reply or "")
    if finished:
        turn.reply = finished
        return turn
    repair_prompt = (
        f"{prompt}\n\n"
        "The reply was cut off before a sentence finished. "
        "Write it again in one or two complete sentences. "
        "End on a period, question mark, or exclamation point."
    )
    try:
        repaired = await call_groq_turn(
            repair_prompt,
            user_id=user_id,
            max_completion_tokens=max_completion_tokens,
            tools=None,
            images=images,
            include_tool_note=False,
            job=job,
        )
    except Exception:
        turn.reply = ""
        return turn
    if repaired.quota:
        return repaired
    if repaired.cut:
        repaired.reply = complete_sentences(repaired.reply or "")
    return repaired


async def call_groq(
    prompt: str,
    user_id: int | None = None,
    max_completion_tokens: int | None = None,
    job: str = "plain",
):
    """
    Main chat call. Works for both Groq (SDK) and Ollama (REST).
    Returns: (reply_text, detail_text)
    Public Discord / Local should use reply_text.
    Local chat stays `plain` so it is not told to open a Discord thread.
    """
    turn = await call_groq_turn(
        prompt,
        user_id=user_id,
        max_completion_tokens=max_completion_tokens,
        job=job,
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
    from core.blocklist import REFUSAL
    from core.safety import MinorsBlocked

    max_tokens = tokens_for(max_chars)
    model = _model()
    backend = _provider_backend()

    messages = [{"role": "user", "content": prompt}]

    if backend == "cloud":
        try:
            result = await _groq_sdk_call(
                messages,
                model=model,
                max_completion_tokens=max_tokens,
                temperature=0.9,
                top_p=0.95,
                reasoning_effort="none",
                stream=False,
            )
        except MinorsBlocked:
            return REFUSAL
        if isinstance(result, dict) and "error" in result:
            return ("error", result["error"])
        text = result.get("content") if isinstance(result, dict) else result
        text = _visible_reply(text or "")
        if "<reply>" in text.lower():
            r, _ = parse_sections(text)
            text = r or text
        return REFUSAL if await _output_blocked(text) else text

    if not _api_key() and backend != "ollama":
        return ("error", _missing_key_error())

    url = _api_url()
    try:
        payload = await _build_payload(
            messages, model, max_tokens=max_tokens, temperature=0.9, reasoning=False
        )
    except MinorsBlocked:
        return REFUSAL
    data = await _async_post(url, payload, _headers(), timeout=90)

    if "error" in data:
        return ("error", data["error"])

    msg = _extract_message(data)
    if msg is None:
        return (
            "error",
            f"{_provider_label()} bad response shape (no content). [{_config_snapshot()}]",
        )
    text = _visible_reply(_message_text(msg))
    return REFUSAL if await _output_blocked(text) else text


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
    from core.blocklist import REFUSAL
    from core.safety import MinorsBlocked

    use_model = (model or "").strip() or _model()
    backend = _provider_backend()
    messages = [{"role": "user", "content": prompt}]

    if backend == "cloud":
        # Thinking models spend this budget on a trace and the summary comes
        # back empty. The SDK default asks for the answer only. A model that
        # rejects that retries without it. OpenRouter uses the REST path
        # below and does not send reasoning_effort.
        try:
            result = await _groq_sdk_call(
                messages,
                model=use_model,
                max_completion_tokens=max_completion_tokens,
                temperature=0.4,
                top_p=0.95,
                stream=False,
            )
        except MinorsBlocked:
            return REFUSAL
        if isinstance(result, dict) and "error" in result:
            return f"ERROR: {result['error']}"
        if isinstance(result, dict):
            text = drop_safety_preamble(_strip_think(result.get("content") or ""))
        else:
            text = drop_safety_preamble(_strip_think(result or ""))
        return REFUSAL if await _output_blocked(text) else text

    if not _api_key() and backend != "ollama":
        return f"ERROR: {_missing_key_error()}"

    url = _api_url()
    try:
        payload = await _build_payload(
            messages,
            use_model,
            max_tokens=max_completion_tokens,
            temperature=0.4,
            reasoning=False,
        )
    except MinorsBlocked:
        return REFUSAL
    data = await _async_post(url, payload, _headers(), timeout=90)

    if "error" in data:
        return f"ERROR: {data['error']}"

    msg = _extract_message(data)
    if msg is None:
        return (
            f"ERROR: {_provider_label()} bad response shape (no content). "
            f"[{_config_snapshot()}]"
        )
    # Empty when the model spent the reply on a trace. The caller retries
    # the fold later instead of storing the trace.
    text = drop_safety_preamble(_strip_think(_message_text(msg)))
    return REFUSAL if await _output_blocked(text) else text
