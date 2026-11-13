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
        self.prompt = cs.fold_memory_prompt(
            "bot", "- user asked about models.", ["Top pick is orcarouter."], 640
        )

    def test_first_person_about_own_outputs(self):
        low = self.prompt.lower()
        self.assertIn("my own outputs", low)
        self.assertIn('starts with "i"', low)
        self.assertIn('never start a bullet with "user"', low)
        self.assertIn("running bits", low)
        self.assertIn("pictures and videos i made", low)
        self.assertIn("my recent outputs", low)
        # Not the person-centric job from the user pin.
        self.assertNotIn("who they are", low)
        self.assertNotIn("recent messages:", low)

    def test_caps_and_shared_rules_kept(self):
        self.assertIn("at most 640 characters", self.prompt)
        self.assertIn('"- " bullets', self.prompt)
        self.assertIn("play-by-play", self.prompt)
        self.assertIn("@mentions", self.prompt)
        self.assertIn("refus", self.prompt)

    def test_condense_is_first_person(self):
        text = cs.condense_memory_prompt("bot", "- A.", "- B.", 420).lower()
        self.assertIn("my own outputs", text)
        self.assertIn('never start a bullet with "user"', text)
        self.assertIn("at most 420 characters", text)

    def test_user_side_unchanged(self):
        user = cs.fold_memory_prompt("user", "", ["hi"], 500)
        self.assertIn("who they are", user)
        self.assertIn("Recent messages:", user)
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


class BotPinFold(TempRoot):
    """The bug: every bot-pin line started with "user". The fold now retries
    in first person and never writes a "user ..." bullet to the bot pin."""

    def _fold(self, replies):
        live = SimpleNamespace(content=bot_memory_initial("sam"), pinned=True)
        written = {}

        async def read_first(_channel, _headers):
            return live, True

        async def write(_channel, header, body):
            written["header"] = header
            written["body"] = body
            return SimpleNamespace(id=1), "edited"

        calls = []

        async def completion(prompt, _limit):
            calls.append(prompt)
            return replies[min(len(calls) - 1, len(replies) - 1)]

        pinned = SimpleNamespace(channel=object())
        with mock.patch("core.discord_store.read_pinned_first", read_first), \
                mock.patch("core.discord_store.write_pinned", write), \
                mock.patch.object(cs, "_memory_completion", completion):
            result = _run(
                cs.fold_long_memory(
                    pinned,
                    "Eche with sam:\n\n",
                    ["[I used duckduckgo: searched \"a cat\"] here you go"],
                    side="bot",
                )
            )
        return result, written, calls

    def test_user_centric_fold_is_retried_in_first_person(self):
        result, written, calls = self._fold([
            "- user asked for a cat video.\n- user liked it.",
            "- I made a cat video for them.\n- I promised a dog one next.",
        ])
        self.assertTrue(result)
        self.assertEqual(len(calls), 2)
        self.assertIn("my own outputs", calls[0])
        self.assertEqual(written["header"], BOT_CONTEXT_HEADER)
        body = written["body"]
        self.assertTrue(body.startswith("Eche with sam:"))
        self.assertIn("Summary:", body)
        self.assertIn("- I made a cat video for them.", body)
        for line in body.splitlines():
            self.assertFalse(line.lower().lstrip("- ").startswith("user"), line)

    def test_stubborn_user_bullets_are_dropped(self):
        result, written, _calls = self._fold([
            "- user asked for a cat video.\n- I made a cat video.",
            "- user asked again.\n- I made a cat video.",
        ])
        self.assertTrue(result)
        self.assertIn("- I made a cat video.", written["body"])
        self.assertNotIn("user asked", written["body"])

    def test_all_user_bullets_keep_the_buffer(self):
        result, written, _calls = self._fold([
            "- user asked for a cat video.",
            "- user asked for a cat video.",
        ])
        self.assertFalse(result)
        self.assertEqual(written, {})

    def test_first_person_fold_is_one_call(self):
        result, written, calls = self._fold(["- I made a cat video."])
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)


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
