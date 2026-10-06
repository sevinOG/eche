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
    call_groq_turn,
    CHAT_COMPLETION_TOKENS,
    CHAT_LONG_COMPLETION_TOKENS,
)

# --- CHAT TOOLS ---
import core.tools_context as tools_context  # registers context_raw
import core.tools_duckduckgo  # noqa: F401  registers duckduckgo
import core.tools_admin  # noqa: F401  registers mute, timeout, kick, ban
from core.admin_tools import admin_injection, admin_tools_enabled, author_is_owner
from core.tools import (
    ToolContext,
    calls_from_model_text,
    execute,
    format_tool_messages,
    specs_for,
    visible_names,
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

    async def _speak_lookup(self, question: str, notes: str) -> tuple[str, bool]:
        """Turn lookup notes into the chat reply. The notes themselves are not sent."""
        from core.client import CHAT_COMPLETION_TOKENS, call_groq_turn, lookup_prompt
        try:
            turn = await call_groq_turn(
                lookup_prompt(question, notes),
                user_id=None,
                max_completion_tokens=CHAT_COMPLETION_TOKENS,
            )
        except Exception as exc:
            dprint(f"[on_message] lookup reply failed: {exc}")
            return "I found the notes, but I couldn't answer from them.", False
        if turn.quota:
            return turn.reply, True
        spoken = (turn.reply or "").strip()
        if not spoken:
            return "I couldn't turn that into an answer.", False
        return spoken[:500], False

    async def _deliver_tool_calls(self, message: discord.Message, calls: list, question: str) -> bool:
        """
        Run the model's tool calls. The visible line is the announcement,
        then the tool body. A lookup body is rewritten by the model first.
        Returns True when at least one tool ran.
        """
        ctx = ToolContext(bot=self.bot, message=message)
        ran_any = False
        seen: set[str] = set()
        for call in calls[:3]:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "")
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            result = await execute(name, (call or {}).get("arguments") or {}, ctx)
            if not result.ran:
                continue
            spoken = result.text
            lookup_quota = False
            if result.for_model:
                spoken, lookup_quota = await self._speak_lookup(question, result.text)
            chunks = format_tool_messages(
                result.name,
                spoken,
                fence=False if result.for_model else result.fence,
            )
            sent = False
            for index, chunk in enumerate(chunks):
                try:
                    if index == 0:
                        await message.reply(chunk)
                    else:
                        await message.channel.send(chunk)
                    sent = True
                except Exception as e:
                    dprint(f"[on_message] tool message failed: {e}")
                    break
            if not sent:
                continue
            ran_any = True
            notice = chunks[0].splitlines()[0]
            remembered = spoken if result.for_model else notice
            try:
                from core.gui_bridge import chat as gui_chat, tool_log
                from core.tools import log_detail
                gui_chat(f"Bot: {chunks[0]}")
                who = getattr(message.author, "name", None) or str(message.author.id)
                tool_log(
                    result.name,
                    who,
                    log_detail((call or {}).get("arguments") or {}, result),
                )
            except Exception:
                pass
            if not lookup_quota:
                try:
                    await log_bot_event(self.bot, remembered)
                except Exception as e:
                    dprint(f"[on_message] tool memory log failed: {e}")
        return ran_any

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
        # Tools ride on this same request. A quota miss does not run them.
        completion_tokens = (
            CHAT_LONG_COMPLETION_TOKENS if max_chars >= 2000 else CHAT_COMPLETION_TOKENS
        )
        try:
            speaker_is_owner = await author_is_owner(self.bot, message.author)
        except Exception:
            speaker_is_owner = False
        # Off: admin markdown is not loaded into the prompt, and those tools
        # are left out of the list the model receives.
        admin_on = admin_tools_enabled()
        admin_visible = bool(speaker_is_owner and admin_on)
        turn = await call_groq_turn(
            prompt,
            user_id=message.author.id,
            max_completion_tokens=completion_tokens,
            tools=specs_for(speaker_is_owner=speaker_is_owner, admin_enabled=admin_on),
            extra_system=admin_injection(enabled=admin_visible),
        )

        reply = (turn.reply or "")[:max_chars]
        quota_reply = turn.quota or reply.strip().lower().startswith(
            "sorry, i'm being rate limited"
        )
        calls = [] if quota_reply else list(turn.tool_calls or [])
        if not calls and not quota_reply:
            # The model typed a tool call or ?context_raw instead of the API.
            calls = calls_from_model_text(
                turn.reply,
                visible_names(speaker_is_owner=speaker_is_owner, admin_enabled=admin_on),
            )
        if not calls and not quota_reply and tools_context.asks_for_own_context(cleaned):
            # The ask was clear and the model answered in prose. Run the tool.
            calls = [{"name": "context_raw", "arguments": {}}]

        ran_tools = False
        if calls:
            try:
                ran_tools = await self._deliver_tool_calls(message, calls, cleaned)
            except Exception as e:
                dprint(f"[on_message] tool delivery failed: {e}")
                ran_tools = False

        if ran_tools:
            if getattr(self.bot, "next_reply_override", False):
                self.bot.next_reply_override = False
                self.bot.override_waiting_for = None
        elif reply.strip():
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
