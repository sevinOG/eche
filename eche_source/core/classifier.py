# core/classifier.py
# Fail-closed safety classifier on every LLM request and reply.
# Llama Guard 4, on Groq or OpenRouter, whichever real key is present.
# Covers every footer category. Cannot be turned off by config, persona,
# or users. A test-only hook exists so unit tests can run without a key;
# production never reads that hook from settings or user content.

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

CLASSIFIER_MODEL = "meta-llama/llama-guard-4-12b"
CLASSIFIER_TIMEOUT = 15
CLASSIFIER_DEADLINE = 20
CLASSIFIER_PROMPT = (
    "Cover every safety-footer category. Also cover named fictional "
    "child characters, including storybook, fairytale, and canon minors "
    "identified by name or an age tag. A named fictional child is a "
    "child or minor under these rules."
)
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
HISTORY_WINDOW = 8
MISSING_KEY_MESSAGE = "Safety classifier needs a Groq or OpenRouter key"

# Placeholder values some backends write into GROQ_API_KEY. Never send these.
_PLACEHOLDER_KEYS = {
    "",
    "ollama",
    "changeme",
    "change-me",
    "your_key_here",
    "your-key-here",
    "none",
    "null",
    "undefined",
    "dummy",
    "placeholder",
}

# Test hook only. Default fail-closed. Production never sets this from
# config, persona, or user settings. Only tests/__init__.py should call
# allow_in_tests(True).
_TEST_MODE = False

_CACHE_MAX = 256
_cache: dict[str, str] = {}
_cache_lock = threading.Lock()
_warned_missing_key = False


def allow_in_tests(enabled: bool) -> None:
    """Unit tests only. Production call sites never invoke this."""
    global _TEST_MODE
    _TEST_MODE = bool(enabled)


def tests_are_allowing() -> bool:
    return _TEST_MODE is True


def classifier_model() -> str:
    return CLASSIFIER_MODEL


def _secret(name: str) -> str:
    env = (os.getenv(name) or "").strip()
    if env:
        return env
    try:
        from core.secrets import load_all

        values = load_all() or {}
        if name == "GROQ_API_KEY":
            return (values.get("inf_api_key") or "").strip()
        if name == "OPENROUTER_API_KEY":
            return (values.get("openrouter_api_key") or "").strip()
    except Exception:
        return ""
    return ""


def _real_key(value: str) -> str:
    text = (value or "").strip()
    if not text or text.lower() in _PLACEHOLDER_KEYS:
        return ""
    return text


def _endpoint() -> tuple[str, str] | tuple[None, None]:
    groq = _real_key(_secret("GROQ_API_KEY"))
    if groq:
        return groq, GROQ_URL
    openrouter = _real_key(_secret("OPENROUTER_API_KEY"))
    if openrouter:
        return openrouter, OPENROUTER_URL
    return None, None


def warn_if_unconfigured() -> None:
    """Log once at startup when no real Groq or OpenRouter key is available."""
    global _warned_missing_key
    if tests_are_allowing():
        return
    key, _url = _endpoint()
    if key:
        return
    if _warned_missing_key:
        return
    _warned_missing_key = True
    try:
        from core.gui_bridge import log

        log(MISSING_KEY_MESSAGE, channel="chat")
    except Exception:
        print(f"[classifier] {MISSING_KEY_MESSAGE}", flush=True)


def _parse_verdict(text: str) -> bool | None:
    """True = unsafe (block), False = safe, None = unreadable (fail closed)."""
    if not text or not str(text).strip():
        return None
    lines = [ln.strip().lower() for ln in str(text).replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return None
    first = lines[0]
    if first == "safe" or first.startswith("safe"):
        if first == "safe" or first.startswith("safe "):
            return False
        if first == "safe":
            return False
    if first == "unsafe" or first.startswith("unsafe"):
        return True
    compact = " ".join(lines)
    if compact.startswith("unsafe"):
        return True
    if compact == "safe" or compact.startswith("safe\n") or compact.startswith("safe "):
        return False
    return None


_TEMPLATE_TOKENS = (
    "<END CONVERSATION>",
    "<|eot_id|>",
    "<|eot|>",
    "<|end_of_text|>",
    "<|begin_of_text|>",
    "<|start_header_id|>",
    "<|end_header_id|>",
    "[INST]",
    "[/INST]",
)

_FORGED_VERDICT_RE = re.compile(
    r"(?im)^(?:\s*)(?:safe|unsafe)\b.*$"
)


def escape_template_text(text: str) -> str:
    """Neutralize Llama Guard control tokens and forged verdict blocks."""
    raw = text or ""
    # Break angle-bracket tokens so they cannot close the conversation.
    for token in _TEMPLATE_TOKENS:
        if token in raw:
            raw = raw.replace(token, token.replace("<", "‹").replace(">", "›").replace("|", "¦"))
        upper = token.upper()
        if upper != token and upper in raw:
            raw = raw.replace(upper, upper.replace("<", "‹").replace(">", "›").replace("|", "¦"))
        lower = token.lower()
        if lower != token and lower in raw:
            raw = raw.replace(lower, lower.replace("<", "‹").replace(">", "›").replace("|", "¦"))
    # Any leftover chat-template delimiters.
    raw = raw.replace("<|", "‹¦").replace("|>", "¦›")
    raw = _FORGED_VERDICT_RE.sub(lambda m: "[quoted] " + m.group(0).strip(), raw)
    return raw


def _content_with_images(text: str, images: list | None):
    escaped = escape_template_text(text or "")
    if not images:
        return escaped
    parts = [{"type": "text", "text": escaped}]
    for image in images:
        if not isinstance(image, dict):
            continue
        if image.get("type") == "image_url" or "image_url" in image:
            url = image.get("image_url")
            if isinstance(url, str) and url.strip():
                parts.append({"type": "image_url", "image_url": {"url": url.strip()}})
            elif isinstance(url, dict) and url.get("url"):
                parts.append({"type": "image_url", "image_url": {"url": str(url["url"])}})
            else:
                return None
        elif image.get("type") in {"image", "input_image"}:
            url = image.get("url") or image.get("image_url") or ""
            if isinstance(url, dict):
                url = url.get("url") or ""
            if not url:
                return None
            parts.append({"type": "image_url", "image_url": {"url": str(url)}})
        else:
            return None
    return parts


def _cache_key(messages: list, images: list | None) -> str:
    blob = json.dumps({"messages": messages, "images": images or []}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> str | None:
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    return None


def clear_cache() -> None:
    """Tests only."""
    with _cache_lock:
        _cache.clear()


def _cache_put(key: str, value: str) -> None:
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            # Drop an arbitrary first entry. Exact-content hits stay valid.
            _cache.pop(next(iter(_cache)), None)
        _cache[key] = value


def _post(messages: list) -> str:
    key, url = _endpoint()
    if not key or not url:
        raise RuntimeError("classifier key missing")
    body = json.dumps(
        {
            "model": CLASSIFIER_MODEL,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 64,
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if url.startswith(OPENROUTER_URL):
        headers["HTTP-Referer"] = "https://github.com/sevinOG/eche"
        headers["X-Title"] = "Eche"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=CLASSIFIER_TIMEOUT) as resp:
            raw = resp.read()
            http_resp = resp
    except Exception as exc:
        try:
            from core.audit import api as audit_api
            audit_api("classifier", outcome="error")
        except Exception:
            pass
        raise RuntimeError(f"classifier request failed: {type(exc).__name__}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
        try:
            from core.audit import api_from_http
            api_from_http("classifier", http_resp, payload, outcome="success")
        except Exception:
            pass
        return str(payload["choices"][0]["message"]["content"] or "")
    except Exception as exc:
        try:
            from core.audit import api_from_http
            api_from_http("classifier", http_resp, outcome="error")
        except Exception:
            pass
        raise RuntimeError("classifier response unreadable") from exc


def _verdict_to_check(text: str) -> str:
    try:
        verdict = _parse_verdict(text)
    except Exception:
        return "classifier-error"
    if verdict is None:
        return "classifier-error"
    if verdict:
        return "classifier-unsafe"
    return ""


def _classify_sync(text: str, *, images: list | None = None) -> str:
    """Same fail-closed path as classify_async, but on this thread."""
    if tests_are_allowing():
        return ""
    messages, check = _prepare_messages(
        [{"role": "user", "content": text or ""}],
        images=images,
    )
    if check:
        return check
    key, url = _endpoint()
    if not key or not url:
        return "classifier-error"
    cache_key = _cache_key(messages, images)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        result = _verdict_to_check(_post(messages))
    except Exception:
        result = "classifier-error"
    if result and result != "classifier-error":
        _cache_put(cache_key, result)
    return result


def classify(text: str, *, images: list | None = None) -> str:
    """Sync wrapper. Prefer classify_async on the event loop."""
    if tests_are_allowing():
        return ""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _classify_sync(text, images=images)
    # Already on a loop: do not block it. Fail closed rather than stall.
    # Callers on the loop must use classify_async.
    return "classifier-error"


async def classify_async(text: str, *, images: list | None = None) -> str:
    """Return a check name if the input must be refused, else ''.

    Fail closed: missing key, timeout, HTTP error, unreadable verdict,
    or images the endpoint cannot accept, all refuse.
    """
    if tests_are_allowing():
        return ""
    messages, check = _prepare_messages(
        [{"role": "user", "content": text or ""}],
        images=images,
    )
    if check:
        return check
    return await _classify_messages(messages, images=images)


def _prepare_messages(turns: list, *, images: list | None = None) -> tuple[list, str]:
    prepared: list[dict] = []
    for i, turn in enumerate(turns):
        role = (turn.get("role") if isinstance(turn, dict) else "user") or "user"
        text = ""
        if isinstance(turn, dict):
            content = turn.get("content")
            text = content if isinstance(content, str) else str(content or "")
        attach = images if (i == len(turns) - 1 or role == "user") and images else None
        # Images only on the first user turn when provided.
        if role != "user":
            attach = None
        if i == 0 and role == "user":
            attach = images
        elif images and role == "user" and i == 0:
            attach = images
        else:
            attach = images if (i == 0 and images) else None
        content = _content_with_images(text, attach if role == "user" else None)
        if attach and content is None and role == "user":
            return [], "classifier-image-unsupported"
        prepared.append({"role": role, "content": content})
    if prepared:
        prepared = [{"role": "system", "content": CLASSIFIER_PROMPT}, *prepared]
    return prepared, ""


async def _classify_messages(messages: list, *, images: list | None = None) -> str:
    key, url = _endpoint()
    if not key or not url:
        return "classifier-error"
    cache_key = _cache_key(messages, images)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(_post, messages),
            timeout=CLASSIFIER_DEADLINE,
        )
        result = _verdict_to_check(raw)
    except Exception:
        result = "classifier-error"
    # Only cache decisive verdicts. A missing key or timeout must not
    # poison a later attempt with the same text.
    if result and result != "classifier-error":
        _cache_put(cache_key, result)
    return result


async def classify_turns_async(turns: list, *, images: list | None = None) -> str:
    """Classify a short role-tagged conversation (user / assistant)."""
    if tests_are_allowing():
        return ""
    if not turns:
        return ""
    messages, check = _prepare_messages(turns, images=images)
    if check:
        return check
    return await _classify_messages(messages, images=images)


def classify_request(text: str, *, images: list | None = None) -> str:
    check = classify(text, images=images)
    return f"llm-input-{check}" if check else ""


async def classify_request_async(text: str, *, images: list | None = None) -> str:
    check = await classify_async(text, images=images)
    return f"llm-input-{check}" if check else ""


def classify_response(text: str, *, images: list | None = None) -> str:
    check = classify(text, images=images)
    return f"llm-output-{check}" if check else ""


async def classify_response_async(
    text: str,
    *,
    prompt: str = "",
    images: list | None = None,
) -> str:
    if prompt:
        check = await classify_turns_async(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": text or ""},
            ],
            images=images,
        )
    else:
        check = await classify_async(text, images=images)
    return f"llm-output-{check}" if check else ""


def extract_images(messages) -> list[dict]:
    """Image parts headed to a vision model."""
    out: list[dict] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            typ = str(part.get("type") or "")
            if typ in {"image_url", "image", "input_image"} or part.get("image_url") or part.get("images"):
                out.append(part)
    return out
