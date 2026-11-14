# tests/test_declined_asset_reply.py
# Live 2026-11-14 08:33 ET: right after a fresh Run (cogs.asset loaded, 19/19,
# BOT READY), the chat model called the asset tool on a reply with no request
# wording. The intent gate declined (reason no-request-wording) and the turn
# was re-asked without the asset tool and without saying why, so the model
# told the user the asset tool "isn't loaded". The re-ask now carries a note
# that the tool is loaded and did not run for this message, and the declined
# audit row keeps a short excerpt of the message. Mocks only; no media.
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

from core import tools as T  # noqa: E402
from core import tools_asset  # noqa: E402

BOT_ID = 1486240665951142079


def _msg(text):
    return SimpleNamespace(
        id=601,
        content=text,
        author=SimpleNamespace(id=284007193181945857, name="archwigger", display_name="tech whore", bot=False),
        guild=SimpleNamespace(id=9),
        channel=SimpleNamespace(id=2),
        reference=None,
        attachments=[],
        embeds=[],
        stickers=[],
        message_snapshots=[],
        mentions=[SimpleNamespace(id=BOT_ID)],
    )


class DeclineNoteTests(unittest.TestCase):
    def test_note_says_loaded_and_forbids_not_loaded_wording(self):
        note = tools_asset.declined_turn_system("")
        self.assertIn("asset tool is loaded and working", note)
        self.assertIn("Do not say the asset tool is unavailable", note)
        self.assertIn("not loaded", note)

    def test_admin_text_is_kept_first(self):
        note = tools_asset.declined_turn_system("ADMIN FILE")
        self.assertTrue(note.startswith("ADMIN FILE\n\n"))
        self.assertTrue(note.endswith(tools_asset.DECLINED_MODEL_NOTE))


class DeclinedReAskTests(unittest.TestCase):
    def test_reask_explains_the_decline_and_drops_only_the_asset_tool(self):
        from cogs.events import on_message as om
        from core.client import ModelTurn

        msg = _msg("lmao ok how about now")
        media = SimpleNamespace(text="", memory="", summary="", images=[], reply_parts=[],
                                reply_images=[], message=msg)
        cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=BOT_ID)))
        sent: list[str] = []
        rows: list[tuple[str, dict]] = []

        async def fake_send(message, content):
            sent.append(content)

        first = ModelTurn(reply="", thoughts="", tool_calls=[
            {"name": "asset", "arguments": {"prompt": "an adult woman pilot by a jet", "kind": "image"}}])
        second = ModelTurn(reply="say what you want me to make", thoughts="")
        model = mock.AsyncMock(side_effect=[first, second])
        offered = [{"type": "function", "function": {"name": "asset"}},
                   {"type": "function", "function": {"name": "duckduckgo"}}]
        run = mock.AsyncMock(side_effect=AssertionError("declined calls never generate"))
        with mock.patch.object(om, "_send_reply", fake_send), \
                mock.patch.object(om, "build_prompt", mock.AsyncMock(return_value="prompt")), \
                mock.patch.object(om, "author_is_owner", mock.AsyncMock(return_value=False)), \
                mock.patch.object(om, "admin_tools_enabled", lambda: False), \
                mock.patch.object(om, "specs_for", lambda **_kw: offered), \
                mock.patch.object(om, "visible_names", lambda **_kw: {"asset", "duckduckgo"}), \
                mock.patch.object(om, "remember_user", mock.AsyncMock()), \
                mock.patch.object(om, "remember_bot", mock.AsyncMock()), \
                mock.patch.object(cog, "_record_server_line", mock.AsyncMock()), \
                mock.patch.object(T, "default_tools_enabled", return_value=True), \
                mock.patch.object(T, "tool_prompt_enabled", return_value=True), \
                mock.patch.object(tools_asset, "run_asset", run), \
                mock.patch("core.audit.record", side_effect=lambda event, **kw: rows.append((event, kw)) or {}), \
                mock.patch.object(om, "call_groq_turn", model):
            asyncio.run(cog._answer_and_remember(msg, None, msg.content, msg.content, media))

        run.assert_not_awaited()
        self.assertEqual(model.await_count, 2)
        reask = model.await_args_list[1].kwargs
        self.assertIn(tools_asset.DECLINED_MODEL_NOTE, reask.get("extra_system") or "")
        names = {t["function"]["name"] for t in (reask.get("tools") or [])}
        self.assertEqual(names, {"duckduckgo"})
        self.assertEqual(sent, ["say what you want me to make"])
        declined = [kw for e, kw in rows if e == "asset-request" and kw.get("outcome") == "declined"]
        self.assertEqual(declined[-1]["reason"], "no-request-wording")
        self.assertEqual(declined[-1]["message"], "lmao ok how about now")


if __name__ == "__main__":
    unittest.main()
