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
from typing import Any

# Keys that must never sit in plaintext settings.json
SECRET_KEYS = (
    "discord_token",
    "inf_api_key",       # GROQ_API_KEY
    "us_access_token",   # US_ACCESS_TOKEN (Unsplash)
    "us_secret_token",   # US_SECRET_TOKEN (Unsplash)
)

# Non-secret app config (IDs, flags, etc.)
PUBLIC_KEYS = (
    "home_server_id",
    "thoughts_thread_id",
    "groq_model",
    "provider_backend",  # cloud (Groq default) | ollama
    "summarizer_model",  # optional model just for memory compression
    "summarizer_prompt_path",  # optional path; blank = config/summarizer_prompt.txt
    "project_path",  # portable package root for rebuilds
    "suppress_no_provider_warn",  # "1" = don't re-show missing inference key dialog
)

# Map settings key -> environment variable used by the bot
ENV_MAP = {
    "discord_token": "DISCORD_TOKEN",
    "inf_api_key": "GROQ_API_KEY",
    "us_access_token": "US_ACCESS_TOKEN",
    "us_secret_token": "US_SECRET_TOKEN",
    "home_server_id": "HOME_SERVER_ID",
    "thoughts_thread_id": "THOUGHTS_THREAD_ID",
    "groq_model": "GROQ_MODEL",
    "provider_backend": "ECHE_PROVIDER",
    "summarizer_model": "SUMMARIZER_MODEL",
    "summarizer_prompt_path": "SUMMARIZER_PROMPT_PATH",
}

# Human labels for Settings UI (key -> label)
SECRET_FIELD_META = (
    ("discord_token", "Discord Token", "Bot token from Discord Developer Portal"),
    ("inf_api_key", "Provider API Key", "Key for your AI provider (default stack uses Groq)"),
    ("us_access_token", "Unsplash Access Token", "Image search (optional)"),
    ("us_secret_token", "Unsplash Secret Token", "Image search (optional)"),
)

PUBLIC_FIELD_META = (
    ("home_server_id", "Home Server ID", "Discord server used for memory / economy"),
    ("thoughts_thread_id", "Thoughts Thread ID", "Optional thread for internal thoughts"),
    (
        "groq_model",
        "Model ID",
        "Which AI model to call (default: llama-3.3-70b-versatile). Use ℹ to learn more.",
    ),
    # project_path / suppress_no_provider_warn edited elsewhere
)

_SECRETS_FILENAME = "secrets.dpapi.json"
_SETTINGS_FILENAME = "settings.json"


def _user_root() -> str:
    try:
        from core.paths import ensure_user_layout
        return ensure_user_layout()
    except Exception:
        return os.getcwd()


def settings_path(root: str | None = None) -> str:
    root = root or _user_root()
    return os.path.join(root, "config", _SETTINGS_FILENAME)


def secrets_path(root: str | None = None) -> str:
    root = root or _user_root()
    return os.path.join(root, "config", _SECRETS_FILENAME)


def _dpapi_protect(data: bytes, description: str = "Eche secret") -> bytes:
    """Windows DPAPI CryptProtectData via ctypes (no pywin32 dependency)."""
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

    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
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
    """Windows DPAPI CryptUnprotectData via ctypes."""
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
    """Safe preview for logs/UI — never full secret."""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "•" * 8
    return f"{value[:keep]}…{'•' * 6}…{value[-keep:]}"


def _restrict_acl_windows(path: str) -> None:
    """Best-effort: ACL so only the current user can read the secrets file."""
    try:
        import getpass
        import subprocess

        user = getpass.getuser()
        # Inheritance disabled; grant current user only
        subprocess.run(
            [
                "icacls",
                path,
                "/inheritance:r",
                "/grant:r",
                f"{user}:(R,W)",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass


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
            # Corrupt / other-user ciphertext — skip
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
        _restrict_acl_windows(path)


def _load_public(root: str) -> dict[str, str]:
    data = _read_json(settings_path(root))
    out: dict[str, str] = {}
    for key in PUBLIC_KEYS:
        out[key] = str(data.get(key) or "").strip()
    return out


def _save_public(root: str, public: dict[str, str]) -> None:
    data = _read_json(settings_path(root))
    # Drop any leftover plaintext secrets from older versions
    for key in SECRET_KEYS:
        data.pop(key, None)
    for key in PUBLIC_KEYS:
        data[key] = str(public.get(key) or "").strip()
    data["secrets_backend"] = "dpapi"
    _write_json(settings_path(root), data)


def _seed_from_dotenv(root: str) -> dict[str, str]:
    """Dev convenience: read .env once (does not write it)."""
    seeded: dict[str, str] = {k: "" for k in (*SECRET_KEYS, *PUBLIC_KEYS)}
    env_path = os.path.join(root, ".env")
    if not os.path.isfile(env_path):
        return seeded
    try:
        from dotenv import dotenv_values
        env = dotenv_values(env_path)
    except Exception:
        return seeded

    # settings key -> .env key (inverse of ENV_MAP for secrets + public)
    for settings_key, env_name in ENV_MAP.items():
        seeded[settings_key] = (env.get(env_name) or "").strip()
    return seeded


def _migrate_plaintext_settings(root: str) -> None:
    """
    If config/settings.json still has plaintext tokens (old builds),
    move them into the DPAPI store and scrub the JSON file.
    """
    path = settings_path(root)
    data = _read_json(path)
    if not data:
        return

    found = False
    secrets = _load_encrypted_secrets(root)
    for key in SECRET_KEYS:
        plain = str(data.get(key) or "").strip()
        if plain:
            # Prefer existing encrypted value if already present
            if not secrets.get(key):
                secrets[key] = plain
            found = True
            data.pop(key, None)

    if found:
        _save_encrypted_secrets(root, secrets)
        data["secrets_backend"] = "dpapi"
        data["migrated_plaintext"] = True
        _write_json(path, data)


def load_all(root: str | None = None) -> dict[str, str]:
    """
    Load public + secret config (decrypted in memory only).
    Priority for secrets: DPAPI store > .env seed (if DPAPI empty).
    """
    root = root or _user_root()
    os.makedirs(os.path.join(root, "config"), exist_ok=True)
    _migrate_plaintext_settings(root)

    public = _load_public(root)
    secrets = _load_encrypted_secrets(root)

    # Fill any empty gaps from .env (dev / first run) without writing .env
    seeded = _seed_from_dotenv(root)
    for key in SECRET_KEYS:
        if not secrets.get(key) and seeded.get(key):
            secrets[key] = seeded[key]
    for key in PUBLIC_KEYS:
        if not public.get(key) and seeded.get(key):
            public[key] = seeded[key]

    out = {**public, **secrets}
    # Ensure all keys exist
    for key in (*SECRET_KEYS, *PUBLIC_KEYS):
        out.setdefault(key, "")
    # Portable source discovery — never hardcode a machine path
    stored = (out.get("project_path") or "").strip()
    try:
        from core.paths import resolve_source_root, is_source_tree
        resolved = resolve_source_root(stored or None)
        # Prefer resolved when stored is empty, missing, or not a real source tree
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
    if not (out.get("groq_model") or "").strip():
        out["groq_model"] = "llama-3.3-70b-versatile"
    backend = (out.get("provider_backend") or "").strip().lower()
    if backend not in ("cloud", "ollama"):
        out["provider_backend"] = "cloud"
    return out


def save_all(data: dict[str, Any], root: str | None = None) -> None:
    """Persist public JSON + DPAPI-encrypted secrets."""
    root = root or _user_root()
    os.makedirs(os.path.join(root, "config"), exist_ok=True)

    public = {k: str(data.get(k) or "").strip() for k in PUBLIC_KEYS}
    secrets = {k: str(data.get(k) or "").strip() for k in SECRET_KEYS}

    # If UI left a secret blank, keep previous encrypted value
    existing = _load_encrypted_secrets(root)
    for key in SECRET_KEYS:
        if not secrets.get(key) and existing.get(key):
            secrets[key] = existing[key]

    _save_public(root, public)
    _save_encrypted_secrets(root, secrets)


def apply_to_environ(root: str | None = None, override_existing: bool = False) -> dict[str, str]:
    """
    Load config into process environment for the bot / child process.
    Returns the loaded map (including secrets) for in-process use.
    """
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
    """Remove all stored secrets; keep public settings."""
    root = root or _user_root()
    public = _load_public(root)
    _save_public(root, public)
    _save_encrypted_secrets(root, {k: "" for k in SECRET_KEYS})


def scrub_text(text: str, root: str | None = None) -> str:
    """Redact known secret values from a log line (best-effort)."""
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
    # Common env-style leaks
    for env_name in ENV_MAP.values():
        val = (os.environ.get(env_name) or "").strip()
        if val and len(val) >= 8 and val in out:
            out = out.replace(val, redact(val))
    return out
