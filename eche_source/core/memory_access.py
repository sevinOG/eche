# core/memory_access.py
# Let each person read their own memory. Owner only: it runs from the desktop
# app (Settings → Context & Memory → Let users see their memory) or from the
# command line on the owner's machine. There is no Discord command for it.
#
# On the home server's "bot memory" category, for every user-{id} channel:
#   * if user {id} is a member of the home server, give them a read-only
#     overwrite on that channel: they can see it and read its history, but
#     cannot send, post in threads, react, or start threads.
#   * the "context" thread: a public thread follows the channel, so nothing
#     more is needed. A private thread gets them added as a member.
# Nobody else's channel changes, and #bot is never touched. Someone who is
# not on the home server is listed and skipped; they need an invite first.
# Running it twice is safe: an overwrite that already matches is left alone.
#
# Command line, from eche_source (or with the packaged Eche.exe):
#   python -m core.memory_access --dry-run
#   python -m core.memory_access --yes
#   Eche.exe --unlock-memory --dry-run
#   Eche.exe --unlock-memory --yes

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field

REASON = "Eche: let this person read their own memory"

# The read-only overwrite each person gets on their own channel.
READ_ONLY = {
    "view_channel": True,
    "read_message_history": True,
    "send_messages": False,
    "send_messages_in_threads": False,
    "add_reactions": False,
    "create_public_threads": False,
    "create_private_threads": False,
}


@dataclass
class AccessReport:
    dry_run: bool = False
    channels: int = 0
    granted: list[int] = field(default_factory=list)
    already: list[int] = field(default_factory=list)
    not_in_server: list[int] = field(default_factory=list)
    thread_added: list[int] = field(default_factory=list)
    no_thread: list[int] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem and not self.failed

    def summary(self) -> str:
        if self.problem:
            return f"Memory access did not run: {self.problem}"
        verb = "would grant" if self.dry_run else "granted"
        parts = [
            f"{self.channels} user channels",
            f"{len(self.granted)} {verb}",
            f"{len(self.already)} already had access",
            f"{len(self.not_in_server)} not in the server",
        ]
        if self.thread_added:
            added = "would be added" if self.dry_run else "added"
            parts.append(f"{len(self.thread_added)} {added} to a private context thread")
        if self.no_thread:
            parts.append(f"{len(self.no_thread)} had no context thread")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        head = "Memory access (dry run, nothing changed): " if self.dry_run else "Memory access: "
        return head + ", ".join(parts) + "."

    def details(self) -> list[str]:
        verb = "Would grant" if self.dry_run else "Granted"
        lines = []
        for label, ids in (
            (verb, self.granted),
            ("Already had", self.already),
            ("Not in server", self.not_in_server),
            ("Private thread " + ("would add" if self.dry_run else "added"), self.thread_added),
            ("No context thread", self.no_thread),
        ):
            lines.append(f"{label} ({len(ids)}): " + (", ".join(str(i) for i in ids) or "-"))
        for line in self.failed:
            lines.append(f"Failed: {line}")
        return lines


def read_only_overwrite():
    import discord

    return discord.PermissionOverwrite(**READ_ONLY)


def has_read_only(overwrite) -> bool:
    """True when the overwrite already sets every read-only value we want."""
    if overwrite is None:
        return False
    for name, want in READ_ONLY.items():
        if getattr(overwrite, name, None) is not want:
            return False
    return True


def merged_overwrite(current):
    """Keep any other values the owner set by hand, and apply ours on top."""
    import discord

    values = {}
    if current is not None:
        try:
            for name, value in current:
                if value is not None:
                    values[name] = value
        except TypeError:
            pass
    values.update(READ_ONLY)
    return discord.PermissionOverwrite(**values)


async def _member(guild, user_id: int):
    """The member, or None when they are not on the server. Raises on other errors."""
    import discord

    getter = getattr(guild, "get_member", None)
    member = getter(user_id) if getter else None
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        return None


def _is_private(thread) -> bool:
    import discord

    kind = getattr(thread, "type", None)
    return kind == discord.ChannelType.private_thread or str(kind) == "private_thread"


async def _thread_has(thread, user_id: int) -> bool:
    for member in list(getattr(thread, "members", None) or []):
        if int(getattr(member, "id", 0)) == user_id:
            return True
    fetch = getattr(thread, "fetch_members", None)
    if fetch is None:
        return False
    try:
        for member in await fetch():
            if int(getattr(member, "id", 0)) == user_id:
                return True
    except Exception:
        return False
    return False


async def _unlock_one(user_id: int, channel, guild, report: AccessReport, dry_run: bool) -> None:
    from core.discord_store import THREAD_CONTEXT, find_thread

    report.channels += 1
    try:
        member = await _member(guild, user_id)
    except Exception as exc:
        report.failed.append(f"user-{user_id}: member lookup: {type(exc).__name__}: {exc}")
        return
    if member is None:
        report.not_in_server.append(user_id)
        return

    current = channel.overwrites_for(member)
    if has_read_only(current):
        report.already.append(user_id)
    else:
        if not dry_run:
            try:
                await channel.set_permissions(member, overwrite=merged_overwrite(current), reason=REASON)
            except Exception as exc:
                report.failed.append(f"user-{user_id}: overwrite: {type(exc).__name__}: {exc}")
                return
        report.granted.append(user_id)

    try:
        thread = await find_thread(channel, THREAD_CONTEXT)
    except Exception:
        thread = None
    if thread is None:
        report.no_thread.append(user_id)
        return
    if not _is_private(thread) or await _thread_has(thread, user_id):
        return
    if not dry_run:
        try:
            await thread.add_user(member)
        except Exception as exc:
            report.failed.append(f"user-{user_id}: thread: {type(exc).__name__}: {exc}")
            return
    report.thread_added.append(user_id)


def _audit_unlock(guild, report: AccessReport) -> None:
    try:
        from core.audit import safe_activity
        safe_activity(
            "memory-access",
            action="memory-access",
            outcome="success" if not report.problem else "error",
            guild_id=getattr(guild, "id", None),
        )
    except Exception:
        pass


async def unlock_memory(guild, *, dry_run: bool = False, log=None) -> AccessReport:
    """Give each user-{id} channel's owner read-only access to it."""
    from core.discord_store import find_category, list_user_channels

    say = log or (lambda text: print(text, flush=True))
    report = AccessReport(dry_run=dry_run)
    if guild is None:
        report.problem = "home server not found"
        say(report.summary())
        _audit_unlock(guild, report)
        return report
    if find_category(guild) is None:
        report.problem = 'no "bot memory" category on the home server'
        say(report.summary())
        _audit_unlock(guild, report)
        return report
    for user_id, channel in list_user_channels(guild):
        await _unlock_one(int(user_id), channel, guild, report, dry_run)
    for line in report.details():
        say(f"[memory_access] {line}")
    say(report.summary())
    _audit_unlock(guild, report)
    return report


def run_unlock(token: str = "", home_raw: str = "", *, dry_run: bool = False, log=None) -> AccessReport:
    """Own bot connection, one pass, then disconnect. Blocks until done."""
    import discord

    from core.memory_wipe import _home_guild

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
    report = AccessReport(dry_run=dry_run)
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
            holder["report"] = await unlock_memory(guild, dry_run=dry_run, log=log)
        except Exception as exc:
            holder["report"].problem = f"{type(exc).__name__}: {exc}"
        finally:
            await client.close()

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(client.start(token))
    except Exception as exc:
        if not holder["report"].problem and holder["report"].channels == 0:
            holder["report"].problem = f"{type(exc).__name__}: {exc}"
    finally:
        loop.close()
    return holder["report"]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in args
    if not dry_run and "--yes" not in args:
        print(
            "This gives each person read-only access to their own memory channel\n"
            "on the home server. Run with --dry-run to see who, or --yes to do it.",
            flush=True,
        )
        return 2
    report = run_unlock(dry_run=dry_run)
    if report.problem:
        print(report.summary(), flush=True)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
