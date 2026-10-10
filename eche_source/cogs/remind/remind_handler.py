# reminders/remind_handler.py
# Each reminder is one message in bot memory / bot / reminders.
# The process only keeps the timer. Restart reads those messages again.

import asyncio
import discord
import re
from datetime import datetime, timedelta, timezone

try:
    from dateparser import parse as parse_date
except ImportError:  # frozen/source missing optional dep — duration parser still works
    parse_date = None


def _user_token(user) -> str:
    if user == "@everyone":
        return "everyone"
    if user == "@here":
        return "here"
    if isinstance(user, str) and user.startswith("role:"):
        return user
    user_id = getattr(user, "id", None)
    if user_id is not None:
        return f"member:{user_id}"
    return "unknown"


def _naive_utc(fire_time: datetime) -> datetime:
    if fire_time.tzinfo is not None:
        return fire_time.astimezone(timezone.utc).replace(tzinfo=None)
    return fire_time


def format_reminder_record(
    reminder_id: str,
    guild_id: int,
    channel_id: int,
    message_id: int,
    user_token: str,
    fire_time: datetime,
    reminder_text: str,
) -> str:
    text = " ".join((reminder_text or "").split())
    body = (
        "REMINDER\n"
        f"id: {reminder_id}\n"
        f"guild: {guild_id}\n"
        f"channel: {channel_id}\n"
        f"message: {message_id}\n"
        f"user: {user_token}\n"
        f"fire: {_naive_utc(fire_time).isoformat()}\n"
        f"text: {text}\n"
    )
    return body[:1990]


def parse_reminder_record(content: str) -> dict | None:
    lines = (content or "").splitlines()
    if not lines or lines[0].strip() != "REMINDER":
        return None
    fields = {}
    for line in lines[1:]:
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        fields[key.strip()] = value.strip()
    try:
        return {
            "id": fields["id"],
            "guild_id": int(fields["guild"]),
            "channel_id": int(fields["channel"]),
            "message_id": int(fields["message"]),
            "user": fields.get("user") or "unknown",
            "fire_time": datetime.fromisoformat(fields["fire"]),
            "text": fields.get("text") or "(no message)",
        }
    except (KeyError, ValueError):
        return None


class ReminderHandler:
    def __init__(self, bot):
        self.bot = bot
        self.active_reminders = {}
        self._loaded = False

    # ---------------------------------------------------------
    # Rebuild timers from Discord
    # ---------------------------------------------------------
    async def load_persistent_reminders(self):
        if self._loaded:
            return
        self._loaded = True
        from core.discord_store import THREAD_REMINDERS, find_bot_thread

        try:
            thread = await find_bot_thread(self.bot, THREAD_REMINDERS)
        except Exception:
            return
        if thread is None:
            return

        try:
            history = thread.history(limit=200)
        except Exception:
            return

        async for message in history:
            data = parse_reminder_record(message.content or "")
            if data is None or data["id"] in self.active_reminders:
                continue
            delay = (_naive_utc(data["fire_time"]) - datetime.utcnow()).total_seconds()
            if delay < 1:
                delay = 1
            task = asyncio.create_task(self._fire_stored(data, message, delay))
            self.active_reminders[data["id"]] = task

    # ---------------------------------------------------------
    # Duration parser (1s, 1 sec, 1 second, 5m, 2h, etc.)
    # ---------------------------------------------------------
    def parse_duration(self, text: str):
        pattern = r"(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\b"
        match = re.search(pattern, text, re.IGNORECASE)

        if not match:
            return None

        amount = int(match.group(1))
        unit = match.group(2).lower()

        if unit.startswith("s"):
            return timedelta(seconds=amount)
        if unit.startswith("m"):
            return timedelta(minutes=amount)
        if unit.startswith("h"):
            return timedelta(hours=amount)
        if unit.startswith("d"):
            return timedelta(days=amount)

        return None

    # ---------------------------------------------------------
    # Create reminder
    # ---------------------------------------------------------
    async def create_reminder(self, ctx, user, time_and_message: str):

        # 1) Try duration parser first
        duration = self.parse_duration(time_and_message)
        if duration:
            fire_time = datetime.utcnow() + duration

            # Extract message after duration
            parts = time_and_message.split()
            reminder_text = " ".join(parts[2:]) if len(parts) > 2 else "(no message)"

        else:
            # 2) Fall back to dateparser (natural language times)
            if parse_date is None:
                return (
                    False,
                    "Natural-language times need the `dateparser` package. "
                    "Use a short duration like `10m` / `2h`, or install deps "
                    "(`pip install dateparser`) and rebuild.",
                )
            parsed = parse_date(time_and_message, settings={"PREFER_DATES_FROM": "future"})
            if not parsed:
                return False, "I couldn't understand the time you gave me."

            fire_time = parsed

            # Extract message after time phrase
            parts = time_and_message.split()
            reminder_text = " ".join(parts[2:]) if len(parts) > 2 else "(no message)"

        fire_time = _naive_utc(fire_time)
        delay = (fire_time - datetime.utcnow()).total_seconds()
        if delay < 1:
            delay = 1

        reminder_id = f"{ctx.message.id}-{ctx.author.id}"
        record = await self._write_reminder(
            reminder_id,
            ctx.guild.id if ctx.guild else 0,
            ctx.channel.id,
            ctx.message.id,
            _user_token(user),
            fire_time,
            reminder_text,
        )
        if record is None:
            return False, "I couldn't save that reminder in bot memory."

        task = asyncio.create_task(
            self._schedule_fire(
                reminder_id,
                ctx,
                user,
                reminder_text,
                ctx.message,
                delay,
                record,
            )
        )
        self.active_reminders[reminder_id] = task

        return True, "Reminding!"

    async def _write_reminder(
        self,
        reminder_id,
        guild_id,
        channel_id,
        message_id,
        user_token,
        fire_time,
        reminder_text,
    ):
        from core.discord_store import THREAD_REMINDERS, ensure_bot_thread

        try:
            thread = await ensure_bot_thread(self.bot, THREAD_REMINDERS)
            if thread is None:
                return None
            return await thread.send(
                format_reminder_record(
                    reminder_id,
                    guild_id,
                    channel_id,
                    message_id,
                    user_token,
                    fire_time,
                    reminder_text,
                )
            )
        except Exception:
            return None

    # ---------------------------------------------------------
    # Fire reminder
    # ---------------------------------------------------------
    async def _schedule_fire(
        self,
        reminder_id,
        ctx,
        user,
        reminder_text,
        original_message,
        delay,
        record,
    ):
        await asyncio.sleep(delay)

        channel = ctx.channel

        # Build mention
        if isinstance(user, discord.Member):
            mention = user.mention
        elif user == "@everyone":
            mention = "@everyone"
        elif user == "@here":
            mention = "@here"
        elif isinstance(user, str) and user.startswith("role:"):
            role_id = int(user.split(":")[1])
            role = ctx.guild.get_role(role_id)
            mention = role.mention if role else "@deleted-role"
        else:
            mention = "@unknown"

        from core.client import discord_chunks
        for chunk in discord_chunks(f"🔔 Reminder for {mention}: **{reminder_text}**"):
            await channel.send(chunk)

        # 2) Forward the original message exactly as-is
        try:
            await original_message.forward(channel)
        except Exception:
            await channel.send("(Could not forward original message.)")

        await self._drop_record(record)
        self.active_reminders.pop(reminder_id, None)

    async def _drop_record(self, record):
        if record is None:
            return
        try:
            await record.delete()
        except Exception:
            pass

    async def _fire_stored(self, data: dict, record, delay: float):
        await asyncio.sleep(delay)
        channel = self.bot.get_channel(data["channel_id"])
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(data["channel_id"])
            except Exception:
                channel = None
        mention = self._mention_for(data["user"], data["guild_id"])
        if channel is not None:
            from core.client import discord_chunks
            for chunk in discord_chunks(f"🔔 Reminder for {mention}: **{data['text']}**"):
                await channel.send(chunk)
            try:
                original = await channel.fetch_message(data["message_id"])
                await original.forward(channel)
            except Exception:
                await channel.send("(Could not forward original message.)")
        await self._drop_record(record)
        self.active_reminders.pop(data["id"], None)

    def _mention_for(self, token: str, guild_id: int) -> str:
        if token == "everyone":
            return "@everyone"
        if token == "here":
            return "@here"
        if token.startswith("member:"):
            return f"<@{token.split(':', 1)[1]}>"
        if token.startswith("role:"):
            guild = self.bot.get_guild(guild_id)
            try:
                role_id = int(token.split(":", 1)[1])
            except ValueError:
                return "@deleted-role"
            role = guild.get_role(role_id) if guild else None
            return role.mention if role else "@deleted-role"
        return "@unknown"
