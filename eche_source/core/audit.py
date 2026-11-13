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
import time
from datetime import datetime, timezone
from typing import Any

AUDIT_RELATIVE = os.path.join("logs", "audit.jsonl")
AUDIT_FALLBACK_RELATIVE = os.path.join("logs", "audit.fallback.jsonl")
AUDIT_ALERT_RELATIVE = os.path.join("logs", "audit.alert")
AUDIT_MAX_BYTES = 5 * 1024 * 1024
# Rotated archives: cap count and total bytes of *non-safety* records.
# Safety lines (blocks, refusals) are always kept.
AUDIT_MAX_ARCHIVES = 8
AUDIT_MAX_ARCHIVE_BYTES = 20 * 1024 * 1024

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
    r"sk-or-[A-Za-z0-9\-._]+|xai-[A-Za-z0-9\-._]+)\b"
)
# Discord webhooks, including /api/v9/ and /api/v10/ REST URLs, bare or hook=...
_WEBHOOK_RE = re.compile(
    r"(?i)(?:https?://)?(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_-]+"
    r"|(?:\bhook\s*=\s*)(?:https?://\S+|\S+)"
)
# Discord bot token shape, with or without a "Bot " prefix.
_BOT_TOKEN_RE = re.compile(
    r"(?:\bBot\s+)?[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,}"
)
_refusal: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "eche_audit_refusal", default=False
)
_write_alerted = False

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


def mark_refusal() -> None:
    """A guardrail fired during this command; completion should log refusal."""
    _refusal.set(True)


def consume_refusal() -> bool:
    if _refusal.get(False):
        _refusal.set(False)
        return True
    return False


def _configured_secret_values() -> list[str]:
    values: list[str] = []
    try:
        from core.secrets import ENV_MAP, SECRET_KEYS, load_all

        cfg = load_all() or {}
        # Tokens, keys, and webhook-like secrets only. Never IDs, model
        # names, or other public settings (HOME_SERVER_ID, ECHE_OWNER_ID).
        secret_keys = set(SECRET_KEYS) | {
            "discord_token", "inf_api_key", "openrouter_api_key",
            "us_access_token", "us_secret_token", "xai_api_key",
        }
        for key in secret_keys:
            val = str(cfg.get(key) or "").strip()
            if len(val) >= 8:
                values.append(val)
        for settings_key, env_name in ENV_MAP.items():
            if settings_key not in secret_keys:
                continue
            val = str(os.environ.get(env_name) or "").strip()
            if len(val) >= 8:
                values.append(val)
    except Exception:
        pass
    # Optional GUI sentinel, only when present in this tree / environment.
    for name in ("ECHE_GUI_SENTINEL",):
        val = str(os.environ.get(name) or "").strip()
        if len(val) >= 8:
            values.append(val)
        try:
            from core import secrets as secrets_mod
            if hasattr(secrets_mod, "public_value"):
                sent = str(secrets_mod.public_value("eche_gui_sentinel") or "").strip()
                if len(sent) >= 8:
                    values.append(sent)
        except Exception:
            pass
    # Unique longest-first so a short value does not mask a longer one.
    uniq = []
    for val in sorted(set(values), key=len, reverse=True):
        uniq.append(val)
    return uniq


def redact(text: str) -> str:
    """Strip secrets, tokens, and nonces. Safe to run on any log payload."""
    raw = "" if text is None else str(text)
    raw = _SECRET_ASSIGN.sub(lambda m: m.group(0).split("=")[0].split(":")[0].rstrip() + "=[redacted]", raw)
    raw = _SECRET_TOKEN.sub("[redacted]", raw)
    raw = _WEBHOOK_RE.sub("[redacted]", raw)
    raw = _BOT_TOKEN_RE.sub("[redacted]", raw)
    for val in _configured_secret_values():
        if val and val in raw:
            raw = raw.replace(val, "[redacted]")
    return raw


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ensure_log_dir(path: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
        _apply_acl(folder)


def audit_path() -> str:
    from core.paths import ensure_user_layout

    root = ensure_user_layout()
    path = os.path.join(root, AUDIT_RELATIVE)
    _ensure_log_dir(path)
    return path



def _fallback_path() -> str:
    from core.paths import ensure_user_layout
    root = ensure_user_layout()
    path = os.path.join(root, AUDIT_FALLBACK_RELATIVE)
    _ensure_log_dir(path)
    return path


def _alert_path() -> str:
    from core.paths import ensure_user_layout
    root = ensure_user_layout()
    path = os.path.join(root, AUDIT_ALERT_RELATIVE)
    _ensure_log_dir(path)
    return path


def _apply_acl(path: str, *, alert: bool = True) -> None:
    try:
        from core.secrets import _restrict_acl_windows
        _restrict_acl_windows(path)
        return
    except Exception as exc:
        try:
            mode = 0o700 if os.path.isdir(path) else 0o600
            os.chmod(path, mode)
        except Exception:
            pass
        if alert:
            _alert_acl_failure(exc)


def _alert_acl_failure(exc: Exception) -> None:
    message = f"Audit ACL failed: {type(exc).__name__}: {exc}"
    try:
        path = _alert_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{_now()} {message}\n")
            fh.flush()
        _apply_acl(path, alert=False)
    except Exception:
        pass
    try:
        from core.gui_bridge import log
        log(message, channel="chat")
    except Exception:
        print(f"[audit] {message}", flush=True)


def _is_safety_line(line: str) -> bool:
    raw = (line or "").strip()
    if not raw:
        return False
    try:
        rec = json.loads(raw)
    except Exception:
        # Keep unreadable lines rather than drop a possible safety record.
        return True
    if not isinstance(rec, dict):
        return True
    if rec.get("safety") is True or rec.get("event") == "block":
        return True
    return str(rec.get("outcome") or "").lower() == "refusal"


def _archive_paths(folder: str) -> list[str]:
    names = []
    try:
        for name in os.listdir(folder):
            if name.startswith("audit.jsonl.") or name.startswith("audit.fallback.jsonl."):
                names.append(os.path.join(folder, name))
    except OSError:
        return []
    names.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    return names


def _non_safety_size(path: str) -> int:
    total = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not _is_safety_line(line):
                    total += len(line.encode("utf-8"))
    except OSError:
        return 0
    return total


def _rewrite_safety_only(path: str) -> None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            kept = [line for line in fh if _is_safety_line(line)]
    except OSError:
        return
    if not kept:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(kept)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _apply_acl(path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _prune_archives(folder: str) -> None:
    """Cap non-safety archive volume; never drop safety lines."""
    while True:
        paths = [p for p in _archive_paths(folder) if os.path.isfile(p)]
        bulky = [p for p in paths if _non_safety_size(p) > 0]
        if not bulky:
            return
        total = sum(_non_safety_size(p) for p in bulky)
        over_count = len(bulky) > AUDIT_MAX_ARCHIVES
        over_size = total > AUDIT_MAX_ARCHIVE_BYTES
        if not over_count and not over_size:
            return
        victim = bulky[0]
        _rewrite_safety_only(victim)
        after = _non_safety_size(victim) if os.path.isfile(victim) else 0
        if after > 0:
            # Could not shrink this file; stop to avoid a tight loop.
            return


def _rotate_if_needed(path: str) -> None:
    """Rename a full log aside. Never truncate, including block events."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < AUDIT_MAX_BYTES:
            return
    except OSError:
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = f"{path}.{stamp}"
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = f"{path}.{stamp}-{n}"
    os.rename(path, dest)
    _apply_acl(dest)
    _prune_archives(os.path.dirname(path) or ".")


def _write_once(path: str, line: str) -> None:
    _ensure_log_dir(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    _apply_acl(path)


def _append_line(path: str, line: str, *, safety: bool, rotate: bool = True, retries: int = 3) -> None:
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            with _lock:
                if rotate:
                    # A rename PermissionError must not block the append,
                    # especially for safety lines that have to reach the live file.
                    try:
                        _rotate_if_needed(path)
                    except Exception:
                        if not safety:
                            # Ordinary records still try to append to the live file.
                            pass
                _write_once(path, line)
            return
        except Exception as exc:
            last = exc
            time.sleep(0.05 * (attempt + 1))
    assert last is not None
    raise last


def _alert_write_failure(exc: Exception, *, safety: bool) -> None:
    global _write_alerted
    kind = "Safety audit" if safety else "Audit"
    message = f"{kind} write failed: {type(exc).__name__}: {exc}"
    try:
        path = _alert_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{_now()} {message}\n")
            fh.flush()
        _apply_acl(path, alert=False)
    except Exception:
        pass
    try:
        from core.gui_bridge import log
        log(message, channel="chat")
    except Exception:
        print(f"[audit] {message}", flush=True)
    _write_alerted = True


def iter_records(path: str | None = None):
    """Yield parsed audit records, skipping a torn last line."""
    target = path or audit_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # A torn last line is dropped; earlier complete lines stay.
            if i == len(lines) - 1:
                continue
            continue
        if isinstance(rec, dict):
            yield rec


def chat(*, kind: str = "mention", **extra) -> dict:
    """LLM mention or reply turn (not a slash/prefix command)."""
    return record("chat", kind=kind, **extra)


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
    """Append one audit record and emit a GUI log line.

    Ordinary command/tool/API writes are best-effort after a persistent
    alert exists. Safety/block writes retry, fall back to a sidecar file,
    raise a persistent GUI/owner alert, and then raise if the write still
    failed. Block events are never truncated or discarded silently.
    """
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
    is_safety = bool(safety or event == "block" or str(rec.get("outcome") or "").lower() == "refusal")
    if is_safety:
        rec["safety"] = True
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    write_error: Exception | None = None
    try:
        _append_line(audit_path(), line, safety=is_safety)
    except Exception as exc:
        write_error = exc
        if is_safety:
            try:
                _append_line(_fallback_path(), line, safety=True, rotate=True)
                write_error = None
            except Exception as fallback_exc:
                write_error = fallback_exc
        _alert_write_failure(write_error or exc, safety=is_safety)
        if is_safety and write_error is not None:
            raise

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
    if consume_refusal() and (outcome or "").lower() == "success":
        outcome = "refusal"
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


def error(source: str, error_type: str = "", *, ctx=None, message=None, **extra) -> dict:
    """Unhandled command, app-command, cog, or event error."""
    if message is not None:
        bind_from_message(message)
    ids = _merge_ids(_ids_from_ctx(ctx if ctx is not None else message), extra)
    return record(
        "error",
        source=source,
        error_type=error_type,
        outcome="error",
        safety=False,
        **ids,
        **extra,
    )


def tool(name: str, outcome: str, *, ctx=None, message=None, **extra) -> dict:
    if message is not None:
        bind_from_message(message)
    source = ctx if ctx is not None else message
    ids = _merge_ids(_ids_from_ctx(source), extra)
    return record("tool", name=name, outcome=outcome, safety=False, **ids, **extra)


def block(category: str, *, footer_hash: str = "", **extra) -> dict:
    mark_refusal()
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
