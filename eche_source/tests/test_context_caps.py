# tests/test_context_caps.py
# Offline checks for Settings → Context & Memory and the concise summarizer
# prompts. No network, no Discord, no model.
#
# Run from eche_source:  python -m unittest tests.test_context_caps -v

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from core import builder  # noqa: E402
from core import context_caps as cc  # noqa: E402
from core import context_summarizer as cs  # noqa: E402
from core import episodic  # noqa: E402
from core import personal_recent as pr  # noqa: E402
from core import secrets  # noqa: E402
from core import summarizer_prompt as sp  # noqa: E402


class TempRoot(unittest.TestCase):
    """Every test gets its own ECHE_USER_ROOT and no cap env vars."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="eche-caps-")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ECHE_")}
        env["ECHE_USER_ROOT"] = self.root
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        cc.clear_cache()

    def tearDown(self):
        self._env.stop()
        cc.clear_cache()
        shutil.rmtree(self.root, ignore_errors=True)

    def write_settings(self, values: dict):
        path = secrets.settings_path(self.root)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(values, fh)
        # mtime resolution can be coarse; the cache key must still change.
        stamp = os.path.getmtime(path) + len(json.dumps(values)) % 7 + 1
        os.utime(path, (stamp, stamp))
        cc.clear_cache()


class CapTable(unittest.TestCase):
    def test_every_cap_is_public_and_sane(self):
        self.assertEqual(len(cc.KEYS), len(set(cc.KEYS)))
        for cap in cc.CAPS:
            with self.subTest(cap=cap.key):
                self.assertIn(cap.key, secrets.PUBLIC_KEYS)
                self.assertLessEqual(cap.low, cap.default)
                self.assertLessEqual(cap.default, cap.high)
                self.assertGreater(cap.step, 0)
                self.assertTrue(cap.env.startswith("ECHE_"))
                self.assertTrue(cap.label and cap.hint)

    def test_defaults_match_the_old_constants(self):
        self.assertEqual(cc.BY_KEY["memory_prompt_chars"].default, 3500)
        self.assertEqual(cc.BY_KEY["memory_summary_prompt_chars"].default, builder.PROMPT_CHARS
                         if hasattr(builder, "PROMPT_CHARS") else 600)
        self.assertEqual(cc.BY_KEY["episode_prompt_chars"].default, episodic.PROMPT_CHARS)
        self.assertEqual(cc.BY_KEY["episode_fold_after"].default, episodic.FOLD_AFTER)
        self.assertEqual(cc.BY_KEY["episode_summary_chars"].default, episodic.MAX_STORE)
        self.assertEqual(cc.BY_KEY["memory_recent_messages"].default, pr.KEEP)
        self.assertEqual(cc.BY_KEY["memory_fold_every"].default, pr.FOLD_EVERY)
        from core import message_media, tools_thread

        self.assertEqual(cc.BY_KEY["attachment_text_chars"].default, message_media._TEXT_CLIP)
        self.assertEqual(cc.BY_KEY["media_prompt_chars"].default, message_media._MEDIA_CLIP)
        self.assertEqual(cc.BY_KEY["media_memory_chars"].default, message_media._MEMORY_CLIP)
        self.assertEqual(cc.BY_KEY["thread_notes_chars"].default, tools_thread._NOTES_CAP)

    def test_help_text_for_every_cap(self):
        from gui.widgets.settings_help import FIELD_HELP

        for key in cc.KEYS:
            with self.subTest(key=key):
                self.assertIn(key, FIELD_HELP)
                title, body = FIELD_HELP[key]
                self.assertTrue(title)
                self.assertIn("### Default", body)
                self.assertIn(str(cc.BY_KEY[key].high), body)
        self.assertIn("context_memory", FIELD_HELP)


class Clamp(unittest.TestCase):
    def test_blank_and_junk_use_default(self):
        for raw in (None, "", "  ", "abc", "1e3x", [], {}):
            with self.subTest(raw=raw):
                self.assertEqual(cc.clamp("memory_prompt_chars", raw), 3500)

    def test_range(self):
        self.assertEqual(cc.clamp("memory_prompt_chars", "5"), 1000)
        self.assertEqual(cc.clamp("memory_prompt_chars", "999999"), 16000)
        self.assertEqual(cc.clamp("memory_prompt_chars", " 4200 "), 4200)
        self.assertEqual(cc.clamp("memory_prompt_chars", 4200.9), 4200)
        self.assertEqual(cc.clamp("memory_recent_messages", "-3"), 1)
        self.assertEqual(cc.clamp("memory_summary_chars", "5000"), 1900)

    def test_zero_means_all_where_allowed(self):
        self.assertEqual(cc.clamp("thread_posted_chars", "0"), 0)
        self.assertEqual(cc.clamp("local_history_turns", "0"), 0)
        self.assertEqual(cc.clamp("memory_message_chars", "0"), 0)


class Persistence(TempRoot):
    def test_defaults_without_settings(self):
        for cap in cc.CAPS:
            self.assertEqual(cc.get(cap.key), cap.default)

    def test_save_all_load_all_round_trip(self):
        data = {cap.key: "" for cap in cc.CAPS}
        data.update(memory_prompt_chars="2200", memory_recent_messages="5",
                    thread_posted_chars="4000")
        with mock.patch.object(secrets, "_load_encrypted_secrets", return_value={}), \
             mock.patch.object(secrets, "_save_encrypted_secrets", create=True), \
             mock.patch.object(secrets, "_write_encrypted_secrets", create=True):
            try:
                secrets.save_all(data, root=self.root)
            except Exception as exc:  # DPAPI is Windows only
                if not os.path.isfile(secrets.settings_path(self.root)):
                    self.skipTest(f"save_all needs Windows here: {exc}")
        with open(secrets.settings_path(self.root), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["memory_prompt_chars"], "2200")
        self.assertEqual(saved["memory_recent_messages"], "5")
        self.assertEqual(secrets.public_value("thread_posted_chars", root=self.root), "4000")
        cc.clear_cache()
        self.assertEqual(cc.get("memory_prompt_chars"), 2200)
        self.assertEqual(cc.get("memory_recent_messages"), 5)
        self.assertEqual(cc.get("thread_posted_chars"), 4000)
        self.assertEqual(cc.get("episode_fold_after"), 5)  # blank → default

    def test_settings_clamped_and_cache_follows_file(self):
        self.write_settings({"memory_prompt_chars": "50"})
        self.assertEqual(cc.get("memory_prompt_chars"), 1000)
        self.write_settings({"memory_prompt_chars": "8000"})
        self.assertEqual(cc.get("memory_prompt_chars"), 8000)

    def test_env_fallback_and_settings_win(self):
        os.environ["ECHE_MEMORY_FOLD_EVERY"] = "7"
        cc.clear_cache()
        self.assertEqual(cc.get("memory_fold_every"), 7)
        self.write_settings({"memory_fold_every": "4"})
        self.assertEqual(cc.get("memory_fold_every"), 4)


class Tokens(unittest.TestCase):
    def test_summary_tokens_follow_chars(self):
        self.assertEqual(cc.summary_tokens(1900), 1900 // 4 + 16)
        self.assertEqual(cc.summary_tokens(0), 32)
        self.assertLess(cc.summary_tokens(600), cc.summary_tokens(1900))


class Bullets(unittest.TestCase):
    def test_tidy_keeps_bullets_and_flattens_prose(self):
        self.assertEqual(episodic.tidy_summary("  a   b \n c "), "a b c")
        text = "* Likes tea.\n-   Lives in Oslo.\n  wraps here.\n• Hates spam."
        self.assertEqual(
            episodic.tidy_summary(text),
            "- Likes tea.\n- Lives in Oslo. wraps here.\n- Hates spam.",
        )

    def test_clip_on_whole_bullets(self):
        text = "\n".join(f"- Fact number {i} about this person here." for i in range(30))
        for limit in (120, 300, 601):
            with self.subTest(limit=limit):
                out = episodic._clip_prose(text, limit)
                self.assertLessEqual(len(out), limit)
                for line in out.splitlines():
                    self.assertTrue(line.startswith("- Fact number") and line.endswith("here."))

    def test_cut_off_bullet_list(self):
        done = "\n".join(f"- Finished fact number {i} about them." for i in range(12))
        self.assertFalse(episodic._cut_off(done))
        self.assertTrue(episodic._cut_off(done + "\n- And then the reply stopped mid"))
        self.assertFalse(episodic._cut_off("- Short and unstopped"))  # under 400


class PromptCaps(TempRoot):
    def rows(self, n, width=200):
        return [f"msg {i} " + "x" * width for i in range(n)]

    def test_fit_memory_prompt_respects_slider(self):
        summary = "\n".join(f"- Fact {i} about them, kept short." for i in range(60))
        for cap in (1200, 2500, 3500):
            with self.subTest(cap=cap):
                self.write_settings({"memory_prompt_chars": str(cap)})
                text = builder.fit_memory_prompt(
                    "Tuesday", summary, self.rows(3), summary, self.rows(3), "", "Latest: hi"
                )
                self.assertLessEqual(len(text), cap)
                self.assertIn("Latest: hi", text)

    def test_summary_block_uses_slider(self):
        summary = "\n".join(f"- Fact {i} about them, kept short." for i in range(60))
        self.write_settings({"memory_summary_prompt_chars": "200"})
        block = builder._summary_block("About this person:\n", summary)
        self.assertLessEqual(len(block) - len("About this person:\n"), 200)
        self.write_settings({"memory_summary_prompt_chars": "1500"})
        longer = builder._summary_block("About this person:\n", summary)
        self.assertGreater(len(longer), len(block))

    def test_prompt_episode_uses_slider(self):
        summary = "Summary:\n" + " ".join(f"People talked about subject {i}." for i in range(80))
        self.write_settings({"episode_prompt_chars": "150"})
        self.assertLessEqual(len(episodic.prompt_episode(summary)), 150)
        self.write_settings({"episode_prompt_chars": "1200"})
        self.assertLessEqual(len(episodic.prompt_episode(summary)), 1200)
        self.assertGreater(len(episodic.prompt_episode(summary)), 600)

    def test_episode_store_and_fold_follow_slider(self):
        self.write_settings({"episode_summary_chars": "800", "episode_fold_after": "9"})
        self.assertEqual(episodic.max_store(), 800)
        self.assertLess(episodic.soft_store(), 800)
        self.assertEqual(episodic.fold_after(), 9)
        self.assertGreaterEqual(episodic.recent_cap(), 12)

    def test_pin_cap_follows_slider(self):
        from core import context_manager as cm

        self.assertEqual(cm.pin_cap(), min(cm.PIN_CAP, 1900))
        self.write_settings({"memory_summary_chars": "700"})
        self.assertEqual(cm.pin_cap(), 700)
        self.assertLessEqual(cs.cloud_target(), 700)


class ConcisePrompts(unittest.TestCase):
    FORBIDDEN = ("No bullet list", "Plain prose", "polar")

    def check(self, prompt, room):
        self.assertIn(f"at most {room} characters", prompt)
        self.assertIn('"- " bullets', prompt)
        self.assertIn("Drop", prompt)
        for word in self.FORBIDDEN:
            self.assertNotIn(word, prompt)

    def test_episode_prompts(self):
        self.check(episodic.fold_prompt("- Old subject.", ["Mon past Dialog: hi"], 777), 777)
        self.check(episodic.condense_prompt("- Old.", "- New.", 555), 555)
        self.assertIn("quotes", episodic.fold_prompt("", [], 500))

    def test_personal_prompts(self):
        for side in ("user", "bot"):
            with self.subTest(side=side):
                fold = cs.fold_memory_prompt(side, "- Likes tea.", ["hello"], 640)
                self.check(fold, 640)
                self.assertIn("play-by-play", fold)
                self.check(cs.condense_memory_prompt(side, "- A.", "- B.", 420), 420)

    def test_legacy_templates(self):
        merge = sp.build_merge_prompt("- Old.", "user: hi", char_limit=900)
        self.assertIn("900 characters", merge)
        self.assertNotIn("{char_limit}", merge)
        self.assertNotIn("No bullet list", sp.DEFAULT_SUMMARIZER_PROMPT)
        self.assertIn("{char_limit}", sp.DEFAULT_CONDENSE_PROMPT)

    def test_prompts_shorter_than_before(self):
        for old in sp._OLD_DEFAULT_SUMMARIZER_PROMPTS:
            self.assertLess(len(sp.DEFAULT_SUMMARIZER_PROMPT), len(old) + 1)

    def test_prompt_echo_is_rejected(self):
        echo = "Reply with the updated memory only.\n- Likes tea."
        self.assertFalse(cs._usable_fold(echo))
        self.assertTrue(cs._usable_fold("- Likes tea.\n- Lives in Oslo."))


class OldPromptMigration(TempRoot):
    def test_unedited_old_default_is_upgraded(self):
        path = os.path.join(self.root, "config", "summarizer_prompt.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(sp._OLD_DEFAULT_SUMMARIZER_PROMPTS[0])
        self.assertEqual(sp.summarizer_prompt_path(), path)
        got = sp.get_summarizer_prompt()
        self.assertEqual(got.strip(), sp.DEFAULT_SUMMARIZER_PROMPT.strip())
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), sp.DEFAULT_SUMMARIZER_PROMPT.strip())

    def test_edited_file_is_left_alone(self):
        path = os.path.join(self.root, "config", "summarizer_prompt.txt")
        mine = sp._OLD_DEFAULT_SUMMARIZER_PROMPTS[0] + "\nAlso keep pet names."
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(mine)
        self.assertEqual(sp.get_summarizer_prompt().strip(), mine.strip())

    def test_edited_prompt_is_kept(self):
        self.assertFalse(sp.is_old_default("My own prompt {char_limit}"))


class PersonalBuffer(TempRoot):
    def test_keep_and_fold_follow_sliders(self):
        self.write_settings({"memory_recent_messages": "2", "memory_fold_every": "5"})
        uid = 42
        for i in range(4):
            pr.push(uid, "user", f"message {i}")
        # Nothing folded yet: all four wait for the fold, the prompt sees two.
        self.assertEqual(len(pr.notes_for_fold(uid, "user")), 4)
        self.assertEqual(pr.prompt_lines(uid, "user")[-2:], ["message 2", "message 3"])
        self.assertEqual(len(pr.prompt_lines(uid, "user")), 2)
        pr.clear_folded(uid, "user")
        self.assertEqual(pr.messages(uid, "user"), ["message 2", "message 3"])

    def test_message_cap(self):
        self.assertEqual(pr.clip_message("x" * 50, 0), "x" * 50)
        clipped = pr.clip_message("word " * 100, 101)
        self.assertLessEqual(len(clipped), 101)
        self.assertTrue(clipped.endswith("…"))
        self.write_settings({"memory_message_chars": "120"})
        pr.push(7, "user", "y" * 500)
        self.assertLessEqual(len(pr.messages(7, "user")[-1]), 120)


class ThreadCaps(TempRoot):
    def test_notes_and_posted(self):
        from core import tools_thread as tt

        notes = tt.append_notes("a" * 100, "b" * 100, cap=150)
        self.assertEqual(len(notes), 150)
        self.assertTrue(notes.endswith("b"))
        posts = [f"Post {i}. " + "words here. " * 40 for i in range(6)]
        self.assertEqual(tt.posted_so_far(posts, cap=0), "\n\n".join(posts))
        tail = tt.posted_so_far(posts, cap=500)
        self.assertLessEqual(len(tail), 501)
        self.assertTrue(tail.startswith("…"))
        self.assertIn("Post 5.", tail)
        self.assertEqual(tt.posted_so_far([]), "(nothing yet)")


if __name__ == "__main__":
    unittest.main()
