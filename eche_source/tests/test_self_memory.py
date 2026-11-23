# tests/test_self_memory.py
# Eche's self memory (the bot pin) is a record of eche's own outputs.
# Offline: no network, no Discord, no model.
#
# Run from eche_source:  python -m unittest tests.test_self_memory -v

from __future__ import annotations

import asyncio
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

from core import builder  # noqa: E402
from core import context_summarizer as cs  # noqa: E402
from core import memory_lines as ml  # noqa: E402
from core import personal_recent as pr  # noqa: E402
from core.discord_store import BOT_CONTEXT_HEADER, bot_memory_initial  # noqa: E402


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="eche-self-")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ECHE_")}
        env["ECHE_USER_ROOT"] = self.root
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.root, ignore_errors=True)


class StoredBotRecord(unittest.TestCase):
    LONG = (
        "Alright here's the plan: I'll keep calling you Captain Toast from now on, "
        "and next week I'll check whether your Ollama swap to the orca model actually "
        "fixed the refusals, because the old one kept balking at dance prompts."
    )

    def test_plain_reply_is_stored_whole(self):
        self.assertEqual(ml.bot_output_record(self.LONG), self.LONG)
        self.assertGreater(len(self.LONG), 120)

    def test_tool_turn_keeps_action_and_words(self):
        action = ml.tool_action("duckduckgo", {"query": "qwen 27b uncensored"})
        got = ml.bot_output_record(
            "draft reply that was never posted",
            ["Top pick is the orcarouter build."],
            [action],
        )
        self.assertEqual(
            got,
            '[I used duckduckgo: searched "qwen 27b uncensored"] '
            "Top pick is the orcarouter build.",
        )
        self.assertNotIn("draft reply", got)

    def test_quiet_search_records_what_was_posted(self):
        action = ml.tool_action(
            "duckduckgo",
            {"query": "raccoon facts"},
            posted="Raccoons wash their food.",
        )
        got = ml.bot_output_record("", [], [action])
        self.assertTrue(got.startswith("[I used duckduckgo: searched"))
        self.assertIn("raccoon facts", got)
        self.assertIn('posted "Raccoons wash their food."', got)

    def test_fallback_line_is_first_person_and_longer(self):
        self.assertEqual(ml.bot_memory_line("", ["duckduckgo"]), "I used duckduckgo")
        line = ml.bot_memory_line(self.LONG)
        self.assertTrue(line.startswith("I said "))
        self.assertGreater(len(line), 200)
        self.assertFalse(line.lower().startswith("user"))
        self.assertEqual(ml.bot_memory_line(""), "I answered")


class BotFoldPrompt(unittest.TestCase):
    def setUp(self):
        from core import summarizer_prompt as sp

        self.sp = sp
        self.prompt = sp.build_self_fold_prompt(
            "b1 | stances | I said orcarouter is the pick.", "Them: which model?\nEche: Top pick is orcarouter."
        )

    def test_first_person_about_own_outputs(self):
        low = self.prompt.lower()
        self.assertIn("my own outputs", low)
        self.assertIn('starts with "i"', low)
        self.assertIn('never start a text with "user"', low)
        self.assertIn("running jokes", low)
        self.assertIn("commitments", low)
        self.assertIn('"kept"', low)
        # Not the person-centric job from the user memory.
        self.assertNotIn("identity", low)

    def test_shared_rules_kept(self):
        self.assertIn("play-by-play", self.sp.build_user_fold_prompt("", "Them: hi"))
        self.assertIn("@mentions", self.prompt)
        self.assertIn("refus", self.prompt)
        self.assertIn("never store names of other people", self.prompt)

    def test_user_side_is_about_them(self):
        user = self.sp.build_user_fold_prompt("", "Them: hi")
        self.assertIn("identity", user)
        self.assertIn('"Them" is the person', user)
        self.assertNotIn("my own outputs", user)


class UserLeadingBullets(unittest.TestCase):
    def test_detect_and_drop(self):
        text = "- User asked for a video.\n- I made a dancing video.\n- the user likes tea."
        self.assertEqual(len(cs.user_centric_bullets(text)), 2)
        self.assertEqual(cs.bot_voice_only(text), "- I made a dancing video.")

    def test_words_like_username_are_not_user(self):
        self.assertEqual(cs.user_centric_bullets("- usernames I joked about."), [])


def _run(coro):
    return asyncio.run(coro)


class BotMemoryEdits(TempRoot):
    """Eche's own memory: first person only, promises kept move to "kept"."""

    def test_user_centric_texts_are_dropped(self):
        from core import memory_store as ms

        profile = ms.empty("bot")
        stats = ms.apply_ops(profile, {"add": [
            {"section": "stances", "text": "User asked for a cat video."},
            {"section": "stances", "text": "the user likes tea"},
            {"section": "commitments", "text": "I promised a dog video next."},
            {"section": "bits", "text": "I keep calling them Captain Toast."},
        ]}, "bot")
        texts = [row["text"] for _k, row in ms._all(profile)]
        self.assertEqual(stats["added"], 2)
        self.assertIn("I promised a dog video next.", texts)
        for text in texts:
            self.assertFalse(text.lower().startswith("user"), text)

    def test_kept_promise_moves(self):
        from core import memory_store as ms

        profile = ms.empty("bot")
        ms.apply_ops(profile, {"add": [{"section": "commitments", "text": "I promised a dog video."}]}, "bot")
        pid = profile["sections"]["commitments"][0]["id"]
        stats = ms.apply_ops(profile, {"kept": [pid]}, "bot")
        self.assertEqual(stats["kept"], 1)
        self.assertEqual(profile["sections"]["commitments"], [])
        self.assertEqual(profile["sections"]["kept"][0]["text"], "I promised a dog video.")
        rendered = ms.render(profile, 600, "bot")
        self.assertIn("Promises I kept:", rendered)

    def test_open_promises_render_first(self):
        from core import memory_store as ms

        profile = ms.empty("bot")
        ms.apply_ops(profile, {"add": [
            {"section": "stances", "text": "I said Linux beats Windows for servers."},
            {"section": "commitments", "text": "I promised a dog video."},
        ]}, "bot")
        self.assertTrue(ms.render(profile, 600, "bot").startswith("- My open promises:"))


class BotBufferStoresOwnWords(TempRoot):
    def test_record_lands_in_bot_buffer_verbatim(self):
        record = ml.bot_output_record(
            "", ["Here is what I found."], [ml.tool_action("duckduckgo", {"query": "a cat"})]
        )
        rows, pending = pr.push(4242, "bot", record)
        self.assertEqual(rows[-1], record)
        self.assertEqual(pending, 1)
        self.assertEqual(pr.messages(4242, "user"), [])
        self.assertFalse(rows[-1].lower().startswith("user"))


class PromptLabels(unittest.TestCase):
    def test_bot_memory_is_labelled_as_own_outputs(self):
        out = builder.render_memory_prompt(
            "today",
            "- Likes tea.",
            ["hey"],
            "- I promised a dog video.",
            ["Here's your cat video."],
            "",
            "=== MOST RECENT MESSAGE ===\nsam:\nthanks",
        )
        self.assertIn("Your own past outputs with this person", out)
        self.assertIn("stay consistent", out)
        self.assertIn("Your own recent replies to them, as you posted them:", out)
        self.assertIn("About this person:", out)

    def test_fit_still_finds_recent_block(self):
        out = builder.fit_memory_prompt(
            "today", "x" * 3000, ["a"], "y" * 3000, ["b"], "", "=== MOST RECENT MESSAGE ===\nsam:\nhi",
            limit=1000,
        )
        self.assertLessEqual(len(out), 1000)
        self.assertIn("Your own recent replies to them", out)
        self.assertIn("=== MOST RECENT MESSAGE ===", out)


if __name__ == "__main__":
    unittest.main()
