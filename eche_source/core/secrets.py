# core/secrets.py
# Standard local secret handling for Eche.
#
# - Sensitive values are encrypted at rest with Windows DPAPI (per-user).
# - Non-secrets live in config/settings.json (plain JSON is fine).
# - .env remains a dev convenience; frozen builds prefer DPAPI store.
# - Plaintext tokens found in old settings.json are migrated + scrubbed.

from __future__ import annotations

import base64
import json
import os
import stat
import sys
import threading
from typing import Any

# Keys that must never sit in plaintext settings.json
CURRENT_XAI_KEY_EPOCH = "2026-11-hardened-asset"

SECRET_KEYS = (
    "discord_token",
    "inf_api_key",       # GROQ_API_KEY
    "openrouter_api_key",  # OPENROUTER_API_KEY
    "us_access_token",   # US_ACCESS_TOKEN (Unsplash)
    "us_secret_token",   # US_SECRET_TOKEN (Unsplash)
    "xai_api_key",       # stored encrypted; read via load_all only, not ENV_MAP
)

PUBLIC_KEYS = (
    "home_server_id",
    "groq_model",          # active model → GROQ_MODEL env (for client.py)
    "cloud_model",         # Cloud (Groq) model id
    "ollama_model",        # Local Ollama model id
    "openrouter_model",    # OpenRouter model id (author/slug)
    "provider_backend",    # cloud | openrouter | ollama
    "summarizer_model",
    "summarizer_prompt_path",
    "project_path",
    "update_branch",       # GitHub branch for Update Eche; blank uses the installed branch, then main
    "suppress_no_provider_warn",
    "admin_tools",          # "1" while the Admin tools toggle is on
    "default_tools",        # "0" hides thread, lookup, and context. Blank stays on.
    "owner_id",             # Discord user ids for owner commands and admin tools
    "channel_completion_tokens",  # channel reply budget; blank uses 64
    "thread_completion_tokens",   # one thread post; blank uses 316
    "tool_duckduckgo",            # "0" leaves lookup out of the prompt. Blank stays on.
    "tool_thread",                # "0" leaves thread out of the prompt. Blank stays on.
    "tool_context_raw",           # "0" leaves context out of the prompt. Blank stays on.
    "prompt_episodic",            # "0" leaves the server episode out of the prompt. Blank stays on.
    "reasoning_cloud",            # "1" lets Groq think. Blank stays off.
    "reasoning_openrouter",       # "1" lets OpenRouter think. Blank stays off.
    "reasoning_ollama",           # "1" lets Ollama think. Blank stays off.
    "reasoning_budget",           # extra output tokens for a thinking trace; blank uses 1024
    "ollama_num_ctx",             # Ollama context window; blank uses 8192
    "reasoning_temperature",      # thinking turns; blank uses 0.6 for Qwen, else the caller's
    "reasoning_top_p",            # thinking turns; blank uses 0.95 for Qwen
    "reasoning_top_k",            # thinking turns; blank uses 20 for Qwen
    "reasoning_models",           # per Ollama tag: "tag=auto|on|off|low|medium|high; ..."
    # Context & Memory caps (core/context_caps.py). Blank uses the default.
    "memory_prompt_chars",          # whole memory prompt; blank uses 3500
    "memory_summary_prompt_chars",  # each long-term summary in the prompt; blank uses 600
    "episode_prompt_chars",         # server episode in the prompt; blank uses 600
    "memory_recent_messages",       # full recent messages kept per side; blank uses 3
    "memory_message_chars",         # one message in the recent buffer; blank/0 = whole message
    "media_prompt_chars",           # attachment/embed text per turn; blank uses 1600
    "attachment_text_chars",        # one attached text file; blank uses 1000
    "media_memory_chars",           # attachment text stored in memory; blank uses 500
    "memory_fold_every",            # messages per long-term summary update; blank uses 3
    "memory_summary_chars",         # long-term summary pin target; blank uses 1900
    "episode_fold_after",           # server lines per episode summary; blank uses 5
    "episode_summary_chars",        # server episode pin target; blank uses 1900
    "thread_notes_chars",           # lookup notes carried into thread posts; blank uses 2000
    "thread_posted_chars",          # earlier posts each thread post sees; blank/0 = all
    "thread_topic_scan",            # messages searched for a thread subject; blank uses 8
    "local_history_turns",          # desktop chat turns sent; blank uses 20
    "memory_mask_slurs",            # "0" stops masking slurs in memory pins. Blank stays on.
    "asset_enabled",                # "1" turns ?asset on. Blank or missing stays OFF.
    "asset_owner_only",             # blank or missing stays ON (owner only). "0" opens it to the guild.
    "asset_adult_enabled",          # "1" turns 18+ mode on. Blank or missing stays OFF.
    "asset_approval_enabled",       # "0" turns owner approval off. Blank or missing stays ON.
    "xai_key_epoch",                # must match CURRENT_XAI_KEY_EPOCH or the stored xAI key is ignored
    # The image safety checkers have no settings; their models are
    # hardcoded in core/classifier.py. See RETIRED_PUBLIC_KEYS below.
)

# Keys older versions saved. Ignored on read (public_value returns '') and
# dropped from settings.json on the next save. Nothing else reads them.
RETIRED_PUBLIC_KEYS = (
    "content_filter_provider",
    "content_filter_model",
    "child_check_provider",
    "child_check_model",
    "image_check_provider",
    "image_check_model",
    "age_vision_ollama_model",
    "blocked_words",
    # Old "In the prompt > Asset" box. A leftover "0" from a pre-removal
    # install hid the chat asset tool while the pictures line said it existed.
    # The Asset switch (asset_enabled) alone decides now.
    "tool_asset",
)

# Map settings key -> environment variable used by the bot
ENV_MAP = {
    "discord_token": "DISCORD_TOKEN",
    "inf_api_key": "GROQ_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "us_access_token": "US_ACCESS_TOKEN",
    "us_secret_token": "US_SECRET_TOKEN",
    "home_server_id": "HOME_SERVER_ID",
    "groq_model": "GROQ_MODEL",
    "provider_backend": "ECHE_PROVIDER",
    "summarizer_model": "SUMMARIZER_MODEL",
    "summarizer_prompt_path": "SUMMARIZER_PROMPT_PATH",
    "admin_tools": "ECHE_ADMIN_TOOLS",
    "owner_id": "ECHE_OWNER_ID",
    "channel_completion_tokens": "ECHE_CHANNEL_TOKENS",
    "thread_completion_tokens": "ECHE_THREAD_TOKENS",
}

_SECRETS_FILENAME = "secrets.dpapi.json"
_SETTINGS_FILENAME = "settings.json"


class SettingsUnreadable(Exception):
    """settings.json exists but cannot be read or parsed."""


def _user_root() -> str:
    try:
        from core.paths import ensure_user_layout
        return ensure_user_layout()
    except Exception:
        return os.getcwd()


def public_value(key: str, root: str | None = None) -> str:
    """One saved settings value. Blank when that key has never been set."""
    if key not in PUBLIC_KEYS:
        return ""
    root = root or _user_root()
    try:
        data = _read_json(settings_path(root))
    except Exception:
        return ""
    return str(data.get(key) or "").strip()


def settings_path(root: str | None = None) -> str:
    root = root or _user_root()
    return os.path.join(root, "config", _SETTINGS_FILENAME)


def secrets_path(root: str | None = None) -> str:
    root = root or _user_root()
    return os.path.join(root, "config", _SECRETS_FILENAME)


def _dpapi_protect(data: bytes, description: str = "Eche secret") -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_buf = ctypes.create_string_buffer(data)
    in_blob = DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()

    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        description,
        None,
        None,
        None,
        0x1,
        ctypes.byref(out_blob),
    ):
        raise OSError(f"CryptProtectData failed (err={kernel32.GetLastError()})")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_buf = ctypes.create_string_buffer(blob)
    in_blob = DATA_BLOB(len(blob), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()

    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(out_blob),
    ):
        raise OSError(f"CryptUnprotectData failed (err={kernel32.GetLastError()})")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def protect(plaintext: str) -> str:
    if not plaintext:
        return ""
    if sys.platform.startswith("win"):
        try:
            blob = _dpapi_protect(plaintext.encode("utf-8"))
            return base64.b64encode(blob).decode("ascii")
        except Exception as e:
            raise RuntimeError(f"DPAPI protect failed: {e}") from e
    raise RuntimeError(
        "Secure secret storage requires Windows DPAPI. "
        "Set secrets via environment variables on this platform."
    )


def unprotect(token_b64: str) -> str:
    if not token_b64:
        return ""
    if sys.platform.startswith("win"):
        try:
            raw = base64.b64decode(token_b64.encode("ascii"))
            return _dpapi_unprotect(raw).decode("utf-8")
        except Exception as e:
            raise RuntimeError(f"DPAPI unprotect failed: {e}") from e
    raise RuntimeError("Secure secret storage requires Windows DPAPI.")


def redact(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "•" * 8
    return f"{value[:keep]}…{'•' * 6}…{value[-keep:]}"


def _restrict_acl_windows(path: str) -> None:
    """Owner+SYSTEM ACL. A nonzero icacls exit is a failure: chmod, then raise.

    On non-Windows hosts icacls is unavailable; chmod is the control and is
    not treated as an ACL failure.
    """
    if not sys.platform.startswith("win"):
        try:
            mode = 0o700 if os.path.isdir(path) else (stat.S_IRUSR | stat.S_IWUSR)
            os.chmod(path, mode)
        except Exception:
            pass
        return
    import getpass
    import subprocess

    err: Exception | None = None
    try:
        user = getpass.getuser()
        flags = 0
        if sys.platform.startswith("win"):
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        is_dir = os.path.isdir(path)
        grant = "(OI)(CI)(F)" if is_dir else "(R,W)"
        result = subprocess.run(
            [
                "icacls",
                path,
                "/inheritance:r",
                "/grant:r",
                f"{user}:{grant}",
                "/grant:r",
                f"SYSTEM:{grant}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=flags,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            err = RuntimeError(f"icacls exited {result.returncode}: {detail}")
    except Exception as exc:
        err = exc
    if err is None:
        return
    try:
        mode = 0o700 if os.path.isdir(path) else (stat.S_IRUSR | stat.S_IWUSR)
        os.chmod(path, mode)
    except Exception:
        pass
    raise err


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _load_encrypted_secrets(root: str) -> dict[str, str]:
    raw = _read_json(secrets_path(root))
    enc = raw.get("secrets") if isinstance(raw.get("secrets"), dict) else {}
    out: dict[str, str] = {}
    for key in SECRET_KEYS:
        blob = enc.get(key) or ""
        if not blob:
            continue
        try:
            out[key] = unprotect(blob)
        except Exception:
            out[key] = ""
    return out


def _save_encrypted_secrets(root: str, secrets: dict[str, str]) -> None:
    payload = {
        "version": 1,
        "note": "DPAPI-encrypted secrets for Eche. Not portable across Windows users/machines.",
        "secrets": {},
    }
    for key in SECRET_KEYS:
        val = (secrets.get(key) or "").strip()
        if val:
            payload["secrets"][key] = protect(val)
    path = secrets_path(root)
    _write_json(path, payload)
    if sys.platform.startswith("win"):
        try:
            _restrict_acl_windows(path)
        except Exception:
            # chmod fallback already ran inside the helper
            pass


def _load_public(root: str) -> dict[str, str]:
    data = _read_json(settings_path(root))
    out: dict[str, str] = {}
    for key in PUBLIC_KEYS:
        out[key] = str(data.get(key) or "").strip()
    return out


def _save_public(root: str, public: dict[str, str]) -> None:
    data = _read_json(settings_path(root))
    for key in SECRET_KEYS:
        data.pop(key, None)
    data.pop("thoughts_thread_id", None)
    for key in RETIRED_PUBLIC_KEYS:
        data.pop(key, None)
    for key in PUBLIC_KEYS:
        data[key] = str(public.get(key) or "").strip()
    data["secrets_backend"] = "dpapi"
    _write_json(settings_path(root), data)


def _seed_from_dotenv(root: str) -> dict[str, str]:
    seeded: dict[str, str] = {k: "" for k in (*SECRET_KEYS, *PUBLIC_KEYS)}
    env_path = os.path.join(root, ".env")
    if not os.path.isfile(env_path):
        return seeded
    try:
        from dotenv import dotenv_values
        env = dotenv_values(env_path)
    except Exception:
        return seeded

    for settings_key, env_name in ENV_MAP.items():
        seeded[settings_key] = (env.get(env_name) or "").strip()
    return seeded


def _migrate_plaintext_settings(root: str) -> None:
    path = settings_path(root)
    data = _read_json(path)
    if not data:
        return

    found = False
    secrets = _load_encrypted_secrets(root)
    for key in SECRET_KEYS:
        plain = str(data.get(key) or "").strip()
        if plain:
            # Pre-build / legacy xAI keys in settings.json must not come back.
            if key != "xai_api_key" and not secrets.get(key):
                secrets[key] = plain
            found = True
            data.pop(key, None)

    if found:
        _save_encrypted_secrets(root, secrets)
        data["secrets_backend"] = "dpapi"
        data["migrated_plaintext"] = True
        _write_json(path, data)


_load_cache_lock = threading.Lock()
_load_cache: dict[str, tuple[tuple, dict[str, str]]] = {}


def _settings_fingerprint(root: str) -> tuple:
    parts = []
    for path in (settings_path(root), secrets_path(root)):
        try:
            st = os.stat(path)
            parts.append((path, st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append((path, None, None))
    return tuple(parts)


def clear_load_cache() -> None:
    """Tests and save_all. Drops the in-memory settings snapshot."""
    with _load_cache_lock:
        _load_cache.clear()


def load_all(root: str | None = None) -> dict[str, str]:
    root = root or _user_root()
    finger = _settings_fingerprint(root)
    with _load_cache_lock:
        hit = _load_cache.get(root)
        if hit and hit[0] == finger:
            return dict(hit[1])
    os.makedirs(os.path.join(root, "config"), exist_ok=True)
    _migrate_plaintext_settings(root)

    public = _load_public(root)
    secrets = _load_encrypted_secrets(root)

    seeded = _seed_from_dotenv(root)
    for key in SECRET_KEYS:
        if not secrets.get(key) and seeded.get(key):
            secrets[key] = seeded[key]
    for key in PUBLIC_KEYS:
        if not public.get(key) and seeded.get(key):
            public[key] = seeded[key]

    out = {**public, **secrets}
    for key in (*SECRET_KEYS, *PUBLIC_KEYS):
        out.setdefault(key, "")
    if (out.get("xai_api_key") or "").strip():
        if str(out.get("xai_key_epoch") or "").strip() != CURRENT_XAI_KEY_EPOCH:
            out["xai_api_key"] = ""

    stored = (out.get("project_path") or "").strip()
    try:
        from core.paths import resolve_source_root, is_source_tree
        resolved = resolve_source_root(stored or None)
        if not stored or not is_source_tree(stored):
            out["project_path"] = resolved
        else:
            out["project_path"] = os.path.abspath(stored)
    except Exception:
        if not stored:
            try:
                from core.paths import package_root
                out["project_path"] = package_root()
            except Exception:
                out["project_path"] = root or os.getcwd()

    backend = (out.get("provider_backend") or "").strip().lower()
    if backend not in ("cloud", "ollama", "openrouter"):
        out["provider_backend"] = "cloud"
        backend = "cloud"

    legacy = (out.get("groq_model") or "").strip()
    cloud_m = (out.get("cloud_model") or "").strip()
    ollama_m = (out.get("ollama_model") or "").strip()
    openrouter_m = (out.get("openrouter_model") or "").strip()

    _CLOUD_DEFAULT = "qwen/qwen3.8-27b"
    _OLLAMA_DEFAULT = "llama3"
    _OPENROUTER_DEFAULT = "openrouter/free"
    _OLLAMA_LOOKALIKES = {"llama3", "llama3.2", "llama3.1", "mistral", "mixtral", "phi3", "qwen2.5"}

    if not cloud_m:
        if legacy and legacy not in _OLLAMA_LOOKALIKES:
            cloud_m = legacy
        else:
            cloud_m = _CLOUD_DEFAULT
        out["cloud_model"] = cloud_m

    if not ollama_m:
        if legacy and (legacy in _OLLAMA_LOOKALIKES or backend == "ollama"):
            ollama_m = legacy if legacy else _OLLAMA_DEFAULT
        else:
            ollama_m = _OLLAMA_DEFAULT
        out["ollama_model"] = ollama_m

    if not openrouter_m:
        openrouter_m = _OPENROUTER_DEFAULT
        out["openrouter_model"] = openrouter_m

    if backend == "ollama":
        out["groq_model"] = ollama_m
    elif backend == "openrouter":
        out["groq_model"] = openrouter_m
    else:
        out["groq_model"] = cloud_m
    if not (out.get("groq_model") or "").strip():
        if backend == "ollama":
            out["groq_model"] = _OLLAMA_DEFAULT
        elif backend == "openrouter":
            out["groq_model"] = _OPENROUTER_DEFAULT
        else:
            out["groq_model"] = _CLOUD_DEFAULT

    try:
        from core.media_scan import reject_image_model
    except Exception:
        reject_image_model = lambda value, default="": value or default
    out["cloud_model"] = reject_image_model(out.get("cloud_model"), _CLOUD_DEFAULT)
    out["ollama_model"] = reject_image_model(out.get("ollama_model"), _OLLAMA_DEFAULT)
    out["openrouter_model"] = reject_image_model(out.get("openrouter_model"), _OPENROUTER_DEFAULT)
    if backend == "ollama":
        out["groq_model"] = out["ollama_model"]
    elif backend == "openrouter":
        out["groq_model"] = out["openrouter_model"]
    else:
        out["groq_model"] = out["cloud_model"]
    if not (out.get("groq_model") or "").strip():
        out["groq_model"] = (
            _OLLAMA_DEFAULT if backend == "ollama"
            else _OPENROUTER_DEFAULT if backend == "openrouter"
            else _CLOUD_DEFAULT
        )

    with _load_cache_lock:
        _load_cache[root] = (finger, dict(out))
    return out



def _asset_flag(store: dict | None, key: str, *, default: bool) -> bool:
    from core.asset_safety import parse_safety_flag

    value, _invalid = parse_safety_flag((store or {}).get(key), default=default)
    return value


def _asset_flag_raw(store: dict | None, key: str, *, default: bool) -> tuple[bool, bool, str]:
    from core.asset_safety import parse_safety_flag

    raw = str((store or {}).get(key) or "")
    value, invalid = parse_safety_flag(raw, default=default)
    return value, invalid, raw


def couple_asset_toggles(public: dict, *, invalid: list | None = None) -> dict:
    """Approval-off without 18+ forces owner-only. 18+ and approval are independent.

    18+ itself is not owner-only: the audience follows asset_owner_only, and
    18+ may run with owner approval off.
    """
    data = dict(public or {})
    adult, ad_bad, ad_raw = _asset_flag_raw(data, "asset_adult_enabled", default=False)
    approval, ap_bad, ap_raw = _asset_flag_raw(data, "asset_approval_enabled", default=True)
    owner, ow_bad, ow_raw = _asset_flag_raw(data, "asset_owner_only", default=True)
    if not approval and not adult:
        owner = True
    data["asset_adult_enabled"] = "1" if adult else "0"
    data["asset_approval_enabled"] = "1" if approval else "0"
    data["asset_owner_only"] = "1" if owner else "0"
    if invalid is not None:
        if ad_bad:
            invalid.append(("asset_adult_enabled", ad_raw))
        if ap_bad:
            invalid.append(("asset_approval_enabled", ap_raw))
        if ow_bad:
            invalid.append(("asset_owner_only", ow_raw))
    return data


def _notify_asset_toggle_owners(lines: list[str]) -> None:
    """Best-effort DM to each owner when an 18+ or approval toggle changes."""
    if not lines:
        return
    try:
        from core.gui_bridge import get_bot, get_loop
        import asyncio

        bot = get_bot()
        loop = get_loop()
        if bot is None or loop is None:
            return
        body = "\n".join(lines)

        async def _send() -> None:
            try:
                from core.asset_review import _reviewer_ids

                reviewers = await _reviewer_ids(bot)
            except Exception:
                return
            fetch = getattr(bot, "fetch_user", None)
            for owner_id in reviewers or []:
                try:
                    owner = await fetch(owner_id) if fetch else None
                    send = getattr(owner, "send", None)
                    if send is not None:
                        await send(body)
                except Exception:
                    continue

        asyncio.run_coroutine_threadsafe(_send(), loop)
    except Exception:
        return


def save_all(data: dict[str, Any], root: str | None = None) -> None:
    root = root or _user_root()
    clear_load_cache()
    os.makedirs(os.path.join(root, "config"), exist_ok=True)

    public = {k: str(data.get(k) or "").strip() for k in PUBLIC_KEYS}
    try:
        from core.media_scan import reject_image_model
        for key, default in (
            ("cloud_model", "qwen/qwen3.8-27b"),
            ("ollama_model", "llama3"),
            ("openrouter_model", "openrouter/free"),
            ("groq_model", ""),
        ):
            if key in public:
                public[key] = reject_image_model(public[key], default)
        backend = (public.get("provider_backend") or data.get("provider_backend") or "").strip().lower()
        if not public.get("groq_model"):
            if backend == "ollama":
                public["groq_model"] = public.get("ollama_model") or "llama3"
            elif backend == "openrouter":
                public["groq_model"] = public.get("openrouter_model") or "openrouter/free"
            else:
                public["groq_model"] = public.get("cloud_model") or "qwen/qwen3.8-27b"
    except Exception:
        pass
    from core.home_id import parse_home_server_id
    saved_home = parse_home_server_id(public.get("home_server_id"))
    if saved_home:
        public["home_server_id"] = str(saved_home)
    secrets = {k: str(data.get(k) or "").strip() for k in SECRET_KEYS}

    existing = _load_encrypted_secrets(root)
    existing_public = _load_public(root)
    entered_xai = bool(secrets.get("xai_api_key"))
    for key in SECRET_KEYS:
        if not secrets.get(key) and existing.get(key):
            if key == "xai_api_key":
                epoch = str(public.get("xai_key_epoch") or existing_public.get("xai_key_epoch") or "").strip()
                if epoch != CURRENT_XAI_KEY_EPOCH:
                    continue
            secrets[key] = existing[key]
    if entered_xai:
        public["xai_key_epoch"] = CURRENT_XAI_KEY_EPOCH
        secrets["xai_api_key"] = str(data.get("xai_api_key") or "").strip()

    invalid: list[tuple[str, str]] = []
    public = couple_asset_toggles(public, invalid=invalid)
    # asset_enabled is default-OFF; couple does not rewrite it. Normalize for the log.
    from core.asset_safety import parse_safety_flag
    old_enabled_val, _ = parse_safety_flag(existing_public.get("asset_enabled"), default=False)
    new_enabled_val, _ = parse_safety_flag(public.get("asset_enabled"), default=False)
    old_owner_val, _ = parse_safety_flag(existing_public.get("asset_owner_only"), default=True)
    new_owner_val = _asset_flag(public, "asset_owner_only", default=True)
    old_enabled = "1" if old_enabled_val else "0"
    old_owner = "1" if old_owner_val else "0"
    new_enabled = "1" if new_enabled_val else "0"
    new_owner = "1" if new_owner_val else "0"
    old_adult = "1" if _asset_flag(existing_public, "asset_adult_enabled", default=False) else "0"
    old_approval = "1" if _asset_flag(existing_public, "asset_approval_enabled", default=True) else "0"
    new_adult = str(public.get("asset_adult_enabled") or "0")
    new_approval = str(public.get("asset_approval_enabled") or "1")
    key_set = bool((secrets.get("xai_api_key") or "").strip())

    _save_public(root, public)
    _save_encrypted_secrets(root, secrets)
    try:
        from core.audit import record, safe_activity
        fields = {}
        for key, raw in invalid:
            record("settings-invalid", outcome="error", key=key, value=raw, safety=True)
        safe_activity("settings-save", outcome="success", **fields)
        record(
            "settings-save",
            outcome="success",
            asset_enabled_old=old_enabled,
            asset_enabled_new=new_enabled,
            asset_owner_only_old=old_owner,
            asset_owner_only_new=new_owner,
            asset_adult_enabled_old=old_adult,
            asset_adult_enabled_new=new_adult,
            asset_approval_enabled_old=old_approval,
            asset_approval_enabled_new=new_approval,
            xai_key_set=key_set,
            **fields,
        )
        try:
            from core.classifier import mark_checker_settings_saved
            mark_checker_settings_saved(public, root)
        except Exception:
            pass
    except Exception:
        # A failed audit must fail the save: revert the written files and surface the error.
        try:
            _save_public(root, existing_public)
        except Exception:
            pass
        try:
            _save_encrypted_secrets(root, existing)
        except Exception:
            pass
        raise
    notices = []
    if old_adult != new_adult:
        notices.append(f"18+ mode changed from {old_adult} to {new_adult}.")
    if old_approval != new_approval:
        notices.append(f"Owner approval changed from {old_approval} to {new_approval}.")
    if notices:
        _notify_asset_toggle_owners(
            ["An ?asset safety toggle changed in Settings.", *notices]
        )


def apply_to_environ(root: str | None = None, override_existing: bool = False) -> dict[str, str]:
    cfg = load_all(root)
    for key, env_name in ENV_MAP.items():
        val = (cfg.get(key) or "").strip()
        if not val:
            continue
        if override_existing or not os.environ.get(env_name):
            os.environ[env_name] = val
    return cfg


def has_discord_token(root: str | None = None) -> bool:
    cfg = load_all(root)
    return bool((cfg.get("discord_token") or "").strip() or os.environ.get("DISCORD_TOKEN"))


def clear_secrets(root: str | None = None) -> None:
    root = root or _user_root()
    public = _load_public(root)
    _save_public(root, public)
    _save_encrypted_secrets(root, {k: "" for k in SECRET_KEYS})
    try:
        from core.audit import safe_activity
        safe_activity("clear-secrets", action="clear-secrets", outcome="success")
    except Exception:
        pass


def scrub_text(text: str, root: str | None = None) -> str:
    if not text:
        return text
    try:
        cfg = load_all(root)
    except Exception:
        return text
    out = text
    for key in SECRET_KEYS:
        val = (cfg.get(key) or "").strip()
        if val and len(val) >= 8 and val in out:
            out = out.replace(val, redact(val))
    for env_name in ENV_MAP.values():
        val = (os.environ.get(env_name) or "").strip()
        if val and len(val) >= 8 and val in out:
            out = out.replace(val, redact(val))
    return out
