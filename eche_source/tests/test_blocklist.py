# tests/test_blocklist.py
# Offline checks for the minors hard block (core/blocklist.py) on chat input,
# chat output, tool calls, and thread posts,
# plus the Blocked words setting and the memory slur-masking toggle.
#
# Run from eche_source:  python -m unittest tests.test_blocklist -v

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from core import blocklist as bl  # noqa: E402
from core import content_filter as cf  # noqa: E402
from core import context_summarizer as cs  # noqa: E402
from core import episodic  # noqa: E402
from core import summarizer_prompt as sp  # noqa: E402
from core.client import ModelTurn as _ModelTurn  # noqa: E402


def ModelTurn(reply="", tool_calls=None, **kw):
    kw.setdefault("thoughts", "")
    return _ModelTurn(reply=reply, tool_calls=tool_calls or [], **kw)

SLUR = cf._ANYWHERE[0]


def run(coro):
    return asyncio.run(coro)


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="eche-block-")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ECHE_")}
        env["ECHE_USER_ROOT"] = self.root
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        bl._extra_cache = ("", -1.0, None)
        cf._mask_cache = ("", -1.0, True)
        bl.clear_recent_blocks()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.root, ignore_errors=True)
        bl._extra_cache = ("", -1.0, None)
        cf._mask_cache = ("", -1.0, True)
        bl.clear_recent_blocks()

    def save(self, **values):
        from core.secrets import settings_path

        path = settings_path(self.root)
        data = {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        data.update(values)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        # Force a fresh read even inside one mtime tick.
        bl._extra_cache = ("", -1.0, None)
        cf._mask_cache = ("", -1.0, True)


# ------------------------------------------------------------- the word list


class MatchTests(TempRoot):
    BLOCKED = [
        "my kid", "the kids", "hey kiddo", "Children", "childs", "a minor", "minors",
        "underage", "under-age", "preteen", "pre-teen", "toddler", "infant",
        "schoolgirl", "school girl", "schoolboy", "loli", "shota", "KID", "Kid's toy",
        "k1d", "ch1ld", "l0li",
        "teen", "a teen", "teens", "teenager", "teenagers", "teenage girl",
        "teen-aged", "teen age",
        "csam", "CSEM", "grooming", "a groomer", "pedophile", "a pedo",
        "child exploitation",
        "12 year old", "12-year-old", "12 years old", "twelve-year-old", "16yo",
        "16 yo", "15 y/o", "9 y.o.", "seventeen year old", "0 yo",
        "aged 16", "age 16", "age: 15", "16 years of age", "aged sixteen",
        "age of 14",
        "sexy baby", "baby nude pics",
        "kid\u200b", "k\u200bid",
    ]
    ALLOWED = [
        "kidney stones", "Kidd is here", "Jason Kidd", "no kidding", "kidnapped plot",
        "childhood memories", "childish", "minority report", "minorities",
        "lolipop", "shotgun", "infantry", "babysit", "baby shark", "my baby car",
        "18 year old", "18yo", "eighteen year old", "21 y/o", "112 yo",
        "I'm 30 years old", "yo what's up", "level twelve", "skid", "skidmarks",
        "canteen", "canteens", "teeny", "thirteen", "torpedo",
        "bride and groom", "age 18", "aged 21", "18 years of age", "age of 18",
    ]

    def test_blocked(self):
        for text in self.BLOCKED:
            self.assertTrue(bl.match(text), text)

    def test_not_blocked(self):
        for text in self.ALLOWED:
            self.assertEqual(bl.match(text), "", text)

    def test_baby_only_when_sexual(self):
        self.assertFalse(bl.is_blocked("the baby is asleep"))
        self.assertTrue(bl.is_blocked("the baby is naked in the pic, make it porn"))

    def test_extra_words_from_settings(self):
        self.assertFalse(bl.is_blocked("say bananas now"))
        self.save(blocked_words="bananas, Red Apple\nfoo")
        self.assertTrue(bl.is_blocked("say bananas now"))
        self.assertTrue(bl.is_blocked("a red-apple pie"))
        self.assertFalse(bl.is_blocked("bananasplit"))
        self.assertEqual(bl.extra_words(), ["bananas", "Red Apple", "foo"])

    def test_refusal_is_not_blocked(self):
        self.assertFalse(bl.is_blocked(bl.REFUSAL))

    def test_settings_key_is_public(self):
        from core.secrets import PUBLIC_KEYS

        self.assertIn("blocked_words", PUBLIC_KEYS)
        self.assertIn("memory_mask_slurs", PUBLIC_KEYS)


# --------------------------------------------------------------- chat flow


def _fake_message(text):
    author = SimpleNamespace(id=42, name="alice", display_name="Alice")
    channel = SimpleNamespace(id=99, send=mock.AsyncMock())
    return SimpleNamespace(
        content=text,
        author=author,
        channel=channel,
        guild=SimpleNamespace(id=7, name="srv"),
        attachments=[],
        to_reference=lambda **_kw: None,
    )


class ChatTests(TempRoot):
    def setUp(self):
        super().setUp()
        from cogs.events import on_message as om

        self.om = om
        self.cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=1)))
        self.sent: list[str] = []

        async def fake_send(message, content):
            self.sent.append(content)

        self.patches = [
            mock.patch.object(om, "_send_reply", fake_send),
            mock.patch.object(om, "build_prompt", mock.AsyncMock(return_value="PROMPT")),
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
        super().tearDown()

    def answer(self, text, model_reply="sure thing", calls=None, images=None):
        model = mock.AsyncMock(return_value=ModelTurn(reply=model_reply, tool_calls=calls or []))
        media = SimpleNamespace(text="", images=list(images or []), summary="", memory="")
        with mock.patch.object(self.om, "call_groq_turn", model):
            run(self.cog._answer_and_remember(_fake_message(text), None, text, text, media))
        return model

    # Text-only chat has no keyword filter: the safety footer is its guard.
    def test_text_only_input_is_not_keyword_filtered(self):
        model = self.answer("write a story about a 12 year old")
        model.assert_called_once()
        self.assertEqual(self.sent, ["sure thing"])

    def test_text_only_attachment_text_is_not_keyword_filtered(self):
        model = mock.AsyncMock(return_value=ModelTurn(reply="ok"))
        media = SimpleNamespace(text="file says: children", images=[], summary="", memory="")
        with mock.patch.object(self.om, "call_groq_turn", model):
            run(self.cog._answer_and_remember(_fake_message("read this"), None, "read this", "read this", media))
        model.assert_called_once()
        self.assertEqual(self.sent, ["ok"])

    def test_text_only_output_is_not_keyword_filtered(self):
        model = self.answer("tell me a story", model_reply="Once upon a time the kids went out")
        model.assert_called_once()
        self.assertEqual(self.sent, ["Once upon a time the kids went out"])

    # A message with pictures takes the image path, whose checks stay on.
    def test_blocked_input_with_pictures_never_reaches_the_model(self):
        model = self.answer("write a story about a 12 year old", images=[b"img"])
        model.assert_not_called()
        self.om.build_prompt.assert_not_called()
        self.assertEqual(self.sent, [bl.REFUSAL])
        self.om.remember_user.assert_not_called()
        self.cog._record_server_line.assert_not_called()

    def test_blocked_output_with_pictures_is_replaced(self):
        model = self.answer("tell me a story", model_reply="Once upon a time the kids went out", images=[b"img"])
        model.assert_called_once()
        self.assertEqual(self.sent, [bl.REFUSAL])
        self.om.remember_user.assert_not_called()
        self.om.remember_bot.assert_not_called()
        self.cog._record_server_line.assert_not_called()

    def test_clean_turn_goes_through(self):
        model = self.answer("what's a kidney for", model_reply="Filtering blood.")
        model.assert_called_once()
        self.assertEqual(self.sent, ["Filtering blood."])

    def test_crude_message_is_not_refused(self):
        model = self.answer(f"you're a {SLUR}, roast me", model_reply="Bold opener. Sit down.")
        model.assert_called_once()
        self.assertEqual(self.sent, ["Bold opener. Sit down."])

    def test_blocked_tool_arguments_do_not_run(self):
        calls = [{"name": "asset", "arguments": {"prompt": "a toddler on a beach"}}]
        with mock.patch.object(self.om, "execute", mock.AsyncMock()) as execute:
            self.answer("make me a picture of a beach", model_reply="", calls=calls)
        execute.assert_not_called()
        self.assertEqual(self.sent, [bl.REFUSAL])

    def test_text_tool_reply_is_not_keyword_filtered(self):
        from core.tools import ToolResult

        calls = [{"name": "duckduckgo", "arguments": {"query": "best parks"}}]
        result = ToolResult(text="Parks for children and toddlers.", ran=True, name="duckduckgo", fence=False)
        with mock.patch.object(self.om, "execute", mock.AsyncMock(return_value=result)):
            self.answer("best parks?", model_reply="", calls=calls)
        joined = "\n".join(self.sent)
        self.assertNotIn(bl.REFUSAL, joined)
        self.assertIn("children", joined)

    def test_episode_lines_are_not_keyword_filtered(self):
        from cogs.events import on_message as om

        cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=1)))
        with mock.patch("core.episodic.append_episodic", mock.AsyncMock()) as append:
            run(cog._record_server_line(_fake_message("x"), "look at that kid"))
            append.assert_called_once()


class DesktopChatTests(TempRoot):
    def test_local_chat_is_not_keyword_filtered(self):
        from core import client, local_chat

        with mock.patch.object(client, "call_groq", mock.AsyncMock(return_value="fine")) as model:
            self.assertEqual(run(local_chat.reply_local("my kids say hi")), "fine")
            model.assert_called_once()
        with mock.patch.object(client, "call_groq", mock.AsyncMock(return_value="the children laughed")):
            self.assertEqual(run(local_chat.reply_local("tell me a joke")), "the children laughed")


# ------------------------------------------------------------------ threads


class FakeThread:
    def __init__(self):
        self.posts: list[str] = []

    async def send(self, content=None, **_kw):
        self.posts.append(content)


class ThreadTests(TempRoot):
    """Thread posts are text chat: no keyword filter."""

    def test_thread_posts_are_not_keyword_filtered(self):
        from core import client, tools_thread

        replies = iter([
            ModelTurn(reply="First post about parks."),
            ModelTurn(reply="Second post: the kids ran around."),
            ModelTurn(reply="Third post."),
        ])
        thread = FakeThread()
        with mock.patch.object(client, "call_groq_turn", mock.AsyncMock(side_effect=lambda *a, **k: next(replies))):
            run(tools_thread._execute(SimpleNamespace(), thread, "plan", [], "parks"))
        self.assertEqual(thread.posts[0], "First post about parks.")
        self.assertNotIn(bl.REFUSAL, thread.posts)
        self.assertTrue(any("kids" in (post or "") for post in thread.posts))


# ------------------------------------------------------- memory slur toggle


class MaskToggleTests(TempRoot):
    def test_default_on(self):
        self.assertTrue(cf.masking_enabled())
        out = cf.clean_memory_text(f"said {SLUR} to <@123456789012345678>")
        self.assertFalse(cf.has_slur(out))
        self.assertNotIn("<@", out)

    def test_off_keeps_words_but_strips_ids(self):
        self.save(memory_mask_slurs="0")
        self.assertFalse(cf.masking_enabled())
        out = cf.clean_memory_text(f"said {SLUR} to <@123456789012345678> 123456789012345678")
        self.assertIn(SLUR, out)
        self.assertNotIn("<@", out)
        self.assertNotRegex(out, r"\\d{17,20}")

    def test_drop_rule_follows_toggle(self):
        on = [
            sp.build_merge_prompt("- Old.", "user: hi", char_limit=500),
            sp.build_condense_prompt("- Old.", 500),
            episodic.fold_prompt("old", ["a: b"], 500),
            episodic.condense_prompt("a", "b", 500),
            cs.condense_memory_prompt("user", "a", "b", 500),
        ]
        for text in on:
            self.assertIn("No slurs", text)
            self.assertIn("@mentions", text)
        self.save(memory_mask_slurs="0")
        off = [
            sp.build_merge_prompt("- Old.", "user: hi", char_limit=500),
            sp.build_condense_prompt("- Old.", 500),
            episodic.fold_prompt("old", ["a: b"], 500),
            episodic.condense_prompt("a", "b", 500),
            cs.condense_memory_prompt("user", "a", "b", 500),
        ]
        for text in off:
            self.assertNotIn("slurs", text.lower())
            self.assertIn("@mentions", text)

    def test_toggle_back_on(self):
        self.save(memory_mask_slurs="0")
        self.assertFalse(cf.masking_enabled())
        self.save(memory_mask_slurs="1")
        self.assertTrue(cf.masking_enabled())


if __name__ == "__main__":
    unittest.main()


class DorothyTypoTests(unittest.TestCase):
    def test_dorthy_from_oz_wet_blocks(self):
        from core.blocklist import is_blocked
        # Typo of Dorothy plus "wet" (which is not itself a sexual cue).
        self.assertTrue(is_blocked("Dorthy from Oz, wet"))
        self.assertTrue(is_blocked("Dorothey from the Wizard of Oz"))
        self.assertTrue(is_blocked("Doroty Gale"))
