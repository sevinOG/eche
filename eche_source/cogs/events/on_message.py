# events/on_message.py

import asyncio
import discord
from discord.ext import commands
import inspect

from core.debuglog import dprint

dprint("[on_message] Loaded from:", inspect.getfile(inspect.currentframe()))

# --- AZBOT INTERNALS ---
from core.context_manager import (
    HOME_SERVER_ID,
    remember_user,
)
from core.builder import build_prompt
from core.message_media import collect_discord_media

# --- REST-BASED GROQ CLIENT ---
from core.client import (
    call_groq_turn,
    channel_completion_tokens,
    settle_cut_reply,
)

# --- CHAT TOOLS ---
import core.tools_context  # noqa: F401  registers context_raw
import core.tools_duckduckgo  # noqa: F401  registers duckduckgo
import core.tools_thread  # noqa: F401  registers thread
import core.tools_admin  # noqa: F401  registers mute, timeout, kick, ban
import core.tools_asset  # noqa: F401  registers asset
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
from core.bot_memory import remember_bot

# --- HARD BLOCK (minors) ---
from core.blocklist import REFUSAL as BLOCK_REFUSAL, is_blocked, note as block_note
from core.guardrails import (
    BYPASS_REFUSAL,
    PART_DECLINE,
    asks_to_bypass_filter,
    coaches_bypass,
    is_refusal_text,
    part_count_request,
    part_stub,
)

# --- BOO SYSTEM ---
from core.boo_kaitar import maybe_boo


def _scope(message):
    author = getattr(message, "author", None)
    channel = getattr(message, "channel", None)
    return getattr(author, "id", None), getattr(channel, "id", None)

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


def _flat_arguments(arguments) -> str:
    """Every string in a tool call's arguments, for the block check."""
    if isinstance(arguments, dict):
        return " ".join(_flat_arguments(value) for value in arguments.values())
    if isinstance(arguments, (list, tuple)):
        return " ".join(_flat_arguments(value) for value in arguments)
    return str(arguments) if isinstance(arguments, str) else ""



COMMAND_PREFIXES = ("?",)


def is_prefix_command(bot, message) -> bool:
    """True when any line of the message starts with a registered ?command.

    Matches command names and aliases after the bot's ``?`` prefix, at the
    start of the message or of any later line (leading spaces allowed).
    Unknown words after ``?`` are left to chat.
    """
    from core.command_lines import is_command_message

    try:
        return is_command_message(bot, message)
    except Exception:
        # Fail toward the command: never let chat (and its asset tool) run
        # on a message whose ? check broke.
        content = getattr(message, "content", None) or ""
        return any(line.lstrip().startswith("?") for line in content.splitlines())

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
        from core.client import (
            call_groq_turn,
            channel_completion_tokens,
            lookup_failed,
            lookup_prompt,
        )
        follow = lookup_prompt(prompt, notes)
        try:
            turn = await call_groq_turn(
                follow,
                user_id=None,
                max_completion_tokens=channel_completion_tokens(),
                include_tool_note=False,
                job="lookup",
            )
        except Exception as exc:
            dprint(f"[on_message] lookup reply failed: {exc}")
            if lookup_failed(notes):
                return (notes or "").strip(), False
            return "I found the notes, but I couldn't answer from them.", False
        if turn.quota:
            return turn.reply, True
        if turn.cut:
            turn = await settle_cut_reply(
                turn,
                follow,
                None,
                job="lookup",
            )
            if turn.quota:
                return turn.reply, True
        spoken = (turn.reply or "").strip()
        if not spoken:
            if lookup_failed(notes):
                return (notes or "").strip(), False
            return "I couldn't turn that into an answer.", False
        return spoken, False

    async def _deliver_tool_calls(self, message: discord.Message, calls: list, prompt: str):
        """
        Run the model's tool calls. The visible line is the announcement,
        then the tool body. A lookup body is rewritten by the model first.
        Returns whether a tool ran, the episode lines, the tool names,
        the reply text that was posted, the quota flag, and one first-person
        action note per tool for eche's own memory. A quiet tool posts
        nothing here.
        """
        ctx = ToolContext(bot=self.bot, message=message)
        ran_any = False
        acts: list[str] = []
        tools_used: list[str] = []
        said: list[str] = []
        actions: list[str] = []
        seen: set[str] = set()
        lookup_quota = False
        for call in calls[:3]:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "")
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            arguments = (call or {}).get("arguments") or {}
            user_id, channel_id = _scope(message)
            # Only the image tool keeps argument checks. Text tools (lookup,
            # thread, context) are text chat and are not keyword-filtered.
            image_tool = name.casefold() == "asset"
            if image_tool and asks_to_bypass_filter(_flat_arguments(arguments)):
                block_note(f"{name} arguments bypass")
                try:
                    await _send_reply(message, BYPASS_REFUSAL)
                except Exception as e:
                    dprint(f"[on_message] refusal send failed: {e}")
                    continue
                said.append(BYPASS_REFUSAL)
                ran_any = True
                break
            if image_tool and is_blocked(_flat_arguments(arguments), user_id=user_id, channel_id=channel_id):
                # The model asked a tool for something on the block list.
                block_note(f"{name} arguments")
                try:
                    await _send_reply(message, BLOCK_REFUSAL)
                except Exception as e:
                    dprint(f"[on_message] refusal send failed: {e}")
                    continue
                said.append(BLOCK_REFUSAL)
                ran_any = True
                break
            result = await execute(name, arguments, ctx)
            if not result.ran:
                continue
            tools_used.append(name)
            spoken = result.text
            if result.for_model:
                spoken, this_quota = await self._speak_lookup(prompt, result.text)
                if this_quota or (spoken or "").strip().lower().startswith(
                    "sorry, i'm being rate limited"
                ):
                    lookup_quota = True
            if image_tool and not result.quiet and is_blocked(spoken, user_id=user_id, channel_id=channel_id):
                block_note(f"{name} reply")
                spoken = BLOCK_REFUSAL
            elif image_tool and not result.quiet and coaches_bypass(spoken):
                block_note(f"{name} reply bypass")
                spoken = BYPASS_REFUSAL
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
            if not result.quiet and (spoken or "").strip():
                said.append(spoken.strip())
            try:
                from core.memory_lines import tool_action

                actions.append(
                    tool_action(
                        name,
                        (call or {}).get("arguments") or {},
                        posted=(result.text or "") if result.quiet else "",
                    )
                )
            except Exception as exc:
                dprint(f"[on_message] tool action note failed: {type(exc).__name__}")
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
        return ran_any, acts, tools_used, said, lookup_quota, actions

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            from core.audit import bind_from_message
            bind_from_message(message)
        except Exception:
            pass

        # This bot's own posts mention it when the dealer wins a hand.
        # A reply to that post would mention it again, so ignore them here.
        # Other bots are still allowed through.
        if self.bot.user and message.author.id == self.bot.user.id:
            return

        # Ignore bang commands. discord.py dispatches those itself.
        if message.content.startswith("!"):
            return

        # A prefix command (?asset and every other registered command) is
        # handled by its command only. It never also starts a chat turn,
        # even when it is a reply to Eche or mentions it.
        if is_prefix_command(self.bot, message):
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

        try:
            from core.audit import chat as audit_chat
            audit_chat(
                kind="reply" if is_reply else "mention",
                user_id=getattr(getattr(message, "author", None), "id", None),
                guild_id=getattr(getattr(message, "guild", None), "id", None),
                channel_id=getattr(getattr(message, "channel", None), "id", None),
            )
        except Exception:
            pass

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

        # Replied-to pictures: Eche's own asset posts need no recheck; anyone
        # else's get the hardcoded image check once (cached by sha256). One
        # that does not pass is left out (fail closed), with a short note.
        if getattr(media, "reply_images", None):
            from core.reply_images import WITHHELD_NOTE, cleared_reply_parts

            try:
                parts, withheld = await cleared_reply_parts(media, message)
            except Exception as e:
                dprint(f"[on_message] reply image check failed: {type(e).__name__}")
                parts, withheld = [], True
            media.reply_parts = list(parts)
            if withheld:
                media.text = (f"{media.text}\n{WITHHELD_NOTE}" if media.text else WITHHELD_NOTE)

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
        from core.context_caps import get as context_cap

        # An attachment-only turn is remembered by its note. 700 unless the
        # Context & Memory slider sets a tighter per-message cap.
        remember_cap = context_cap("memory_message_chars") or 700
        remember_cap = min(700, remember_cap)
        if len(remembered) > remember_cap:
            remembered = remembered[: remember_cap - 1].rstrip() + "…"

        guild = self.bot.get_guild(HOME_SERVER_ID)

        # Hold this user's memory lock through the reply and the pin writes.
        # The Discord reply is sent before those writes. The fold, when it
        # runs, is the last write on this turn.
        from core.client import reset_precleared_images, set_precleared_images

        token = set_precleared_images(getattr(media, "reply_parts", None) or [])
        try:
            async with _memory_lock(message.author.id):
                await self._answer_and_remember(message, guild, cleaned, remembered, media)
        finally:
            reset_precleared_images(token)

    async def _answer_and_remember(self, message, guild, cleaned, remembered, media):
        # Hard block before any prompt is built or model is called. Nothing
        # from this message is remembered or added to the episode.
        # Text-only chat has no keyword filter; the hardcoded safety footer
        # is its only guard. A message with pictures takes the image path,
        # whose checks stay on and cannot be turned off.
        user_id, channel_id = _scope(message)
        # Only pictures in the message itself take the image path. Replied-to
        # pictures were already cleared (core/reply_images); the reply text
        # gets no text filter.
        has_images = bool(getattr(media, "images", None))
        all_images = list(getattr(media, "images", None) or []) + list(getattr(media, "reply_parts", None) or [])
        if has_images and (
            asks_to_bypass_filter(cleaned) or asks_to_bypass_filter(getattr(media, "text", "") or "")
        ):
            block_note("chat message bypass")
            try:
                await _send_reply(message, BYPASS_REFUSAL)
            except Exception as e:
                dprint(f"[on_message] refusal send failed: {e}")
            return
        if has_images and is_blocked(
            cleaned, getattr(media, "text", ""), user_id=user_id, channel_id=channel_id
        ):
            block_note("chat message")
            try:
                await _send_reply(message, BLOCK_REFUSAL)
            except Exception as e:
                dprint(f"[on_message] refusal send failed: {e}")
            return

        # Build from older memory first, so this turn appears only once,
        # under MOST RECENT MESSAGE, with its pictures.
        prompt = await build_prompt(
            self.bot,
            guild,
            message.author.id,
            message.author.name,
            cleaned,
            media_note=media.text,
            image_count=len(all_images),
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
        completion_tokens = channel_completion_tokens()
        try:
            speaker_is_owner = await author_is_owner(self.bot, message.author)
        except Exception:
            speaker_is_owner = False
        # Off: admin markdown is not loaded into the prompt, and those tools
        # are left out of the list the model receives.
        admin_on = admin_tools_enabled()
        admin_visible = bool(speaker_is_owner and admin_on)
        # Every offered tool, context_raw included. The model decides when
        # someone is asking about their own memory; there is no phrase trigger.
        offered = specs_for(speaker_is_owner=speaker_is_owner, admin_enabled=admin_on)
        turn = await call_groq_turn(
            prompt,
            user_id=message.author.id,
            max_completion_tokens=completion_tokens,
            tools=offered,
            extra_system=admin_injection(enabled=admin_visible),
            images=all_images or None,
        )

        reply = turn.reply or ""
        quota_reply = turn.quota or reply.strip().lower().startswith(
            "sorry, i'm being rate limited"
        )
        calls = [] if quota_reply else list(turn.tool_calls or [])
        names = visible_names(speaker_is_owner=speaker_is_owner, admin_enabled=admin_on)
        if not calls and not quota_reply:
            # The model typed a tool call instead of using the API.
            calls = calls_from_model_text(turn.reply, names)
        if not calls and not quota_reply and turn.cut:
            turn = await settle_cut_reply(
                turn,
                prompt,
                message.author.id,
                images=all_images or None,
            )
            reply = turn.reply or ""
            quota_reply = turn.quota or reply.strip().lower().startswith(
                "sorry, i'm being rate limited"
            )

        ran_tools = False
        episode_lines: list[str] = []
        tools_used: list[str] = []
        tool_said: list[str] = []
        tool_actions: list[str] = []
        if calls:
            try:
                (
                    ran_tools,
                    episode_lines,
                    tools_used,
                    tool_said,
                    lookup_quota,
                    tool_actions,
                ) = await self._deliver_tool_calls(
                    message, calls, prompt
                )
                if lookup_quota:
                    quota_reply = True
            except Exception as e:
                dprint(f"[on_message] tool delivery failed: {e}")
                ran_tools = False

        if has_images and not ran_tools and reply.strip() and is_blocked(
            reply, user_id=user_id, channel_id=channel_id
        ):
            # The model's own reply is checked before it is sent.
            block_note("model reply")
            reply = BLOCK_REFUSAL
        elif has_images and not ran_tools and coaches_bypass(reply):
            block_note("model reply bypass")
            reply = BYPASS_REFUSAL
        elif not ran_tools and part_stub(reply):
            block_note("multipart stub")
            reply = PART_DECLINE

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
        said_all = tool_said if ran_tools else ([reply] if reply.strip() else [])
        refused_turn = bool(said_all) and all(is_refusal_text(s) for s in said_all)
        if not quota_reply and not refused_turn:
            # The reply is already on Discord. The last three full messages
            # stay in the local buffer. Every third one updates the long-term pin.
            user_text = asked
            if user_text in ("(no text)", "(no content)"):
                user_text = "a picture" if media.images else "a message with no text"
            try:
                await remember_user(
                    self.bot,
                    guild,
                    message.author.id,
                    message.author.name,
                    user_text,
                )
            except Exception as e:
                dprint(f"[on_message] user memory store failed: {e}")
            if tools_used or (not ran_tools and reply.strip()):
                # Eche's own record of this turn: what it did and what it
                # posted. A tool turn keeps both the action and the words.
                from core.memory_lines import bot_memory_line, bot_output_record

                if ran_tools:
                    said = bot_output_record("", tool_said, tool_actions)
                else:
                    said = reply
                if not str(said or "").strip():
                    said = bot_memory_line("", tools_used)
                try:
                    await remember_bot(
                        self.bot,
                        message.author.id,
                        message.author.name,
                        said,
                    )
                except Exception as e:
                    dprint(f"[on_message] bot memory store failed: {e}")
        if not refused_turn:
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
