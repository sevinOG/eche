# events/on_message.py

import discord
from discord.ext import commands
import inspect

from core.debuglog import dprint

dprint("[on_message] Loaded from:", inspect.getfile(inspect.currentframe()))

# --- AZBOT INTERNALS ---
from core.context_manager import update_context, HOME_SERVER_ID
from core.context_summarizer import summarize_context
from core.builder import build_prompt

# --- REST-BASED GROQ CLIENT ---
from core.client import call_groq

# --- SEVIN SELF-MEMORY ---
from core.bot_memory import log_bot_event

# --- BOO SYSTEM ---
from core.boo_kaitar import maybe_boo


class OnMessage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        dprint("[on_message] Cog initialized")

        # Override flags
        self.bot.next_reply_override = False
        self.bot.override_waiting_for = None

    async def cog_load(self):
        dprint("[on_message] Cog loaded")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):

        # Ignore bot messages
        # REMOVED BOT BLOCK

        # Ignore commands
        if message.content.startswith("!"):
            return

        # ⭐ BOO LOGIC — runs for ALL messages, even without mention ⭐
        if await maybe_boo(message):
            return

        # Detect reply or mention
        is_reply = (
            message.reference
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.bot.user.id
        )
        is_mention = self.bot.user in message.mentions

        # If user is NOT talking to Sevin, stop here
        if not (is_reply or is_mention):
            return

        # Clean message
        cleaned = (
            message.content
            .replace(f"<@{self.bot.user.id}>", "")
            .replace(f"<@!{self.bot.user.id}>", "")
            .strip()
        ) or "(no content)"

        guild = self.bot.get_guild(HOME_SERVER_ID)

        # 1. Update user context
        await update_context(self.bot, guild, message.author.id, cleaned, message.author.name)

        # 2. Summarize user context (now triggered automatically every 3rd message inside update_context, but we can also ensure state)
        # summarize_context(...)

        # 3. Build unified prompt
        prompt = await build_prompt(
            self.bot,
            guild,
            message.author.id,
            message.author.display_name,
            cleaned
        )

        # Push unified prompt + inbound chat to GUI panels
        try:
            from core.gui_bridge import unifier, chat as gui_chat
            unifier(prompt)
            gui_chat(f"{message.author.display_name}: {cleaned}")
        except Exception:
            pass

        # ---------------------------------------------------------
        # OVERRIDE LOGIC — allow 2000 chars for next reply only
        # ---------------------------------------------------------
        max_chars = 500
        if getattr(self.bot, "next_reply_override", False):
            if message.reference and message.reference.message_id == self.bot.override_waiting_for:
                max_chars = 2000

        # 4. Call Groq (REST)
        reply, thoughts = await call_groq(prompt, user_id=message.author.id)

        # Enforce character limit
        reply = reply[:max_chars]

        if not reply or not reply.strip():
            return

        # Mirror reply + thoughts into GUI panels
        try:
            from core.gui_bridge import (
                chat as gui_chat,
                subconscious,
                log as gui_log,
            )
            gui_chat(f"Bot: {reply}")
            if thoughts:
                subconscious(thoughts)
            gui_log(f"Replied to {message.author.display_name}", channel="chat")
        except Exception:
            pass

        # 5. Send reply
        await message.channel.send(reply)

        # Reset override after use
        if getattr(self.bot, "next_reply_override", False):
            self.bot.next_reply_override = False
            self.bot.override_waiting_for = None

        # 6. Log Bot's self-context (writes to Bot's memory file)
        await log_bot_event(self.bot, reply)


async def setup(bot):
    await bot.add_cog(OnMessage(bot))
