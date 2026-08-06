# reminders/remind_handler.py

import asyncio
import discord
import re
from datetime import datetime, timedelta

try:
    from dateparser import parse as parse_date
except ImportError:  # frozen/source missing optional dep — duration parser still works
    parse_date = None


class ReminderHandler:
    def __init__(self, bot):
        self.bot = bot
        self.active_reminders = {}
        self.persistent = {}

    # ---------------------------------------------------------
    # Persistent rebuild
    # ---------------------------------------------------------
    async def load_persistent_reminders(self):
        for reminder_id, data in self.persistent.items():
            ctx = data["ctx"]
            user = data["user"]
            fire_time = data["fire_time"]
            reminder_text = data["reminder_text"]
            original_message = data["original_message"]
            created_at = data["created_at"]

            delay = (fire_time - datetime.utcnow()).total_seconds()
            if delay < 0:
                delay = 1

            task = asyncio.create_task(
                self._schedule_fire(
                    reminder_id,
                    ctx,
                    user,
                    reminder_text,
                    original_message,
                    created_at,
                    delay
                )
            )
            self.active_reminders[reminder_id] = task

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

        delay = (fire_time - datetime.utcnow()).total_seconds()
        if delay < 1:
            delay = 1

        reminder_id = f"{ctx.message.id}-{ctx.author.id}"
        created_at = datetime.utcnow()

        # Store persistent
        self.persistent[reminder_id] = {
            "ctx": ctx,
            "user": user,
            "fire_time": fire_time,
            "reminder_text": reminder_text,
            "original_message": ctx.message,
            "created_at": created_at
        }

        # Schedule
        task = asyncio.create_task(
            self._schedule_fire(
                reminder_id,
                ctx,
                user,
                reminder_text,
                ctx.message,
                created_at,
                delay
            )
        )
        self.active_reminders[reminder_id] = task

        return True, "Reminding!"

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
        created_at,
        delay
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

        # 1) Send reminder text
        await channel.send(f"🔔 Reminder for {mention}: **{reminder_text}**")

        # 2) Forward the original message exactly as-is
        try:
            await original_message.forward(channel)
        except Exception:
            await channel.send("(Could not forward original message.)")

        # Cleanup
        self.active_reminders.pop(reminder_id, None)
        self.persistent.pop(reminder_id, None)
