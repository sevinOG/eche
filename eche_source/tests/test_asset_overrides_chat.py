"""Owner 2026-11-23 20:26 ET: ?asset overrides the chat model, and a
delivered retry logs success. Mocks only; no image is generated."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

import cogs.events.on_message as om  # noqa: E402
from core import audit as A  # noqa: E402
from core.bot import strict_prefix  # noqa: E402
from core.command_lines import command_start  # noqa: E402
from tests.test_command_lines_live_2151 import BOT_ID, _bot, _Msg  # noqa: E402

LEADING = (
    f"<@{BOT_ID}> ?asset a knight in armor",
    f"<@!{BOT_ID}>   ?asset a knight in armor",
    f"<@{BOT_ID}> <@123> ?asset a knight in armor",
    f"hey\n<@{BOT_ID}> ?asset a knight in armor",
)


class MentionThenCommand(unittest.TestCase):
    def setUp(self):
        self.bot = _bot()

    def test_a_leading_mention_does_not_hide_asset(self):
        for text in LEADING:
            self.assertTrue(om.is_prefix_command(self.bot, _Msg(text)), repr(text))
            ctx = asyncio.run(self.bot.get_context(_Msg(text)))
            self.assertIsNotNone(ctx.command, repr(text))
            self.assertEqual(ctx.command.name, "asset")
            self.assertEqual(ctx.view.read_rest().strip(), "a knight in armor")

    def test_no_chat_turn_when_asset_follows_a_mention(self):
        cog = om.OnMessage(self.bot)
        for text in LEADING:
            chat = mock.AsyncMock()
            with mock.patch.object(om, "maybe_boo", mock.AsyncMock(return_value=False)), \
                    mock.patch.object(cog, "_record_server_line", mock.AsyncMock()), \
                    mock.patch.object(cog, "_answer_and_remember", chat), \
                    mock.patch("core.audit.chat") as audit_chat:
                asyncio.run(cog.on_message(_Msg(text)))
            self.assertEqual(chat.await_count, 0, repr(text))
            audit_chat.assert_not_called()

    def test_mid_line_and_unknown_still_chat(self):
        for text in (f"<@{BOT_ID}> hey ?asset car", f"<@{BOT_ID}> ?lol", f"<@{BOT_ID}> what?"):
            self.assertFalse(om.is_prefix_command(self.bot, _Msg(text)), repr(text))
            self.assertEqual(strict_prefix(self.bot, _Msg(text)), [], repr(text))

    def test_index_points_at_the_question_mark(self):
        has = {"asset"}.__contains__
        text = f"<@{BOT_ID}> ?asset x"
        self.assertEqual(text[command_start(text, has)], "?")


class DeliveredRetryLogsSuccess(unittest.TestCase):
    def test_clear_outcome_after_an_earlier_block(self):
        from cogs.asset import _clear_outcome
        A.mark_refusal()  # the first attempt's output block
        _clear_outcome()  # the retry was posted
        rows = []
        with mock.patch.object(A, "record", side_effect=lambda ev, **kw: rows.append(kw) or kw):
            A.command("asset", "success")
        self.assertEqual(rows[-1]["outcome"], "success")


if __name__ == "__main__":
    unittest.main()
