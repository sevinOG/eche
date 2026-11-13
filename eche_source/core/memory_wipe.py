# core/memory_wipe.py
# One-time reset of everything Eche remembers. Owner only: it runs from the
# desktop app (Settings → Memory & Prompts → Reset all memory) or from the
# command line on the owner's machine. There is no Discord command for it.
#
# What it touches, on the home server's "bot memory" category:
#   * every user-{id} channel's "context" thread: the "Context for ..." pin and
#     the "Eche with ..." pin (or the older "Self Conversation Data" pin) are
#     edited to the empty Summary format. Old "New: USER: ..." lines go too.
#   * the #bot channel's "episodic" thread: every "Episodic {server id}:" pin
#     is edited to an empty Summary.
# and locally:
#   * memories/user-{id}/user.json and bot.json (the rolling buffers)
#   * memories/{server id}/recent.txt (episode lines waiting on a summary)
#   * this process's pin and thread caches
#
# Pins are edited, never deleted, and channels stay. Edits never ping. Running
# it twice is safe: a pin that is already empty is left alone.
#
# Command line, from eche_source (or with the packaged Eche.exe):
#   python -m core.memory_wipe --yes
#   Eche.exe --reset-memory --yes

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field

EMPTY = "(none yet)"


@dataclass
class WipeReport:
    users: int = 0
    user_pins: int = 0
    bot_pins: int = 0
    episode_pins: int = 0
    unchanged: int = 0
    legacy: int = 0
    no_thread: int = 0
    local_files: int = 0
    failed: list[str] = field(default_factory=list)
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem and not self.failed

    def summary(self) -> str:
        if self.problem:
            return f"Memory reset did not run: {self.problem}"
        parts = [
            f"{self.users} user threads",
            f"{self.user_pins} user pins reset",
            f"{self.bot_pins} Eche pins reset",
            f"{self.episode_pins} episode pins reset",
            f"{self.unchanged} already empty",
            f"{self.legacy} old-format pins migrated",
            f"{self.local_files} local buffers cleared",
        ]
        if self.no_thread:
            parts.append(f"{self.no_thread} user channels had no context thread")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return "Memory reset: " + ", ".join(parts) + "."


def _name_from_title(first_line: str, prefix: str) -> str:
    """"Context for alice:" -> "alice". Filtered so a title cannot ping."""
    from core.content_filter import clean_memory_text

    text = (first_line or "").strip()
    if text.startswith(prefix):
        text = text[len(prefix):]
    text = text.rstrip(":").strip()
    return " ".join(clean_memory_text(text).split())


def fresh_user_pin(content: str, user_id: int) -> str:
    from core.context_manager import summary_only
    from core.discord_store import USER_CONTEXT_HEADER

    first = (content or "").split("\n", 1)[0]
    name = _name_from_title(first, USER_CONTEXT_HEADER) or str(int(user_id))
    return summary_only(f"{USER_CONTEXT_HEADER}{name}:\n", "")


def fresh_bot_pin(content: str, fallback_label: str) -> str:
    from core.discord_store import BOT_CONTEXT_HEADER, bot_memory_initial

    first = (content or "").split("\n", 1)[0]
    name = ""
    if first.startswith(BOT_CONTEXT_HEADER):
        name = _name_from_title(first, BOT_CONTEXT_HEADER)
    return bot_memory_initial(name or fallback_label)


def fresh_episode_pin(content: str) -> str:
    heading = (content or "").split("\n", 1)[0].strip()
    return f"{heading}\nSummary:\n{EMPTY}\n"


def _is_legacy(content: str) -> bool:
    from core.discord_store import OLD_BOT_CONTEXT_HEADER

    text = content or ""
    return (
        "\nNew:" in text
        or "USER:" in text
        or "BOT:" in text
        or text.startswith(OLD_BOT_CONTEXT_HEADER)
    )


async def _rewrite(message, fresh: str, report: WipeReport, what: str) -> bool:
    """Edit one pin to `fresh`. True when it changed. Never pings."""
    from core.discord_store import edit_record, same_discord_text

    current = getattr(message, "content", "") or ""
    if same_discord_text(current, fresh):
        report.unchanged += 1
        return False
    try:
        await edit_record(message, fresh)
    except Exception as exc:
        report.failed.append(f"{what}: {type(exc).__name__}: {exc}")
        return False
    if _is_legacy(current):
        report.legacy += 1
    return True


async def _wipe_user_thread(user_id: int, channel, report: WipeReport) -> None:
    from core.discord_store import (
        BOT_CONTEXT_HEADER,
        OLD_BOT_CONTEXT_HEADER,
        THREAD_CONTEXT,
        USER_CONTEXT_HEADER,
        _list_pins,
        _open_thread,
        find_thread,
    )

    thread = await _open_thread(await find_thread(channel, THREAD_CONTEXT))
    if thread is None:
        report.no_thread += 1
        return
    report.users += 1
    pins, ok = await _list_pins(thread)
    if not ok:
        report.failed.append(f"user-{user_id}: could not read pins")
        return
    label = str(int(user_id))
    for pin in pins:
        content = getattr(pin, "content", "") or ""
        if content.startswith(USER_CONTEXT_HEADER):
            name = _name_from_title(content.split("\n", 1)[0], USER_CONTEXT_HEADER)
            if name:
                label = name
    for pin in pins:
        content = getattr(pin, "content", "") or ""
        if content.startswith(USER_CONTEXT_HEADER):
            if await _rewrite(pin, fresh_user_pin(content, user_id), report, f"user-{user_id} user pin"):
                report.user_pins += 1
        elif content.startswith(BOT_CONTEXT_HEADER) or content.startswith(OLD_BOT_CONTEXT_HEADER):
            if await _rewrite(pin, fresh_bot_pin(content, label), report, f"user-{user_id} Eche pin"):
                report.bot_pins += 1


async def _wipe_episodes(guild, report: WipeReport) -> None:
    from core.discord_store import THREAD_EPISODIC, _list_pins, _open_thread, bot_channel, find_thread

    channel = bot_channel(guild)
    if channel is None:
        return
    thread = await _open_thread(await find_thread(channel, THREAD_EPISODIC))
    if thread is None:
        return
    pins, ok = await _list_pins(thread)
    if not ok:
        report.failed.append("#bot episodic: could not read pins")
        return
    for pin in pins:
        content = getattr(pin, "content", "") or ""
        if not content.startswith("Episodic "):
            continue
        if await _rewrite(pin, fresh_episode_pin(content), report, "episode pin"):
            report.episode_pins += 1


def clear_local_memory() -> int:
    """Rolling buffers and waiting episode lines. Returns how many files went."""
    from core.paths import memories_dir

    base = memories_dir()
    removed = 0
    if not os.path.isdir(base):
        return 0
    for name in os.listdir(base):
        folder = os.path.join(base, name)
        if not os.path.isdir(folder):
            continue
        if name.startswith("user-"):
            targets = ("user.json", "bot.json")
        elif name.isdigit():
            targets = ("recent.txt",)
        else:
            continue
        for target in targets:
            path = os.path.join(folder, target)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    removed += 1
                except OSError as exc:
                    print(f"[memory_wipe] could not remove {path}: {exc}", flush=True)
    return removed


def _audit_wipe(guild, report: WipeReport) -> None:
    try:
        from core.audit import safe_activity
        safe_activity(
            "memory-wipe",
            action="memory-wipe",
            outcome="success" if not report.problem else "error",
            guild_id=getattr(guild, "id", None),
        )
    except Exception:
        pass


async def wipe_memory(guild, *, clear_local: bool = True, log=None) -> WipeReport:
    """Reset every memory pin on `guild` (the home server) and the local buffers."""
    from core.discord_store import clear_caches, find_category, list_user_channels

    say = log or (lambda text: print(text, flush=True))
    report = WipeReport()
    if guild is None:
        report.problem = "home server not found"
        say(report.summary())
        _audit_wipe(guild, report)
        return report
    if find_category(guild) is None:
        report.problem = 'no "bot memory" category on the home server'
        say(report.summary())
        _audit_wipe(guild, report)
        return report
    for user_id, channel in list_user_channels(guild):
        await _wipe_user_thread(user_id, channel, report)
    await _wipe_episodes(guild, report)
    if clear_local:
        report.local_files = clear_local_memory()
    clear_caches()
    for line in report.failed:
        say(f"[memory_wipe] {line}")
    say(report.summary())
    _audit_wipe(guild, report)
    return report


async def _home_guild(client, home_raw: str):
    from core.home_id import parse_home_server_id

    guild_id = parse_home_server_id(str(home_raw or ""))
    if not guild_id:
        return None
    guild = client.get_guild(guild_id)
    if guild is None:
        try:
            guild = await client.fetch_guild(guild_id)
        except Exception:
            guild = None
    return guild


def run_wipe(token: str = "", home_raw: str = "", *, log=None) -> WipeReport:
    """Own bot connection, one wipe, then disconnect. Blocks until done."""
    import discord

    if not token or not home_raw:
        try:
            from dotenv import load_dotenv
            from core.paths import ensure_user_layout
            from core.secrets import load_all

            root = ensure_user_layout()
            load_dotenv(os.path.join(root, ".env"))
            settings = load_all(root)
        except Exception:
            settings = {}
        token = token or (settings.get("discord_token") or "").strip() or os.getenv("DISCORD_TOKEN", "")
        home_raw = home_raw or settings.get("home_server_id") or os.getenv("HOME_SERVER_ID", "")
    report = WipeReport()
    if not token:
        report.problem = "Discord token missing. Set it in Settings."
        return report
    if not home_raw:
        report.problem = "Home Server ID missing. Set it in Settings."
        return report

    intents = discord.Intents.default()
    intents.guilds = True
    client = discord.Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())
    holder: dict = {"report": report}

    @client.event
    async def on_ready():
        try:
            guild = await _home_guild(client, home_raw)
            holder["report"] = await wipe_memory(guild, log=log)
        except Exception as exc:
            holder["report"].problem = f"{type(exc).__name__}: {exc}"
        finally:
            await client.close()

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(client.start(token))
    except Exception as exc:
        if not holder["report"].problem and holder["report"].users == 0:
            holder["report"].problem = f"{type(exc).__name__}: {exc}"
    finally:
        loop.close()
    return holder["report"]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--yes" not in args:
        print(
            "This resets every memory pin on the home server and the local buffers.\n"
            "Stop the bot first, then run again with --yes.",
            flush=True,
        )
        return 2
    report = run_wipe()
    print(report.summary(), flush=True)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
