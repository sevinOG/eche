# events/on_message.py

import discord
from discord.ext import commands
import inspect

from core.debuglog import dprint

dprint("[on_message] Loaded from:", inspect.getfile(inspect.currentframe()))

# --- AZBOT INTERNALS ---
from core.context_manager import (
    archive_user_recents_if_due,
    update_context,
    HOME_SERVER_ID,
)
from core.builder import build_prompt

# --- REST-BASED GROQ CLIENT ---
from core.client import (
    call_groq,
    CHAT_COMPLETION_TOKENS,
    CHAT_LONG_COMPLETION_TOKENS,
)

# --- SEVIN SELF-MEMORY ---
from core.bot_memory import archive_bot_recents_if_due, log_bot_event

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

        # 1. Store this message in the recent block. Summary runs after the reply.
        await update_context(self.bot, guild, message.author.id, cleaned, message.author.name)

        # 2. Build unified prompt from long-term memory plus the recent lines.
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

        # 3. Call Groq. Reserve only enough completion tokens for the reply cap.
        completion_tokens = (
            CHAT_LONG_COMPLETION_TOKENS if max_chars >= 2000 else CHAT_COMPLETION_TOKENS
        )
        reply, thoughts = await call_groq(
            prompt,
            user_id=message.author.id,
            max_completion_tokens=completion_tokens,
        )

        # Enforce character limit
        reply = (reply or "")[:max_chars]
        quota_reply = reply.strip().lower().startswith("sorry, i'm being rate limited")

        if reply.strip():
            # Mirror reply into GUI panels
            try:
                from core.gui_bridge import chat as gui_chat, log as gui_log
                gui_chat(f"Bot: {reply}")
                gui_log(f"Replied to {message.author.display_name}", channel="chat")
            except Exception:
                pass

            # 5. Send reply before any summarization.
            await message.reply(reply)

            # Reset override after use
            if getattr(self.bot, "next_reply_override", False):
                self.bot.next_reply_override = False
                self.bot.override_waiting_for = None

            # A quota miss is not a real reply. Skip memory writes so we do not
            # spend another request in the same full minute.
            if not quota_reply:
                await log_bot_event(self.bot, reply)

        if not quota_reply:
            # User and bot recents each fold on their second stored line,
            # after the Discord reply is already out.
            try:
                await archive_user_recents_if_due(
                    self.bot,
                    guild,
                    message.author.id,
                    message.author.name,
                )
            except Exception as e:
                dprint(f"[on_message] user memory archive failed: {e}")
            try:
                await archive_bot_recents_if_due(self.bot)
            except Exception as e:
                dprint(f"[on_message] bot memory archive failed: {e}")


async def setup(bot):
    await bot.add_cog(OnMessage(bot))
