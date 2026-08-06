# core/user_context.py
# Local + Discord-compatible user context files, organized by server (guild).
#
# On disk layout (portable under user_dir()):
#   context/
#     {server_id}/
#       _meta.json          # optional: {"name": "My Guild"}
#       {user_id}.txt       # same Summary/New shape as Discord pins
#
# Discord live layout (home / any guild the bot can see):
#   category memory-{user_id} / channel context / pinned message
#
# Parsing mirrors bot_memory / context_manager so GUI and bot share one format.

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

HEADER_RE = re.compile(r"^Context for (.+?):\s*$", re.MULTILINE)
MEMORY_CAT_RE = re.compile(r"^memory-(\d+)$", re.IGNORECASE)


def _root() -> str:
    try:
        from core.paths import ensure_user_layout
        return ensure_user_layout()
    except Exception:
        return os.getcwd()


def context_root() -> str:
    try:
        from core.paths import context_dir
        return context_dir()
    except Exception:
        path = os.path.join(_root(), "context")
        os.makedirs(path, exist_ok=True)
        return path


def server_dir(server_id: str | int) -> str:
    try:
        from core.paths import context_dir
        return context_dir(server_id)
    except Exception:
        path = os.path.join(context_root(), str(server_id))
        os.makedirs(path, exist_ok=True)
        return path


def user_context_path(server_id: str | int, user_id: str | int) -> str:
    return os.path.join(server_dir(server_id), f"{user_id}.txt")


def _meta_path(server_id: str | int) -> str:
    return os.path.join(server_dir(server_id), "_meta.json")


def load_server_meta(server_id: str | int) -> dict[str, Any]:
    path = _meta_path(server_id)
    if not os.path.isfile(path):
        return {"id": str(server_id), "name": str(server_id)}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"id": str(server_id), "name": str(server_id)}
        data.setdefault("id", str(server_id))
        data.setdefault("name", str(server_id))
        return data
    except Exception:
        return {"id": str(server_id), "name": str(server_id)}


def save_server_meta(server_id: str | int, name: str = "", **extra: Any) -> None:
    path = _meta_path(server_id)
    data = load_server_meta(server_id)
    if name:
        data["name"] = name
    data.update(extra)
    data["id"] = str(server_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


@dataclass
class ParsedContext:
    """Structured view of a user context pin / file."""

    header: str = ""
    display_name: str = ""
    summary: str = ""
    new_lines: list[str] = field(default_factory=list)
    raw: str = ""
    malformed: bool = False

    @property
    def new_text(self) -> str:
        return "\n".join(self.new_lines)

    def to_raw(self) -> str:
        name = self.display_name or "User"
        header = self.header.strip() or f"Context for {name}:"
        if not header.endswith(":"):
            header = header.rstrip() + ":"
        summary = (self.summary or "(none yet)").strip()
        new_body = "\n".join(self.new_lines).rstrip()
        parts = [header, "", "Summary:", summary, "", "New:"]
        if new_body:
            parts.append(new_body)
        parts.append("")
        return "\n".join(parts)


def empty_context(display_name: str = "User") -> str:
    return (
        f"Context for {display_name}:\n\n"
        "Summary:\n(none yet)\n\n"
        "New:\n"
    )


def parse_context(text: str) -> ParsedContext:
    """Parse Discord-pin / local file text into sections."""
    raw = text or ""
    if not raw.strip():
        return ParsedContext(raw=raw, malformed=True)

    display_name = ""
    header = ""
    m = HEADER_RE.search(raw)
    if m:
        display_name = m.group(1).strip()
        header = f"Context for {display_name}:"
    elif raw.lstrip().startswith("Self Conversation Data"):
        display_name = "Bot"
        header = raw.splitlines()[0].strip()

    if "Summary:" not in raw or "New:" not in raw:
        return ParsedContext(
            header=header,
            display_name=display_name,
            summary="",
            new_lines=[],
            raw=raw,
            malformed=True,
        )

    try:
        summary_start = raw.index("Summary:") + len("Summary:")
        new_start = raw.index("New:")
    except ValueError:
        return ParsedContext(
            header=header,
            display_name=display_name,
            raw=raw,
            malformed=True,
        )

    summary = raw[summary_start:new_start].strip()
    new_section = raw[new_start + len("New:") :]
    new_lines = [ln for ln in new_section.splitlines() if ln.strip()]

    return ParsedContext(
        header=header,
        display_name=display_name,
        summary=summary,
        new_lines=new_lines,
        raw=raw,
        malformed=False,
    )


def list_servers() -> list[dict[str, Any]]:
    """List local server folders under context/."""
    root = context_root()
    out: list[dict[str, Any]] = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        if not name.isdigit() and not name.replace("-", "").isdigit():
            # Allow any folder name, but prefer numeric guild ids
            pass
        meta = load_server_meta(name)
        users = list_users(name)
        meta = {**meta, "id": name, "user_count": len(users)}
        out.append(meta)
    return out


def list_users(server_id: str | int) -> list[dict[str, Any]]:
    """List user context files for a server."""
    sdir = server_dir(server_id)
    users: list[dict[str, Any]] = []
    if not os.path.isdir(sdir):
        return users
    for name in sorted(os.listdir(sdir)):
        if not name.endswith(".txt"):
            continue
        user_id = name[:-4]
        path = os.path.join(sdir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            text = ""
        parsed = parse_context(text)
        users.append(
            {
                "id": user_id,
                "display_name": parsed.display_name or user_id,
                "path": path,
                "summary_preview": (parsed.summary or "")[:120],
                "new_count": len(parsed.new_lines),
                "malformed": parsed.malformed,
            }
        )
    return users


def load_user_context(server_id: str | int, user_id: str | int) -> str:
    path = user_context_path(server_id, user_id)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def save_user_context(
    server_id: str | int,
    user_id: str | int,
    content: str,
    display_name: str | None = None,
) -> str:
    """Write raw context text; auto-heal empty structure."""
    text = (content or "").strip()
    if not text:
        text = empty_context(display_name or str(user_id))
    elif "Summary:" not in text or "New:" not in text:
        # Preserve free-form notes under New:
        name = display_name or str(user_id)
        text = (
            f"Context for {name}:\n\n"
            f"Summary:\n(none yet)\n\n"
            f"New:\n{text.rstrip()}\n"
        )
    path = user_context_path(server_id, user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    return path


def delete_user_context(server_id: str | int, user_id: str | int) -> bool:
    path = user_context_path(server_id, user_id)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def append_user_line(
    server_id: str | int,
    user_id: str | int,
    line: str,
    display_name: str = "User",
    role: str = "USER",
) -> str:
    """Append a USER/BOT line into New: (local mirror of Discord pin)."""
    raw = load_user_context(server_id, user_id)
    if not raw:
        raw = empty_context(display_name)
    parsed = parse_context(raw)
    if parsed.malformed:
        parsed = parse_context(empty_context(display_name))
    if not parsed.display_name and display_name:
        parsed.display_name = display_name
    entry = line if line.startswith(("USER:", "BOT:", "SYSTEM:")) else f"{role}: {line}"
    parsed.new_lines.append(entry)
    text = parsed.to_raw()
    # Soft cap similar to Discord 2000-char pin
    if len(text) > 1990:
        parsed.summary = (parsed.summary + "\n" + parsed.new_text).strip()[:800]
        parsed.new_lines = [entry]
        text = parsed.to_raw()
    return save_user_context(server_id, user_id, text, display_name=parsed.display_name)


def ensure_local_from_discord_text(
    server_id: str | int,
    user_id: str | int,
    pin_text: str,
    server_name: str = "",
) -> str:
    """Cache a Discord pin into the local tree."""
    if server_name:
        save_server_meta(server_id, name=server_name)
    return save_user_context(server_id, user_id, pin_text)


def import_legacy_memories(server_id: str | int | None = None) -> int:
    """
    Import flat memories/{user_id}.txt into context/{server_id}/{user_id}.txt.
    Uses home_server_id from settings when server_id is omitted.
    """
    if server_id is None:
        try:
            from core.secrets import load_all
            cfg = load_all()
            server_id = (cfg.get("home_server_id") or "0").strip() or "0"
        except Exception:
            server_id = "0"
    try:
        from core.paths import memories_dir
        mdir = memories_dir()
    except Exception:
        mdir = os.path.join(_root(), "memories")
    if not os.path.isdir(mdir):
        return 0
    count = 0
    for name in os.listdir(mdir):
        path = os.path.join(mdir, name)
        if not os.path.isfile(path):
            continue
        if name.endswith(".txt"):
            uid = name[:-4]
        elif name.endswith(".json"):
            uid = name[:-5]
        else:
            continue
        if not uid.isdigit():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                body = f.read()
        except Exception:
            continue
        if "Summary:" in body and "New:" in body:
            save_user_context(server_id, uid, body)
        else:
            # Wrap legacy SUMMARY/CODES style
            save_user_context(
                server_id,
                uid,
                empty_context(uid).replace(
                    "Summary:\n(none yet)",
                    f"Summary:\n{body.strip() or '(imported legacy memory)'}",
                ),
            )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Discord accessors (same shape as botmemorywindow workers)
# ---------------------------------------------------------------------------

async def discord_list_user_contexts(guild) -> list[dict[str, Any]]:
    """
    Scan a guild for memory-{user_id} categories and return pin previews.
    Read-only against Discord — does not write local files.
    """
    import discord  # noqa: F401 — runtime

    results: list[dict[str, Any]] = []
    categories = list(getattr(guild, "categories", None) or [])
    if not categories:
        # fetch_guild often omits channel cache — pull via HTTP
        try:
            from discord import CategoryChannel
            channels = await guild.fetch_channels()
            categories = [c for c in channels if isinstance(c, CategoryChannel)]
        except Exception:
            categories = []

    for category in categories:
        m = MEMORY_CAT_RE.match(category.name or "")
        if not m:
            continue
        user_id = m.group(1)
        channel = discord.utils.get(category.text_channels, name="context")
        if not channel:
            try:
                channel = discord.utils.get(
                    guild.text_channels, name="context", category_id=category.id
                )
            except Exception:
                channel = None
        if not channel:
            continue
        pins = await channel.pins()
        text = pins[0].content if pins else ""
        parsed = parse_context(text)
        results.append(
            {
                "id": user_id,
                "display_name": parsed.display_name or user_id,
                "channel_id": channel.id,
                "raw": text,
                "summary_preview": (parsed.summary or "")[:120],
                "new_count": len(parsed.new_lines),
            }
        )
    results.sort(key=lambda r: (r.get("display_name") or r["id"]).lower())
    return results


async def discord_fetch_user_context(guild, user_id: str | int) -> str:
    import discord

    cat = discord.utils.get(guild.categories, name=f"memory-{user_id}")
    if not cat:
        return ""
    channel = discord.utils.get(cat.text_channels, name="context")
    if not channel:
        return ""
    pins = await channel.pins()
    return pins[0].content if pins else ""


async def discord_save_user_context(guild, user_id: str | int, content: str) -> str:
    import discord

    cat = discord.utils.get(guild.categories, name=f"memory-{user_id}")
    if not cat:
        raise RuntimeError(f"No memory-{user_id} category in this server.")
    channel = discord.utils.get(cat.text_channels, name="context")
    if not channel:
        raise RuntimeError(f"No context channel under memory-{user_id}.")
    pins = await channel.pins()
    if not pins:
        msg = await channel.send(content)
        await msg.pin()
        return msg.content
    await pins[0].edit(content=content)
    return pins[0].content
