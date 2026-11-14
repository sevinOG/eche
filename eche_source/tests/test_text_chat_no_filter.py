# tests/test_text_chat_no_filter.py
# Regression (2026-11-13, ~7:18 PM ET): a plain "hi" in Strictly Business got
# "Blocked (llm-input-age)". Text-only chat now has no keyword filter at all:
# the unremovable safety footer is its only guard. Stored memory, the server
# episode, history, nicknames, and the system prompt are never scanned. The
# image path keeps its checks.
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from core import blocklist as bl  # noqa: E402
from core import client as cl  # noqa: E402
from core import safety as sf  # noqa: E402
from core.builder import render_memory_prompt  # noqa: E402

FOOTER_SHA = "66e2062573080eb6f55015acabf38fc33848fbfa8d450a2821918196b464d7f1"
IMG = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]

# Shaped like the user's pasted context: adult topics only. The last two
# phrases tripped the old keyword scan of memory ("age" and "context").
USER_SUMMARY = (
    "archwigger keeps asking for an adult photorealistic ogress video series, "
    "Fiona from Shrek in ogre form. Joked about JD Vance stuck in a tree and "
    "wrote firefighter dialogue. Talks about South Carolina, New Yorkers, a weed "
    "pen, and people who never use blinkers. Planning a two-year-old cartoon "
    "village rewrite with generational lore."
)
BOT_SUMMARY = "Played along with the ogress bit. Described a young woman: a girl-next-door ogre look."
EPISODE = (
    "Strictly Business: two-year arc about a gender transition transformation, a "
    "furry Jar of Dreams gag, Fiona from Shrek, a story playable as a game, and a "
    "Beekeeper's hat. tech whore kept the jokes going."
)


def _prompt(latest_text: str = "hi", speaker: str = "archwigger") -> str:
    return render_memory_prompt(
        "Friday, November 13, 2026",
        USER_SUMMARY,
        ["tech whore: lol", "tech whore: whats ur problem dude"],
        BOT_SUMMARY,
        ["Nope. Not going there."],
        EPISODE,
        f"=== MOST RECENT MESSAGE ===\n{speaker}:\n{latest_text}",
    )


def run(coro):
    return asyncio.run(coro)


class TextChatNoFilterTests(unittest.TestCase):
    def setUp(self):
        bl.clear_recent_blocks()
        self.sent = []

        def fake_post(url, payload, headers, timeout=45):
            self.sent.append(payload)
            return {"choices": [{"message": {"content": "hey"}, "finish_reason": "stop"}]}

        self._patches = [
            mock.patch.object(cl, "_provider_backend", return_value="ollama"),
            mock.patch.object(cl, "_api_key", return_value="ollama"),
            mock.patch.object(cl, "_model", return_value="test-model"),
            mock.patch.object(cl, "_sync_post", side_effect=fake_post),
            mock.patch.object(cl, "_async_post", side_effect=lambda *a, **k: fake_post(*a, **k)),
            mock.patch("core.classifier.classify_async", new=mock.AsyncMock(return_value="")),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in self._patches:
            patch.stop()
        bl.clear_recent_blocks()

    def test_old_scan_would_have_matched_this_memory(self):
        # Guard the regression: this prompt really does trip the matcher.
        self.assertTrue(bl.match(USER_SUMMARY) or bl.match(BOT_SUMMARY))

    def test_hi_with_this_context_and_episode_is_answered(self):
        with mock.patch("core.blocklist.note") as note:
            turn = run(cl.call_groq_turn(_prompt("hi"), job="channel"))
        self.assertEqual(turn.reply, "hey")
        self.assertTrue(self.sent)
        note.assert_not_called()

    def test_nickname_as_speaker_is_answered(self):
        turn = run(cl.call_groq_turn(_prompt("Whats ur problem dude", speaker="tech whore"), job="channel"))
        self.assertEqual(turn.reply, "hey")
        self.assertTrue(self.sent)

    def test_memory_and_episode_are_sent_unchanged_with_footer_first_and_last(self):
        prompt = _prompt("hi")
        run(cl.call_groq_turn(prompt, job="channel"))
        messages = self.sent[-1]["messages"]
        self.assertEqual(messages[0]["content"], sf.SAFETY_FOOTER)
        self.assertEqual(messages[-1]["content"], sf.SAFETY_FOOTER)
        self.assertEqual(sf.footer_sha256(), FOOTER_SHA)
        blob = "\n".join(str(m.get("content")) for m in messages)
        self.assertIn("two-year-old cartoon", blob)
        self.assertIn("Beekeeper's hat", blob)

    def test_text_reply_is_not_keyword_filtered(self):
        def reply_post(url, payload, headers, timeout=45):
            self.sent.append(payload)
            return {"choices": [{"message": {"content": "Fiona, two-year-old meme, kids these days."},
                                 "finish_reason": "stop"}]}

        with mock.patch.object(cl, "_async_post", side_effect=lambda *a, **k: reply_post(*a, **k)), \
                mock.patch.object(cl, "_sync_post", side_effect=reply_post):
            turn = run(cl.call_groq_turn(_prompt("hi"), job="channel"))
        self.assertIn("kids these days", turn.reply)

    def test_text_path_never_calls_a_model_check(self):
        with mock.patch("core.classifier.classify_async", new=mock.AsyncMock(return_value="unsafe")) as check:
            turn = run(cl.call_groq_turn(_prompt("hi"), job="channel"))
        check.assert_not_called()
        self.assertEqual(turn.reply, "hey")

    def test_image_path_still_blocks_minors(self):
        turn = run(cl.call_groq_turn(_prompt("draw her as a 12 year old"), images=IMG, job="channel"))
        self.assertEqual(turn.reply, bl.REFUSAL)
        self.assertFalse(self.sent)

    def test_image_path_model_check_still_fails_closed(self):
        with mock.patch("core.classifier.classify_async", new=mock.AsyncMock(return_value="unsafe")):
            turn = run(cl.call_groq_turn("look at this", images=IMG))
        self.assertEqual(turn.reply, bl.REFUSAL)
        self.assertFalse(self.sent)


class OnMessageTextPathTests(unittest.TestCase):
    def setUp(self):
        from cogs.events import on_message as om
        from core.client import ModelTurn

        self.om = om
        self.ModelTurn = ModelTurn
        self.cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=1)))
        self.sent: list[str] = []

        async def fake_send(message, content):
            self.sent.append(content)

        self.patches = [
            mock.patch.object(om, "_send_reply", fake_send),
            mock.patch.object(om, "build_prompt", mock.AsyncMock(return_value=_prompt("hi"))),
            mock.patch.object(om, "author_is_owner", mock.AsyncMock(return_value=False)),
            mock.patch.object(om, "admin_tools_enabled", lambda: False),
            mock.patch.object(om, "specs_for", lambda **_kw: []),
            mock.patch.object(om, "visible_names", lambda **_kw: set()),
            mock.patch.object(om, "remember_user", mock.AsyncMock()),
            mock.patch.object(om, "remember_bot", mock.AsyncMock()),
            mock.patch.object(self.cog, "_record_server_line", mock.AsyncMock()),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def _message(self, text):
        return SimpleNamespace(
            content=text,
            author=SimpleNamespace(id=284007193181945857, name="archwigger", display_name="tech whore"),
            channel=SimpleNamespace(id=1083466447734452308, send=mock.AsyncMock()),
            guild=SimpleNamespace(id=1083466447164018738, name="Strictly Business"),
            attachments=[],
            to_reference=lambda **_kw: None,
        )

    def test_hi_is_answered(self):
        model = mock.AsyncMock(return_value=self.ModelTurn(reply="hey", thoughts=""))
        media = SimpleNamespace(text="", images=[], summary="", memory="")
        with mock.patch.object(self.om, "call_groq_turn", model):
            run(self.cog._answer_and_remember(self._message("hi"), None, "hi", "hi", media))
        model.assert_called_once()
        self.assertEqual(self.sent, ["hey"])


if __name__ == "__main__":
    unittest.main()
