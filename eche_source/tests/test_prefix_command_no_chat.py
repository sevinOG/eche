# tests/test_prefix_command_no_chat.py
# Live bug 2026-11-13 20:43 ET: "?asset ..." sent as a reply to Eche posted
# the asset AND a chat reply, because the chat listener skipped only "!"
# messages. A prefix command must never also start a chat turn.
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

import discord  # noqa: E402
from discord.ext import commands  # noqa: E402

import cogs.events.on_message as om  # noqa: E402
from core import prompt_text  # noqa: E402

BOT_ID = 1486240665951142079


def _real_bot():
    bot = commands.Bot(command_prefix="?", intents=discord.Intents.none(), case_insensitive=True)

    @bot.command(name="asset")
    async def asset(ctx, *, prompt: str = ""):
        return None

    @bot.command(name="mem", aliases=["memory"])
    async def mem(ctx):
        return None

    return bot


class _Msg:
    def __init__(self, content, *, author_id=42, reply_to_bot=False, mention_bot=False):
        self.content = content
        self.author = SimpleNamespace(id=author_id, name="Sir Dumbass", bot=False)
        self.guild = SimpleNamespace(id=1)
        self.channel = SimpleNamespace(id=2)
        me = SimpleNamespace(id=BOT_ID)
        self.mentions = [me] if mention_bot else []
        if reply_to_bot:
            resolved = mock.Mock(spec=discord.Message)
            resolved.author = me
            self.reference = SimpleNamespace(resolved=resolved, message_id=9)
        else:
            self.reference = None


class IsPrefixCommandTests(unittest.TestCase):
    def setUp(self):
        self.bot = _real_bot()

    def test_registered_commands_and_aliases_match(self):
        for text in ("?asset bigger wheels trans flag wrap", "?ASSET car", "?asset", "?memory", "?mem x"):
            self.assertTrue(om.is_prefix_command(self.bot, SimpleNamespace(content=text)), text)

    def test_other_text_is_not_a_command(self):
        for text in ("?", "? asset car", "?lol what", "asset car", "hey ?asset", "!asset", "", None):
            self.assertFalse(om.is_prefix_command(self.bot, SimpleNamespace(content=text)), text)

    def test_a_bot_without_commands_never_matches(self):
        self.assertFalse(om.is_prefix_command(SimpleNamespace(), SimpleNamespace(content="?asset car")))


class ListenerTests(unittest.TestCase):
    def setUp(self):
        self.bot = _real_bot()
        self.bot._connection.user = SimpleNamespace(id=BOT_ID)  # what bot.user reads
        self.cog = om.OnMessage(self.bot)

    def _run(self, msg):
        chat = mock.AsyncMock()
        with mock.patch.object(om, "maybe_boo", mock.AsyncMock(return_value=False)), \
                mock.patch.object(self.cog, "_record_server_line", mock.AsyncMock()), \
                mock.patch.object(self.cog, "_answer_and_remember", chat), \
                mock.patch.object(om, "collect_discord_media", mock.AsyncMock(side_effect=AssertionError("chat path"))), \
                mock.patch("core.audit.chat") as audit_chat:
            try:
                asyncio.run(self.cog.on_message(msg))
            except AssertionError:
                return True, audit_chat
        return chat.await_count > 0, audit_chat

    def test_asset_reply_to_eche_starts_no_chat_turn(self):
        started, audit_chat = self._run(_Msg("?asset bigger wheels trans flag wrap very wide mirrors", reply_to_bot=True))
        self.assertFalse(started)
        audit_chat.assert_not_called()

    def test_filtered_asset_reply_gets_no_second_chat_refusal(self):
        # 20:45 ET: "?asset give her a bulge" was filtered by ?asset and
        # then ALSO got "Nope. Not going there." from the chat path.
        started, audit_chat = self._run(_Msg("?asset give her a bulge", reply_to_bot=True))
        self.assertFalse(started)
        audit_chat.assert_not_called()

    def test_command_that_mentions_eche_starts_no_chat_turn(self):
        started, _ = self._run(_Msg(f"?asset a red car <@{BOT_ID}>", mention_bot=True))
        self.assertFalse(started)

    def test_eches_own_asset_post_starts_no_chat_turn(self):
        started, audit_chat = self._run(_Msg("*asset*!\nbigger wheels", author_id=BOT_ID, mention_bot=True))
        self.assertFalse(started)
        audit_chat.assert_not_called()

    def test_plain_reply_to_eche_still_chats(self):
        started, audit_chat = self._run(_Msg("nice wheels", reply_to_bot=True))
        self.assertTrue(started)
        audit_chat.assert_called_once()

    def test_unknown_question_mark_word_still_chats(self):
        started, _ = self._run(_Msg("?huh what was that", reply_to_bot=True))
        self.assertTrue(started)


class PromptTests(unittest.TestCase):
    def test_pictures_on_line_points_to_asset_command_not_outage(self):
        line = prompt_text._PICTURES_ON
        self.assertIn("?asset", line)
        # 22:16 ET: the model said the tool "isn't live this turn". The line
        # must never suggest that; it tells the model to call the tool.
        for bad in ("not live", "isn't live", "not offered", "this turn", "down, off"):
            self.assertNotIn(bad, line.casefold())
        self.assertIn("call the asset tool", line)
