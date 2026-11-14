# tests/test_command_lines_live_2151.py
# Live report 2026-11-13 21:51 ET (#official-usa-account-btw): a ?asset video
# request got "The hourly video limit for this user has been reached." and
# also a video. The audit log shows two ?asset command runs ~90 s apart (an
# earlier request still rendering when the second arrived); no chat-tool
# call. These tests pin the guard the owner asked for anyway: any message
# with a registered ?command at the start of any line is handled by that
# command only, and the chat model's asset tool never acts on it.
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
from core import tools_asset  # noqa: E402
from core.bot import strict_prefix  # noqa: E402
from core.command_lines import command_start  # noqa: E402
from core.tools import ToolContext  # noqa: E402

BOT_ID = 1486240665951142079
EXACT = "?asset video parody with sound of Eminem's Without Me but instead of \"Guess who's back...\" it's \"kaitar's bald...\" @eche"
PROMPT = EXACT[len("?asset "):]
VARIANTS = (
    EXACT,
    "how\n" + EXACT,
    "how\n\n" + EXACT,
    "   " + EXACT,
    "how\n  " + EXACT,
    EXACT.replace("@eche", f"<@{BOT_ID}>"),
    "how\n" + EXACT.replace("@eche", f"<@{BOT_ID}>"),
    EXACT.replace(" but instead", "\nbut instead"),
)


def _bot():
    bot = commands.Bot(command_prefix=strict_prefix, intents=discord.Intents.none(), case_insensitive=True)

    @bot.command(name="asset")
    async def asset(ctx, *, prompt: str = ""):
        return None

    bot._connection.user = SimpleNamespace(id=BOT_ID)
    return bot


class _Msg:
    def __init__(self, content, *, mention_bot=True):
        self.content = content
        self.author = SimpleNamespace(id=42, name="Sir Dumbass", bot=False)
        self.guild = SimpleNamespace(id=1)
        self.channel = SimpleNamespace(id=2)
        self.mentions = [SimpleNamespace(id=BOT_ID)] if mention_bot else []
        self.reference = None
        self.attachments = []
        self._state = None


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.bot = _bot()

    def test_every_variant_is_a_command_message(self):
        for text in VARIANTS:
            self.assertTrue(om.is_prefix_command(self.bot, _Msg(text)), repr(text))

    def test_non_commands_stay_chat(self):
        for text in ("how", "hey ?asset car", "what? asset", "how\n?lol no", "how\n? asset", "> ?asset quoted", ""):
            self.assertFalse(om.is_prefix_command(self.bot, _Msg(text)), repr(text))
            self.assertEqual(strict_prefix(self.bot, _Msg(text)), [], repr(text))

    def test_prefix_points_discord_py_at_the_command_line(self):
        for text in VARIANTS:
            ctx = asyncio.run(self.bot.get_context(_Msg(text)))
            self.assertIsNotNone(ctx.command, repr(text))
            self.assertEqual(ctx.command.name, "asset")
            rest = ctx.view.read_rest().strip()
            self.assertTrue(rest.startswith("video parody"), repr(rest))
            self.assertIn("kaitar's bald", rest)

    def test_exact_text_prompt_is_the_rest_of_the_line(self):
        ctx = asyncio.run(self.bot.get_context(_Msg("how\n" + EXACT)))
        self.assertEqual(ctx.view.read_rest().strip(), PROMPT)

    def test_command_start_index(self):
        has = {"asset"}.__contains__
        self.assertEqual(command_start(EXACT, has), 0)
        self.assertEqual(command_start("how\n" + EXACT, has), 4)
        self.assertEqual(command_start("how\n  " + EXACT, has), 6)
        self.assertIsNone(command_start("how are you?", has))


class ChatPathTests(unittest.TestCase):
    def setUp(self):
        self.bot = _bot()
        self.cog = om.OnMessage(self.bot)

    def test_no_chat_turn_for_any_variant(self):
        for text in VARIANTS:
            chat = mock.AsyncMock()
            with mock.patch.object(om, "maybe_boo", mock.AsyncMock(return_value=False)), \
                    mock.patch.object(self.cog, "_record_server_line", mock.AsyncMock()), \
                    mock.patch.object(self.cog, "_answer_and_remember", chat), \
                    mock.patch.object(om, "collect_discord_media", mock.AsyncMock(side_effect=AssertionError("chat path"))), \
                    mock.patch("core.audit.chat") as audit_chat:
                asyncio.run(self.cog.on_message(_Msg(text)))
            self.assertEqual(chat.await_count, 0, repr(text))
            audit_chat.assert_not_called()


class AssetToolTests(unittest.TestCase):
    def test_asset_tool_never_runs_for_a_command_message(self):
        bot = _bot()
        for text in VARIANTS:
            ctx = ToolContext(bot=bot, message=_Msg(text))
            with mock.patch.object(tools_asset, "refuse_speaker", side_effect=AssertionError("gate reached")), \
                    mock.patch.object(tools_asset, "run_asset", side_effect=AssertionError("asset ran")):
                result = asyncio.run(tools_asset.make_asset(ctx, {"prompt": "kaitar's bald", "kind": "video"}))
            self.assertEqual(result.text, tools_asset.COMMAND_MESSAGE_SKIPPED, repr(text))
            self.assertFalse(result.ran)

    def test_asset_tool_still_runs_gate_for_plain_chat(self):
        bot = _bot()
        ctx = ToolContext(bot=bot, message=_Msg("make a video of kaitar's bald head @eche"))
        with mock.patch.object(tools_asset, "refuse_speaker", return_value="gate said no") as gate:
            result = asyncio.run(tools_asset.make_asset(ctx, {"prompt": "kaitar", "kind": "video"}))
        gate.assert_called_once()
        self.assertEqual(result.text, "gate said no")


if __name__ == "__main__":
    unittest.main()
