# events/on_message.py

import asyncio
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
from core.message_media import collect_discord_media

# --- REST-BASED GROQ CLIENT ---
from core.client import (
    call_groq_turn,
    CHAT_COMPLETION_TOKENS,
    settle_cut_reply,
)

# --- CHAT TOOLS ---
import core.tools_context as tools_context  # registers context_raw
import core.tools_duckduckgo  # noqa: F401  registers duckduckgo
import core.tools_thread  # noqa: F401  registers thread
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

# One user's pin is read, then written, then maybe folded. A second message
# from that user waits so it cannot append lines after the fold.
_memory_locks: dict[int, asyncio.Lock] = {}


def _memory_lock(user_id: int) -> asyncio.Lock:
    lock = _memory_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _memory_locks[user_id] = lock
    return lock


async def _send_reply(message, content: str):
    """Reply to the Discord message. Longer text continues in following messages."""
    from core.client import discord_chunks

    chunks = discord_chunks(content)
    if not chunks:
        return None
    sent = None
    for index, chunk in enumerate(chunks):
        if index == 0:
            try:
                sent = await message.channel.send(
                    chunk,
                    reference=message.to_reference(fail_if_not_exists=False),
                )
            except discord.HTTPException as exc:
                missing = getattr(exc, "code", None) == 50035 or "unknown message" in str(exc).lower()
                if not missing:
                    raise
                dprint(f"[on_message] reply had no message to reference: {exc}")
                sent = await message.channel.send(chunk)
        else:
            sent = await message.channel.send(chunk)
    return sent


class OnMessage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        dprint("[on_message] Cog initialized")

        # Override flags
        self.bot.next_reply_override = False
        self.bot.override_waiting_for = None

    async def cog_load(self):
        dprint("[on_message] Cog loaded")

    async def _speak_lookup(self, prompt: str, notes: str) -> tuple[str, bool]:
        """Answer the same turn from the lookup notes. The notes themselves are not sent."""
        from core.client import CHAT_COMPLETION_TOKENS, call_groq_turn, lookup_prompt
        follow = lookup_prompt(prompt, notes)
        try:
            turn = await call_groq_turn(
                follow,
                user_id=None,
                max_completion_tokens=CHAT_COMPLETION_TOKENS,
                include_tool_note=False,
            )
        except Exception as exc:
            dprint(f"[on_message] lookup reply failed: {exc}")
            return "I found the notes, but I couldn't answer from them.", False
        if turn.quota:
            return turn.reply, True
        if turn.cut:
            turn = await settle_cut_reply(
                turn,
                follow,
                None,
            )
            if turn.quota:
                return turn.reply, True
        spoken = (turn.reply or "").strip()
        if not spoken:
            return "I couldn't turn that into an answer.", False
        return spoken, False

    async def _deliver_tool_calls(self, message: discord.Message, calls: list, prompt: str):
        """
        Run the model's tool calls. The visible line is the announcement,
        then the tool body. A lookup body is rewritten by the model first.
        Returns whether a tool ran, the episode lines, and the tool names.
        """
        ctx = ToolContext(bot=self.bot, message=message)
        ran_any = False
        acts: list[str] = []
        tools_used: list[str] = []
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
            tools_used.append(name)
            spoken = result.text
            lookup_quota = False
            if result.for_model:
                spoken, lookup_quota = await self._speak_lookup(prompt, result.text)
            # A quiet tool already posted everything, including its announcement.
            shown = "" if result.quiet else spoken
            chunks = format_tool_messages(
                result.name,
                shown,
                fence=False if result.for_model else result.fence,
            )
            sent = result.quiet
            if not result.quiet:
                for index, chunk in enumerate(chunks):
                    try:
                        if index == 0:
                            await _send_reply(message, chunk)
                        else:
                            await message.channel.send(chunk)
                        sent = True
                    except Exception as e:
                        dprint(f"[on_message] tool message failed: {e}")
                        break
            if not sent:
                continue
            ran_any = True
            who = getattr(message.author, "name", None) or str(message.author.id)
            try:
                from core.gui_bridge import tool_log
                from core.tools import log_detail
                tool_log(
                    result.name,
                    who,
                    log_detail((call or {}).get("arguments") or {}, result),
                )
            except Exception:
                pass
            try:
                from core.gui_bridge import chat as gui_chat
                gui_chat(f"Bot: {chunks[0]}")
            except Exception:
                pass
            from core.episodic import act_line
            from core.today import today_day
            acts.append(
                act_line(
                    result.name,
                    (call or {}).get("arguments") or {},
                    today_day(),
                )
            )
        return ran_any, acts, tools_used

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):

        # This bot's own posts mention it when the dealer wins a hand.
        # A reply to that post would mention it again, so ignore them here.
        # Other bots are still allowed through.
        if self.bot.user and message.author.id == self.bot.user.id:
            return

        # Ignore commands
        if message.content.startswith("!"):
            return

        # ⭐ BOO LOGIC — runs for ALL messages, even without mention ⭐
        if await maybe_boo(message):
            await self._record_server_line(message, (message.content or "").strip())
            return

        # Detect reply or mention
        is_reply = (
            message.reference
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.bot.user.id
        )
        is_mention = self.bot.user in message.mentions

        # Messages that are not for Eche still belong in this server's episode.
        if not (is_reply or is_mention):
            await self._record_server_line(message, (message.content or "").strip())
            return

        # Link embeds show up just after the create event. Attachments and
        # forwards are already on the message.
        try:
            media = await collect_discord_media(message)
        except Exception as e:
            dprint(f"[on_message] media read failed: {e}")
            from core.message_media import DiscordMedia
            media = DiscordMedia(message=message)
        if media.message is not None:
            message = media.message

        # Clean message
        cleaned = (
            message.content
            .replace(f"<@{self.bot.user.id}>", "")
            .replace(f"<@!{self.bot.user.id}>", "")
            .strip()
        )
        if not cleaned:
            cleaned = "(no text)" if media.text else "(no content)"
        remembered = cleaned if not media.memory else f"{cleaned}\n{media.memory}"
        if len(remembered) > 700:
            remembered = remembered[:699].rstrip() + "…"

        guild = self.bot.get_guild(HOME_SERVER_ID)

        # Hold this user's memory lock through the reply and the pin writes.
        # The Discord reply is sent before those writes. The fold, when it
        # runs, is the last write on this turn.
        async with _memory_lock(message.author.id):
            await self._answer_and_remember(message, guild, cleaned, remembered, media)

    async def _answer_and_remember(self, message, guild, cleaned, remembered, media):
        # Build from older memory first, so this turn appears only once,
        # under MOST RECENT MESSAGE, with its pictures.
        prompt = await build_prompt(
            self.bot,
            guild,
            message.author.id,
            message.author.name,
            cleaned,
            media_note=media.text,
            image_count=len(media.images),
            server_id=getattr(message.guild, "id", None),
        )

        # The chat pane is the conversation. The prompt stays off that pane.
        try:
            from core.gui_bridge import chat as gui_chat
            inbound = cleaned if not media.summary else f"{cleaned} [media: {media.summary}]"
            gui_chat(f"{message.author.display_name}: {inbound}")
        except Exception:
            pass

        # 3. Call the model. The token cap is the reply length.
        # Tools ride on this same request. A quota miss does not run them.
        completion_tokens = CHAT_COMPLETION_TOKENS
        try:
            speaker_is_owner = await author_is_owner(self.bot, message.author)
        except Exception:
            speaker_is_owner = False
        # Off: admin markdown is not loaded into the prompt, and those tools
        # are left out of the list the model receives.
        admin_on = admin_tools_enabled()
        admin_visible = bool(speaker_is_owner and admin_on)
        # context_raw is offered only when this message asks to see it.
        # Otherwise the model dumps the pin on "what does that mean".
        asked_context = tools_context.asks_for_own_context(cleaned)
        offered = specs_for(speaker_is_owner=speaker_is_owner, admin_enabled=admin_on)
        if not asked_context:
            offered = [
                spec
                for spec in offered
                if (spec.get("function") or {}).get("name") != "context_raw"
            ]
        turn = await call_groq_turn(
            prompt,
            user_id=message.author.id,
            max_completion_tokens=completion_tokens,
            tools=offered,
            extra_system=admin_injection(enabled=admin_visible),
            images=media.images or None,
        )

        reply = turn.reply or ""
        quota_reply = turn.quota or reply.strip().lower().startswith(
            "sorry, i'm being rate limited"
        )
        calls = [] if quota_reply else list(turn.tool_calls or [])
        names = visible_names(speaker_is_owner=speaker_is_owner, admin_enabled=admin_on)
        if not asked_context:
            names = {name for name in names if name.casefold() != "context_raw"}
        typed_calls = False
        if not calls and not quota_reply:
            # The model typed a tool call or ?context_raw instead of the API.
            calls = calls_from_model_text(turn.reply, names)
            typed_calls = bool(calls)
        if not calls and not quota_reply and asked_context:
            # The ask was clear and the model answered in prose. Run the tool.
            calls = [{"name": "context_raw", "arguments": {}}]
        dropped_context = False
        if not asked_context and calls:
            kept = []
            for call in calls:
                if isinstance(call, dict) and str(call.get("name") or "").casefold() == "context_raw":
                    dropped_context = True
                    continue
                kept.append(call)
            calls = kept
        if dropped_context and not calls and not quota_reply:
            prose = (reply or "").strip()
            if typed_calls or not prose:
                try:
                    turn = await call_groq_turn(
                        prompt,
                        user_id=message.author.id,
                        max_completion_tokens=completion_tokens,
                        tools=None,
                        include_tool_note=False,
                        extra_system=admin_injection(enabled=admin_visible),
                        images=media.images or None,
                    )
                except Exception as exc:
                    dprint(f"[on_message] context repair failed: {exc}")
                else:
                    reply = turn.reply or ""
                    quota_reply = turn.quota or reply.strip().lower().startswith(
                        "sorry, i'm being rate limited"
                    )
                    calls = []
        if not calls and not quota_reply and turn.cut:
            turn = await settle_cut_reply(
                turn,
                prompt,
                message.author.id,
                images=media.images or None,
            )
            reply = turn.reply or ""
            quota_reply = turn.quota or reply.strip().lower().startswith(
                "sorry, i'm being rate limited"
            )

        ran_tools = False
        episode_lines: list[str] = []
        tools_used: list[str] = []
        if calls:
            try:
                ran_tools, episode_lines, tools_used = await self._deliver_tool_calls(
                    message, calls, prompt
                )
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

            # 5. Send reply before any memory write.
            await _send_reply(message, reply)

            # Reset override after use
            if getattr(self.bot, "next_reply_override", False):
                self.bot.next_reply_override = False
                self.bot.override_waiting_for = None

        asked = cleaned
        note = " ".join((remembered or "").split())
        if asked in ("(no text)", "(no content)") and note not in ("", "(no text)", "(no content)"):
            asked = note
        if not quota_reply:
            # Pin writes start only after the Discord reply is out. New: gets
            # one short line, not the raw turn. Each pin folds on its third
            # stored line. That fold is the last edit. A failed fold leaves
            # the lines.
            from core.memory_lines import bot_memory_line, user_memory_line

            try:
                await update_context(
                    self.bot,
                    guild,
                    message.author.id,
                    user_memory_line(asked, picture=bool(media.images)),
                    message.author.name,
                )
            except Exception as e:
                dprint(f"[on_message] user memory store failed: {e}")
            if tools_used or (not ran_tools and reply.strip()):
                try:
                    await log_bot_event(
                        self.bot,
                        message.author.id,
                        bot_memory_line(asked, tools_used),
                        message.author.name,
                    )
                except Exception as e:
                    dprint(f"[on_message] bot memory store failed: {e}")
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
                await archive_bot_recents_if_due(
                    self.bot,
                    message.author.id,
                    message.author.name,
                )
            except Exception as e:
                dprint(f"[on_message] bot memory archive failed: {e}")
        await self._record_server_line(message, asked, episode_lines)

    async def _record_server_line(self, message, text: str, tool_lines: list[str] | None = None) -> None:
        """One episode line for this server message, plus any tool that ran."""
        server_id = getattr(getattr(message, "guild", None), "id", None)
        if not server_id:
            return
        body = " ".join((text or "").split())
        if not body and not tool_lines and not getattr(message, "attachments", None):
            return
        try:
            from core.episodic import append_episodic, turn_lines
            from core.today import today_day

            lines = turn_lines(body or "(attachment)", today_day(), tool_lines or [])
            if lines:
                await append_episodic(
                    self.bot,
                    server_id,
                    getattr(message.guild, "name", None),
                    lines,
                )
        except Exception as e:
            print(f"[episodic] update failed: {e}", flush=True)


async def setup(bot):
    await bot.add_cog(OnMessage(bot))
