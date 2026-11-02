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


# ---------------------------------------------------------------------------
# Per-model reasoning mode
# ---------------------------------------------------------------------------
# auto: think when /api/show lists thinking. on: always ask (an unknown model
# is asked too, and a refusal is retried without the field). off: never.
# low / medium / high: on, with that level (gpt-oss style models).
MODES = ("auto", "on", "off", "low", "medium", "high")
THINK_LEVELS = ("low", "medium", "high")
_MODE_ALIASES = {
    "1": "on", "true": "on", "yes": "on",
    "0": "off", "false": "off", "no": "off", "none": "off",
    "": "auto", "default": "auto",
}


def normalize_mode(raw) -> str | None:
    low = str(raw if raw is not None else "").strip().lower()
    low = _MODE_ALIASES.get(low, low)
    return low if low in MODES else None


def _tag_key(model: str) -> str:
    """Tags compare without case and without a trailing :latest."""
    low = (model or "").strip().lower()
    return low[: -len(":latest")] if low.endswith(":latest") else low


def parse_model_modes(raw) -> dict[str, str]:
    """reasoning_models setting: "tag=mode; tag2=mode" or a JSON object."""
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        items = list(raw.items())
    else:
        text = str(raw or "").strip()
        items = []
        if text.startswith("{"):
            try:
                import json
                loaded = json.loads(text)
                if isinstance(loaded, dict):
                    items = list(loaded.items())
            except ValueError:
                items = []
        else:
            import re
            for chunk in re.split(r"[;\n]+", text):
                if "=" not in chunk:
                    continue
                tag, _, mode = chunk.rpartition("=")
                items.append((tag, mode))
    for tag, mode in items:
        name = _tag_key(str(tag))
        value = normalize_mode(mode)
        if name and value:
            out[name] = value
    return out


def format_model_modes(modes: dict[str, str]) -> str:
    """Settings text for a mode map. Blank when nothing is set."""
    clean = parse_model_modes(modes)
    return "; ".join(f"{tag}={clean[tag]}" for tag in sorted(clean))


def saved_model_modes() -> dict[str, str]:
    return parse_model_modes(_saved("reasoning_models", "ECHE_REASONING_MODELS"))


def model_mode(model: str, provider_on: bool, modes: dict[str, str] | None = None) -> str:
    """Mode for one tag. A saved per-model value wins over the Reasoning switch.

    With no per-model value, the switch on means auto and off means off.
    """
    table = saved_model_modes() if modes is None else parse_model_modes(modes)
    picked = table.get(_tag_key(model))
    if picked:
        return picked
    return "auto" if provider_on else "off"


def resolve_think(mode: str, caps: frozenset | None):
    """Value for the think field. None leaves the field out of the request.

    A model that lists thinking gets true, false, or a level. A model
    without it never gets the field. Unknown capabilities send the field
    only for an explicit on or level.
    """
    mode = normalize_mode(mode) or "auto"
    able = can_think(caps)
    if mode == "off":
        return False if able else None
    if mode == "auto":
        return True if able else None
    if able is False:
        return None
    return mode if mode in THINK_LEVELS else True


def think_field(want: bool, caps: frozenset | None):
    """Bool form of resolve_think: want=True is "on", False is "off"."""
    return resolve_think("on" if want else "off", caps)


# ---------------------------------------------------------------------------
# Settings: context window and thinking budget
# ---------------------------------------------------------------------------
DEFAULT_NUM_CTX = 8192
NUM_CTX_MIN = 512
NUM_CTX_MAX = 262144
# Thinking shares num_predict with the reply. This much is added for the trace.
DEFAULT_THINK_BUDGET = 1024
THINK_BUDGET_MIN = 0
THINK_BUDGET_MAX = 8192


def _saved(key: str, env_name: str) -> str:
    """Settings value, else the env copy. Blank when neither is set."""
    raw = ""
    try:
        from core.secrets import public_value
        raw = public_value(key)
    except Exception:
        raw = ""
    if not raw:
        import os
        raw = (os.environ.get(env_name) or "").strip()
    return raw


def clamp_int(raw, default: int, low: int, high: int) -> int:
    """A whole number inside low..high. Blank or junk stays at default."""
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return int(default)
    return max(low, min(high, value))


def num_ctx() -> int:
    """Ollama context window. Settings → Provider & Model, else 8192."""
    return clamp_int(
        _saved("ollama_num_ctx", "OLLAMA_NUM_CTX"),
        DEFAULT_NUM_CTX, NUM_CTX_MIN, NUM_CTX_MAX,
    )


def think_budget() -> int:
    """Extra output tokens for a thinking trace. Settings, else 1024."""
    return clamp_int(
        _saved("reasoning_budget", "ECHE_REASONING_BUDGET"),
        DEFAULT_THINK_BUDGET, THINK_BUDGET_MIN, THINK_BUDGET_MAX,
    )


# ---------------------------------------------------------------------------
# Sampling for thinking turns
# ---------------------------------------------------------------------------
# Qwen's published settings for thinking mode. Greedy or hot sampling makes
# Qwen3-family models loop inside the trace.
QWEN_THINKING_SAMPLING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20}
_QWEN_MARKERS = ("qwen", "qwq")
_SAMPLING_KEYS = (
    # settings key, env, option name, parse, low, high
    ("reasoning_temperature", "ECHE_REASONING_TEMPERATURE", "temperature", float, 0.0, 2.0),
    ("reasoning_top_p", "ECHE_REASONING_TOP_P", "top_p", float, 0.0, 1.0),
    ("reasoning_top_k", "ECHE_REASONING_TOP_K", "top_k", int, 0, 1000),
)


def is_qwen_family(model: str) -> bool:
    """Qwen, QwQ, and their fine-tunes (abliterated builds keep the name)."""
    low = (model or "").strip().lower()
    return any(marker in low for marker in _QWEN_MARKERS)


def sampling_overrides() -> dict:
    """Saved thinking-sampling values. A blank or junk field is not an override."""
    out: dict = {}
    for key, env_name, option, parse, low, high in _SAMPLING_KEYS:
        raw = _saved(key, env_name)
        if not raw:
            continue
        try:
            value = parse(float(raw)) if parse is int else parse(raw)
        except (TypeError, ValueError):
            continue
        out[option] = max(low, min(high, value))
    return out


def thinking_sampling(model: str, temperature) -> dict:
    """Sampling options for a request that has thinking on.

    Qwen-family models get 0.6 / 0.95 / 20. Other models keep the caller's
    temperature. Saved overrides win over both.
    """
    opts: dict = {"temperature": temperature}
    if is_qwen_family(model):
        opts.update(QWEN_THINKING_SAMPLING)
    opts.update(sampling_overrides())
    return opts
