# core/audit.py
# Append-only audit log in the data folder.
#
# The 11:47:56 AM ET ?asset incident (Discord message 1570842665740927110)
# wrote no success log and the failure traces lacked a channel ID. Every
# command and tool invocation, every guardrail or classifier block, and
# every external API call (when a request or response id is available)
# now writes a full line with user, guild, and channel ids and a timestamp.
#
# Safety-relevant events are never truncated. Secrets, tokens, and nonces
# are never written. The GUI log gets a summary of the same ids; the
# audit file is the complete, append-only copy.

from __future__ import annotations

import contextvars
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

AUDIT_RELATIVE = os.path.join("logs", "audit.jsonl")

_lock = threading.Lock()
_bound: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "eche_audit_ids", default={}
)

# Field names and assignment forms that must never appear in a log with a value.
_SECRET_KEYS = (
    "authorization", "api_key", "apikey", "access_token", "secret", "password",
    "token", "nonce", "client_secret", "client_id", "bearer", "discord_token",
    "groq_api_key", "openrouter_api_key", "xai_api_key", "google_api_key",
    "us_access_token", "us_secret_token", "inf_api_key",
)
_SECRET_ASSIGN = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(k) for k in _SECRET_KEYS) + r")\b\s*[:=]\s*\S+"
)
_SECRET_TOKEN = re.compile(
    r"(?i)\b(?:bearer\s+[A-Za-z0-9\-._~+/]+=*|sk-[A-Za-z0-9\-._]+|gsk_[A-Za-z0-9]+|"
    r"sk-or-[A-Za-z0-9\-._]+)\b"
)
_HEADER_ID_KEYS = (
    "x-request-id",
    "x-groq-id",
    "x-openai-request-id",
    "openrouter-request-id",
    "x-ratelimit-remaining",  # not an id; skip in extractor
)


def bind(*, user_id=None, guild_id=None, channel_id=None) -> None:
    """Remember the current turn's ids for later block and API lines."""
    current = dict(_bound.get({}) or {})
    if user_id is not None:
        current["user_id"] = _id(user_id)
    if guild_id is not None:
        current["guild_id"] = _id(guild_id)
    if channel_id is not None:
        current["channel_id"] = _id(channel_id)
    _bound.set(current)


def bind_from_message(message) -> None:
    if message is None:
        return
    author = getattr(message, "author", None)
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    bind(
        user_id=getattr(author, "id", None),
        guild_id=getattr(guild, "id", None),
        channel_id=getattr(channel, "id", None),
    )


def bound_ids() -> dict[str, str]:
    return dict(_bound.get({}) or {})


def _id(value) -> str:
    if value is None:
        return ""
    return str(value)


def redact(text: str) -> str:
    """Strip secrets, tokens, and nonces. Safe to run on any log payload."""
    raw = "" if text is None else str(text)
    raw = _SECRET_ASSIGN.sub(lambda m: m.group(0).split("=")[0].split(":")[0].rstrip() + "=[redacted]", raw)
    raw = _SECRET_TOKEN.sub("[redacted]", raw)
    return raw


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def audit_path() -> str:
    from core.paths import ensure_user_layout

    root = ensure_user_layout()
    path = os.path.join(root, AUDIT_RELATIVE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _ids_from_ctx(ctx) -> dict[str, str]:
    if ctx is None:
        return bound_ids()
    author = getattr(ctx, "author", None) or getattr(getattr(ctx, "message", None), "author", None)
    guild = getattr(ctx, "guild", None) or getattr(getattr(ctx, "message", None), "guild", None)
    channel = getattr(ctx, "channel", None) or getattr(getattr(ctx, "message", None), "channel", None)
    ids = bound_ids()
    if getattr(author, "id", None) is not None:
        ids["user_id"] = _id(author.id)
    if getattr(guild, "id", None) is not None:
        ids["guild_id"] = _id(guild.id)
    if getattr(channel, "id", None) is not None:
        ids["channel_id"] = _id(channel.id)
    return ids


def _qualified_command(ctx) -> str:
    command = getattr(ctx, "command", None) if ctx is not None else None
    name = getattr(command, "qualified_name", None) or getattr(command, "name", None)
    if name:
        return str(name)
    invoked = getattr(ctx, "invoked_with", None) if ctx is not None else None
    return str(invoked or "")


def record(event: str, *, safety: bool = False, **fields) -> dict:
    """Append one audit record and emit a GUI log line. Never raises."""
    ids = bound_ids()
    rec: dict[str, Any] = {
        "ts": _now(),
        "event": event,
        "user_id": _id(fields.pop("user_id", ids.get("user_id", ""))),
        "guild_id": _id(fields.pop("guild_id", ids.get("guild_id", ""))),
        "channel_id": _id(fields.pop("channel_id", ids.get("channel_id", ""))),
    }
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if isinstance(value, (str, int, float, bool)):
            rec[key] = redact(str(value)) if isinstance(value, str) else value
        else:
            try:
                rec[key] = json.loads(redact(json.dumps(value, ensure_ascii=False, default=str)))
            except Exception:
                rec[key] = redact(str(value))
    rec = {k: (redact(v) if isinstance(v, str) else v) for k, v in rec.items()}
    # Never persist secret-looking keys.
    for key in list(rec):
        if key.lower() in _SECRET_KEYS or "nonce" in key.lower():
            rec.pop(key, None)
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    try:
        path = audit_path()
        with _lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
    except Exception:
        pass
    try:
        from core.gui_bridge import log

        if event == "block":
            bits = [f"Blocked ({rec.get('category') or 'unknown'})"]
        else:
            bits = [f"Audit {event}"]
        if rec.get("name") and event != "block":
            bits.append(str(rec["name"]))
        if rec.get("outcome") and event != "block":
            bits.append(f"outcome={rec['outcome']}")
        if rec.get("category") and event != "block":
            bits.append(f"category={rec['category']}")
        if rec.get("footer_hash"):
            bits.append(f"footer sha256={rec['footer_hash']}")
        if rec.get("request_id") or rec.get("response_id"):
            bits.append(f"request_id={rec.get('request_id') or '-'}")
            bits.append(f"response_id={rec.get('response_id') or '-'}")
        bits.append(f"user_id={rec.get('user_id') or '-'}")
        bits.append(f"guild_id={rec.get('guild_id') or '-'}")
        bits.append(f"channel_id={rec.get('channel_id') or '-'}")
        bits.append(f"ts={rec['ts']}")
        log(" ".join(bits) + ".", channel="chat" if (safety or event in {"block", "command", "tool"}) else "bot")
    except Exception:
        pass
    return rec


def _merge_ids(ids: dict, extra: dict) -> dict:
    merged = dict(ids)
    for key in ("user_id", "guild_id", "channel_id"):
        if key in extra and extra[key] is not None:
            merged[key] = _id(extra.pop(key))
    return merged


def command(name: str, outcome: str, *, ctx=None, **extra) -> dict:
    ids = _merge_ids(_ids_from_ctx(ctx), extra)
    return record(
        "command",
        name=name,
        outcome=outcome,
        safety=False,
        **ids,
        **extra,
    )


def command_from_ctx(ctx, outcome: str, **extra) -> dict:
    return command(_qualified_command(ctx), outcome, ctx=ctx, **extra)


def tool(name: str, outcome: str, *, ctx=None, message=None, **extra) -> dict:
    if message is not None:
        bind_from_message(message)
    source = ctx if ctx is not None else message
    ids = _merge_ids(_ids_from_ctx(source), extra)
    return record("tool", name=name, outcome=outcome, safety=False, **ids, **extra)


def block(category: str, *, footer_hash: str = "", **extra) -> dict:
    digest = footer_hash
    if not digest:
        try:
            from core.safety import footer_sha256

            digest = footer_sha256()
        except Exception:
            digest = ""
    ids = bound_ids()
    extra_ids = {k: extra.pop(k) for k in ("user_id", "guild_id", "channel_id") if k in extra}
    ids.update({k: _id(v) for k, v in extra_ids.items()})
    return record(
        "block",
        category=category,
        footer_hash=digest,
        safety=True,
        outcome="refusal",
        **ids,
        **extra,
    )


def api(service: str, *, request_id: str = "", response_id: str = "", outcome: str = "", **extra) -> dict:
    ids = bound_ids()
    return record(
        "api",
        service=service,
        request_id=request_id or "",
        response_id=response_id or "",
        outcome=outcome or "",
        **ids,
        **extra,
    )


def ids_from_http(response=None, payload=None) -> tuple[str, str]:
    """Best-effort request and response ids from a provider HTTP reply."""
    request_id = ""
    response_id = ""
    headers = None
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers is None and isinstance(response, dict):
            headers = response.get("headers")
    if headers:
        try:
            items = {str(k).lower(): str(v) for k, v in headers.items()}
        except Exception:
            items = {}
        request_id = (
            items.get("x-request-id")
            or items.get("x-groq-id")
            or items.get("x-openai-request-id")
            or items.get("openrouter-request-id")
            or ""
        )
    body = payload
    if body is None and response is not None:
        body = getattr(response, "id", None)
        if body is None and isinstance(response, dict):
            body = response
        elif not isinstance(body, dict):
            body = {"id": body} if body else None
    if isinstance(body, dict):
        response_id = str(body.get("id") or body.get("request_id") or body.get("response_id") or "")
        if not request_id:
            request_id = str(body.get("request_id") or "")
    elif body is not None:
        response_id = str(body)
    return request_id, response_id


def api_from_http(service: str, response=None, payload=None, *, outcome: str = "") -> dict:
    request_id, response_id = ids_from_http(response, payload)
    return api(service, request_id=request_id, response_id=response_id, outcome=outcome)
