# core/ollama_thinking.py
# Ollama thinking support that is per model, not per provider.
# /api/show lists what a model can do ("completion", "tools", "thinking",
# "vision"). The think field is only sent to a model that lists "thinking",
# because some models and older servers answer HTTP 400 to it.

from __future__ import annotations

import threading
import time

import requests

# A capability list does not change while a tag stays pulled. A failed
# lookup is retried soon, so a server that was starting up is read again.
_OK_TTL = 3600.0
_FAIL_TTL = 60.0
SHOW_TIMEOUT = 5

_CACHE: dict[tuple[str, str], tuple[float, frozenset | None]] = {}
_LOCK = threading.Lock()

_ENDPOINT_SUFFIXES = ("/v1/chat/completions", "/api/chat", "/v1")


def base_url(url: str) -> str:
    """http://host:port for an Ollama chat URL."""
    text = (url or "").strip().rstrip("/")
    for suffix in _ENDPOINT_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _parse_caps(data) -> frozenset | None:
    """The capability set, or None when the server does not report one."""
    if not isinstance(data, dict):
        return None
    caps = data.get("capabilities")
    if not isinstance(caps, list):
        return None
    return frozenset(str(item).strip().lower() for item in caps if str(item).strip())


def _key(base: str, model: str) -> tuple[str, str]:
    return (base_url(base), (model or "").strip())


def capabilities(base: str, model: str) -> frozenset | None:
    """Cached /api/show capabilities for one model tag. None means unknown."""
    key = _key(base, model)
    if not key[1]:
        return None
    now = time.monotonic()
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is not None and now < entry[0]:
            return entry[1]
    caps: frozenset | None = None
    ttl = _FAIL_TTL
    try:
        response = requests.post(
            key[0] + "/api/show", json={"model": key[1]}, timeout=SHOW_TIMEOUT
        )
        if response.status_code == 200:
            caps = _parse_caps(response.json())
            # A server too old to list capabilities stays that way.
            ttl = _OK_TTL
    except Exception:
        caps = None
    with _LOCK:
        _CACHE[key] = (time.monotonic() + ttl, caps)
    return caps


def cached_capabilities(base: str, model: str) -> frozenset | None:
    """Capabilities already looked up, without a request."""
    with _LOCK:
        entry = _CACHE.get(_key(base, model))
    return entry[1] if entry is not None else None


def forget_thinking(base: str, model: str) -> None:
    """The server refused think for this model. Stop sending it."""
    key = _key(base, model)
    with _LOCK:
        entry = _CACHE.get(key)
        caps = set(entry[1] or ()) if entry is not None else {"completion"}
        caps.discard("thinking")
        _CACHE[key] = (time.monotonic() + _OK_TTL, frozenset(caps))


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def can_think(caps: frozenset | None) -> bool | None:
    """True or False from /api/show. None when the server did not say."""
    if caps is None:
        return None
    return "thinking" in caps


def think_field(want: bool, caps: frozenset | None):
    """Value for the think field. None leaves the field out of the request.

    A model that lists thinking gets true or false. A model without it never
    gets the field. Unknown capabilities send true only when thinking was
    asked for; a refusal is retried without the field.
    """
    able = can_think(caps)
    if able is False:
        return None
    if able is None:
        return True if want else None
    return bool(want)
