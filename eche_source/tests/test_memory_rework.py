# tests/test_memory_rework.py
# Structured long-term memory (user + Eche's own), the single fold path, and
# real episodes. Offline: no network, no Discord, no model (all mocked).
#
# Run from eche_source:  python -m pytest -q tests/test_memory_rework.py

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from core import context_caps as cc  # noqa: E402
from core import context_summarizer as cs  # noqa: E402
from core import episodic as ep  # noqa: E402
from core import memory_privacy as mp  # noqa: E402
from core import memory_store as ms  # noqa: E402
from core import personal_recent as pr  # noqa: E402
from core import summarizer_prompt as sp  # noqa: E402
from core.discord_store import BOT_CONTEXT_HEADER, OLD_BOT_CONTEXT_HEADER, USER_CONTEXT_HEADER  # noqa: E402

DAY = 86400.0


def run(coro):
    return asyncio.run(coro)


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="eche-mem-")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ECHE_")}
        env["ECHE_USER_ROOT"] = self.root
        env.pop("SUMMARIZER_MODEL", None)
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        cc.clear_cache()

    def tearDown(self):
        self._env.stop()
        cc.clear_cache()
        shutil.rmtree(self.root, ignore_errors=True)


# ------------------------------------------------------------------ privacy


class Privacy(unittest.TestCase):
    def test_third_party_names(self):
        self.assertTrue(mp.third_party_names("Asked how much Dale, Dan, and Tony paid."))
        self.assertTrue(mp.third_party_names("My friend Kowalski fixes cars."))
        self.assertTrue(mp.third_party_names("Plays games with Zorblat.", known_names=["Zorblat"]))
        self.assertEqual(mp.third_party_names("Uses Linux, Python, and Ollama."), [])
        self.assertEqual(mp.third_party_names("Works on Eche, a Discord bot."), [])
        # Their own name is allowed.
        self.assertEqual(mp.third_party_names("Goes by Dale online.", own_names=["dale"]), [])

    def test_sensitive_and_minors(self):
        for text in (
            "Email is someone@example.com.",
            "Phone is +1 555 123 4567.",
            "Lives at 12 Main Street.",
            "Was diagnosed with something.",
            "Password is hunter2.",
            "Has two kids.",
            "Is 15 years old.",
        ):
            with self.subTest(text=text):
                self.assertTrue(mp.entry_problem(text), text)
        self.assertEqual(mp.entry_problem("Prefers short, blunt replies."), "")

    def test_redact_names_in_episodes(self):
        self.assertEqual(mp.redact_names("P1 said Dale was late."), "P1 said someone was late.")


# ------------------------------------------------------------------ store


class StoreEdits(TempRoot):
    def test_the_reported_mess_is_not_stored(self):
        profile = ms.empty("user")
        stats = ms.apply_ops(profile, {"add": [
            {"section": "identity", "text": "Asked how much Dale, Dan, and Tony paid to be removed from search results."},
            {"section": "identity", "text": "Asked whether yt-dlp supports HLS?"},
            {"section": "identity", "text": "Is working on the eche music player right now."},
            {"section": "style", "text": "Prefers short, blunt replies"},
            {"section": "style", "text": "Prefers blunt, short replies."},
            {"section": "identity", "text": "Runs Eche on a Windows PC."},
        ]}, "user", own_names=["sevin"])
        self.assertIn("third-party name", stats["dropped"])
        sections = {k: [r["text"] for r in rows] for k, rows in profile["sections"].items()}
        self.assertEqual(sections["identity"], ["Runs Eche on a Windows PC."])
        self.assertEqual(sections["style"], ["Prefers short, blunt replies."])
        self.assertEqual(sections["recent"], ["Asked whether yt-dlp supports HLS?"])
        self.assertEqual(sections["projects"], ["Is working on the eche music player right now."])
        self.assertEqual(stats["merged"], 1)
        joined = json.dumps(profile)
        for name in ("Dale", "Dan", "Tony"):
            self.assertNotIn(name, joined)

    def test_update_remove_touch_and_bad_ids(self):
        profile = ms.empty("user")
        ms.apply_ops(profile, {"add": [
            {"section": "identity", "text": "Lives in Ohio."},
            {"section": "likes", "text": "Likes jazz."},
        ]}, "user", now=1000.0)
        ohio = profile["sections"]["identity"][0]["id"]
        jazz = profile["sections"]["likes"][0]["id"]
        stats = ms.apply_ops(profile, {
            "update": [{"id": ohio, "text": "Lives in Texas now."}, {"id": "nope", "text": "x"}],
            "remove": [jazz, "u999"],
            "touch": [ohio],
            "add": "not a list",
        }, "user", now=2000.0)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["removed"], 1)
        row = profile["sections"]["identity"][0]
        self.assertEqual(row["text"], "Lives in Texas now.")
        self.assertEqual(row["hits"], 3)
        self.assertEqual(profile["sections"]["likes"], [])

    def test_bad_update_keeps_the_old_text(self):
        profile = ms.empty("user")
        ms.apply_ops(profile, {"add": [{"section": "identity", "text": "Lives in Ohio."}]}, "user")
        rid = profile["sections"]["identity"][0]["id"]
        ms.apply_ops(profile, {"update": [{"id": rid, "text": "Lives in Ohio with Tony."}]}, "user")
        self.assertEqual(profile["sections"]["identity"][0]["text"], "Lives in Ohio.")

    def test_unsafe_and_filtered_texts_dropped(self):
        profile = ms.empty("user")
        ms.apply_ops(profile, {"add": [
            {"section": "likes", "text": "Likes the pass moderation filters trick."},
            {"section": "likes", "text": "Mentions <@123456789012345678> a lot."},
            {"section": "likes", "text": "Asked for a sexy schoolgirl."},
        ]}, "user")
        joined = json.dumps(profile)
        self.assertNotIn("<@", joined)
        self.assertNotIn("123456789012345678", joined)
        self.assertNotIn("schoolgirl", joined)

    def test_expiry_decay_and_caps(self):
        profile = ms.empty("user")
        t0 = 1_000_000.0
        ms.apply_ops(profile, {"add": [
            {"section": "projects", "text": "Building a Discord music bot."},
            {"section": "recent", "text": "HLS streaming."},
        ] + [{"section": "likes", "text": f"Likes {word}."} for word in
             ("jazz", "hiking", "chess", "ramen", "linux", "rust", "cycling", "vinyl")]}, "user", now=t0)
        self.assertEqual(len(profile["sections"]["likes"]), 5)
        # 20 days later the recent topic has faded below 0.2; the project lasts 30 days.
        ms.maintain(profile, "user", now=t0 + 20 * DAY)
        self.assertEqual(profile["sections"]["recent"], [])
        self.assertEqual(len(profile["sections"]["projects"]), 1)
        ms.maintain(profile, "user", now=t0 + 31 * DAY)
        self.assertEqual(profile["sections"]["projects"], [])

    def test_touch_keeps_a_project_alive(self):
        profile = ms.empty("user")
        ms.apply_ops(profile, {"add": [{"section": "projects", "text": "Building a Discord music bot."}]}, "user", now=0.0)
        rid = profile["sections"]["projects"][0]["id"]
        ms.apply_ops(profile, {"touch": [rid]}, "user", now=25 * DAY)
        ms.maintain(profile, "user", now=50 * DAY)
        self.assertEqual(len(profile["sections"]["projects"]), 1)

    def test_render_order_and_limits(self):
        profile = ms.empty("user")
        ms.apply_ops(profile, {"add": [
            {"section": "recent", "text": "Talked about HLS."},
            {"section": "likes", "text": "Likes jazz."},
            {"section": "identity", "text": "Works nights as a nurse."},
            {"section": "style", "text": "Wants blunt answers."},
        ] + [{"section": "identity", "text": f"Fact {word} about their setup and work."} for word in
             ("alpha", "bravo", "charlie", "delta")]}, "user")
        text = ms.render(profile, 600, "user")
        self.assertTrue(text.startswith("- Identity:"))
        self.assertLess(text.index("How they want to be treated"), text.index("Likes:"))
        for limit in (60, 150, 600, 5000):
            with self.subTest(limit=limit):
                out = ms.render(profile, limit, "user")
                self.assertLessEqual(len(out), min(limit, ms.RENDER_CAP))
        self.assertLessEqual(len(ms.prompt_text(profile, "user")), 600)

    def test_round_trip_and_junk_file(self):
        profile = ms.empty("user")
        ms.apply_ops(profile, {"add": [{"section": "identity", "text": "Lives in Ohio."}]}, "user")
        ms.save(7, "user", profile)
        self.assertEqual(ms.load(7, "user")["sections"]["identity"][0]["text"], "Lives in Ohio.")
        with open(ms._path(8, "user"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIsNone(ms.load(8, "user"))
        weird = ms.normalize({"sections": {"identity": [{"id": "u5", "text": "x" * 500}, "junk", {"text": ""}]}}, "user")
        self.assertEqual(len(weird["sections"]["identity"]), 1)
        self.assertEqual(weird["next_id"], 6)


# ------------------------------------------------------------------ model


class SummarizerModel(TempRoot):
    def test_router_and_blank_use_the_default(self):
        for value in ("", "openrouter/auto", "auto"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"SUMMARIZER_MODEL": value, "GROQ_MODEL": "openrouter/auto"}):
                    self.assertEqual(sp.get_summarizer_model(), sp.DEFAULT_SUMMARIZER_MODEL)

    def test_chat_model_is_not_used_on_cloud(self):
        with mock.patch.dict(os.environ, {"GROQ_MODEL": "some/chat-model"}):
            self.assertEqual(sp.get_summarizer_model(), "qwen/qwen3.6-27b")

    def test_explicit_model_wins(self):
        with mock.patch.dict(os.environ, {"SUMMARIZER_MODEL": "meta/some-model"}):
            self.assertEqual(sp.get_summarizer_model(), "meta/some-model")

    def test_ollama_falls_back_to_local_chat_model(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "ollama", "GROQ_MODEL": "qwen3:14b"}):
            self.assertEqual(sp.get_summarizer_model(), "qwen3:14b")

    def test_temperature_is_low(self):
        self.assertEqual(sp.SUMMARIZER_TEMPERATURE, 0.2)


# ------------------------------------------------------------------ buffer


class Buffer(TempRoot):
    def test_old_file_without_times_still_loads(self):
        path = pr._path(5, "user")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"pending": 2, "stash": [], "messages": ["a", "b"]}, fh)
        self.assertEqual(pr.pending_rows(5, "user"), [(0.0, "a"), (0.0, "b")])
        pr.push(5, "user", "c")
        rows = pr.pending_rows(5, "user")
        self.assertEqual([t for _a, t in rows], ["a", "b", "c"])
        self.assertGreater(rows[-1][0], 0)

    def test_clear_keeps_messages_that_arrived_during_the_fold(self):
        for i in range(3):
            pr.push(6, "user", f"m{i}")
        taken = pr.pending_count(6, "user")
        pr.push(6, "user", "late")
        pr.clear_folded(6, "user", taken)
        self.assertEqual(pr.pending_count(6, "user"), 1)
        self.assertEqual([t for _a, t in pr.pending_rows(6, "user")], ["late"])

    def test_fold_reason(self):
        self.assertEqual(cs.fold_reason(9), "")
        for i in range(7):
            pr.push(9, "user", f"m{i}")
        self.assertEqual(cs.fold_reason(9), "")
        pr.push(9, "user", "eighth")
        self.assertEqual(cs.fold_reason(9), "count")
        pr.clear_folded(9, "user")
        pr.push(9, "user", "one")
        self.assertEqual(cs.fold_reason(9), "")
        self.assertEqual(cs.fold_reason(9, now=time.time() + 31 * 60), "idle")

    def test_conversation_has_both_sides_in_order(self):
        text = cs.conversation_text([(1.0, "which model?"), (3.0, "thanks")], [(2.0, "Use qwen.")])
        self.assertEqual(text.splitlines(), ["Them: which model?", "Eche: Use qwen.", "Them: thanks"])
        self.assertLessEqual(len(cs.conversation_text([(float(i), "x" * 500) for i in range(1, 40)], [])), cs.CONVERSATION_CHARS)


# ------------------------------------------------------------------ fold


class FakePin(SimpleNamespace):
    pass


class FoldPerson(TempRoot):
    UID = 4242

    def _run_fold(self, replies, *, user_pin="", bot_pin="", pins_ok=True, **kw):
        threads = {"user": SimpleNamespace(name="user-thread"), "bot": SimpleNamespace(name="bot-thread")}
        pins = {"user": user_pin, "bot": bot_pin}
        written = {}
        prompts = []

        async def ctx_channel(_bot, _guild, _uid, _name=None):
            return threads["user"], FakePin(content=pins["user"])

        async def bot_channel(_bot, _uid, _name=None):
            return threads["bot"], FakePin(content=pins["bot"])

        async def read_first(thread, _headers):
            side = "bot" if thread is threads["bot"] else "user"
            return (FakePin(content=pins[side]) if pins[side] else None), pins_ok

        async def write(thread, header, body):
            side = "bot" if thread is threads["bot"] else "user"
            written[side] = (header, body)
            return SimpleNamespace(id=1), "edited"

        async def raw(prompt, **k):
            prompts.append((prompt, k))
            return replies[min(len(prompts) - 1, len(replies) - 1)]

        with mock.patch("core.context_manager.ensure_context_channel", ctx_channel), \
                mock.patch("core.bot_memory.ensure_bot_memory_channel", bot_channel), \
                mock.patch("core.discord_store.read_pinned_first", read_first), \
                mock.patch("core.discord_store.write_pinned", write), \
                mock.patch.object(cs, "call_groq_raw", raw):
            result = run(cs.fold_person(SimpleNamespace(guilds=[], get_user=lambda _i: None),
                                        SimpleNamespace(id=1, get_member=lambda _i: None),
                                        self.UID, "sevin", **kw))
        return result, written, prompts

    def _chat(self):
        for i in range(8):
            pr.push(self.UID, "user", f"question {i} about yt-dlp")
            pr.push(self.UID, "bot", f"answer {i}")

    def test_both_sides_reach_the_summarizer_and_pins_are_rendered(self):
        self._chat()
        user_ops = json.dumps({"add": [
            {"section": "identity", "text": "Maintains a Discord bot called Eche."},
            {"section": "identity", "text": "Asked how much Dale paid?"},
        ]})
        bot_ops = json.dumps({"add": [{"section": "commitments", "text": "I promised to check the HLS fix."}]})
        result, written, prompts = self._run_fold([user_ops, bot_ops])
        self.assertTrue(result)
        self.assertEqual(len(prompts), 2)
        for prompt, kwargs in prompts:
            self.assertIn("Them: question 0 about yt-dlp", prompt)
            self.assertIn("Eche: answer 0", prompt)
            self.assertEqual(kwargs["temperature"], 0.2)
            self.assertEqual(kwargs["model"], sp.DEFAULT_SUMMARIZER_MODEL)
        self.assertIn("my own outputs", prompts[1][0])
        header, body = written["user"]
        self.assertEqual(header, USER_CONTEXT_HEADER)
        self.assertTrue(body.startswith("Context for sevin:"))
        self.assertIn("- Identity: Maintains a Discord bot called Eche.", body)
        self.assertNotIn("Dale", body)
        self.assertLessEqual(len(body), 1900)
        self.assertEqual(written["bot"][0], BOT_CONTEXT_HEADER)
        self.assertIn("I promised to check the HLS fix.", written["bot"][1])
        self.assertEqual(pr.pending_count(self.UID, "user"), 0)
        self.assertEqual(pr.pending_count(self.UID, "bot"), 0)
        self.assertIsNotNone(ms.load(self.UID, "user"))

    def test_unusable_reply_keeps_everything(self):
        self._chat()
        result, written, _prompts = self._run_fold(["I can't help with that.", "ERROR: rate limit"])
        self.assertFalse(result)
        self.assertEqual(pr.pending_count(self.UID, "user"), 8)
        self.assertNotIn("user", written)

    def test_old_pin_is_migrated_once_with_backup(self):
        self._chat()
        old_user = (
            "Context for sevin:\nSummary:\n- Likes Linux.\n- Asked how much Dale, Dan, and Tony paid.\n"
        )
        old_bot = f"{OLD_BOT_CONTEXT_HEADER}\nSummary:\n- I called them Captain Toast.\n"
        migrate_user = json.dumps({"add": [
            {"section": "likes", "text": "Likes Linux."},
            {"section": "recent", "text": "Asked how much Dale, Dan, and Tony paid."},
        ]})
        fold_user = json.dumps({"touch": ["u1"]})
        migrate_bot = json.dumps({"add": [{"section": "bits", "text": "I call them Captain Toast."}]})
        fold_bot = json.dumps({"add": []})
        result, written, prompts = self._run_fold(
            [migrate_user, fold_user, migrate_bot, fold_bot], user_pin=old_user, bot_pin=old_bot
        )
        self.assertTrue(result)
        self.assertEqual(len(prompts), 4)
        self.assertIn("older memory format", prompts[0][0])
        profile = ms.load(self.UID, "user")
        self.assertEqual(profile["legacy"]["text"], "- Likes Linux.\n- Asked how much Dale, Dan, and Tony paid.")
        self.assertEqual([r["text"] for r in profile["sections"]["likes"]], ["Likes Linux."])
        self.assertEqual(profile["sections"]["likes"][0]["hits"], 2)
        self.assertNotIn("Dale", json.dumps(profile["sections"]))
        # The old "Self Conversation Data" pin keeps its header and still parses.
        self.assertEqual(written["bot"][0], OLD_BOT_CONTEXT_HEADER)
        self.assertIn("I call them Captain Toast.", written["bot"][1])

    def test_unreadable_old_pin_is_not_overwritten(self):
        self._chat()
        result, written, prompts = self._run_fold(['{"add": []}'], pins_ok=False)
        self.assertFalse(result)
        self.assertEqual(prompts, [])
        self.assertIsNone(ms.load(self.UID, "user"))
        self.assertEqual(pr.pending_count(self.UID, "user"), 8)

    def test_builder_reads_the_structured_memory(self):
        from core import builder

        profile = ms.empty("user")
        ms.apply_ops(profile, {"add": [{"section": "identity", "text": "Works nights."}]}, "user")
        ms.save(self.UID, "user", profile)
        prose, rows = run(builder.load_user_context(None, self.UID, "sevin"))
        self.assertEqual(prose, "- Identity: Works nights.")
        self.assertEqual(rows, [])


class FoldRetries(TempRoot):
    """Live 2026-11-23 15:08-16:39 ET: the bot side answered {} seven times;
    each retry applied the user side again from the same messages."""

    UID = 4343
    _run_fold = FoldPerson._run_fold
    _chat = FoldPerson._chat

    def setUp(self):
        super().setUp()
        ms.save(self.UID, "user", ms.empty("user"))
        ms.save(self.UID, "bot", ms.empty("bot"))

    def test_empty_reply_is_no_change(self):
        self.assertEqual(cs.parse_ops("{}"), {})
        self.assertEqual(cs.parse_ops('{"add": [], "kept": []}'), {"add": [], "kept": []})
        self.assertIsNone(cs.parse_ops('{"summary": "x"}'))
        self.assertIsNone(cs.parse_ops("I can't help with that."))
        self._chat()
        user_ops = json.dumps({"add": [{"section": "identity", "text": "Maintains a Discord bot called Eche."}]})
        result, _written, prompts = self._run_fold([user_ops, "{}"])
        self.assertTrue(result)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(pr.pending_count(self.UID, "user"), 0)
        self.assertIn("If nothing should change, reply {}.", prompts[0][0])

    def test_rule_is_in_every_memory_prompt(self):
        self.assertIn("reply {}", sp.build_user_fold_prompt("none", "Them: hi"))
        self.assertIn("reply {}", sp.build_self_fold_prompt("none", "Eche: hi"))

    def test_saved_side_is_not_applied_again(self):
        self._chat()
        user_ops = json.dumps({"add": [{"section": "identity", "text": "Maintains a Discord bot called Eche."}]})
        bot_ops = json.dumps({"add": [{"section": "commitments", "text": "I promised to check the HLS fix."}]})
        result, _w, prompts = self._run_fold([user_ops, "not json"])
        self.assertFalse(result)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(pr.pending_count(self.UID, "user"), 8)
        self.assertEqual(pr.load_fold_state(self.UID)["done"], {"user": [8, 8]})
        # The retry asks only the side that failed.
        result, _w, prompts = self._run_fold([bot_ops])
        self.assertTrue(result)
        self.assertEqual(len(prompts), 1)
        self.assertIn("my own outputs", prompts[0][0])
        self.assertEqual(len(ms.load(self.UID, "user")["sections"]["identity"]), 1)
        self.assertEqual(pr.pending_count(self.UID, "user"), 0)
        self.assertEqual(pr.load_fold_state(self.UID), {"done": {}, "fails": 0, "retry_at": 0.0})

    def test_new_messages_mean_a_new_batch(self):
        self._chat()
        user_ops = json.dumps({"add": [{"section": "identity", "text": "Maintains a Discord bot called Eche."}]})
        self._run_fold([user_ops, "not json"])
        pr.push(self.UID, "user", "one more thing")
        _r, _w, prompts = self._run_fold(["{}", "{}"])
        self.assertEqual(len(prompts), 2)

    def test_backoff_then_drop(self):
        self._chat()
        start = 1_800_000_000.0
        with mock.patch.object(cs.time, "time", return_value=start):
            for attempt in range(1, 3):
                self.assertFalse(self._run_fold(["no", "no"])[0])
                self.assertEqual(pr.load_fold_state(self.UID)["fails"], attempt)
                self.assertEqual(pr.load_fold_state(self.UID)["retry_at"], 0.0)
            self.assertFalse(self._run_fold(["no", "no"])[0])
            state = pr.load_fold_state(self.UID)
            self.assertEqual(state["fails"], 3)
            self.assertEqual(state["retry_at"], start + 3600)
            # While backing off: no model call, no idle/count reason.
            _r, _w, prompts = self._run_fold(["no", "no"])
            self.assertEqual(prompts, [])
            self.assertEqual(cs.fold_reason(self.UID, start + 60), "")
        for attempt in range(4, 6):
            with mock.patch.object(cs.time, "time", return_value=start + attempt * 4000):
                self.assertFalse(self._run_fold(["no", "no"])[0])
            self.assertEqual(pr.pending_count(self.UID, "user"), 8)
        with mock.patch.object(cs.time, "time", return_value=start + 30000):
            self.assertFalse(self._run_fold(["no", "no"])[0])
        self.assertEqual(pr.pending_count(self.UID, "user"), 0)
        self.assertEqual(pr.pending_count(self.UID, "bot"), 0)
        self.assertEqual(pr.load_fold_state(self.UID)["fails"], 0)
        self.assertEqual(ms.load(self.UID, "user"), ms.load(self.UID, "user"))

    def test_owner_repair_ignores_backoff(self):
        self._chat()
        recent_state = {"done": {}, "fails": 3, "retry_at": 9e12}
        pr.save_fold_state(self.UID, recent_state)
        result, _w, prompts = self._run_fold(["{}", "{}"], force=True)
        self.assertTrue(result)
        self.assertEqual(len(prompts), 2)

    def test_push_print_is_debug_only(self):
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            pr.push(self.UID, "user", "hello there")
        self.assertNotIn("since the last summary", out.getvalue())

    def test_wipe_removes_fold_state(self):
        from core import memory_wipe

        pr.save_fold_state(self.UID, {"done": {}, "fails": 2, "retry_at": 0.0})
        memory_wipe.clear_local_memory()
        self.assertFalse(os.path.exists(pr._fold_state_path(self.UID)))


class EpisodeAuthors(TempRoot):
    def test_other_bots_are_not_recorded(self):
        from cogs.events import on_message as om

        calls = []

        async def fake_append(*a, **k):
            calls.append(k.get("author_id"))

        cog = om.OnMessage.__new__(om.OnMessage)
        cog.bot = SimpleNamespace()
        guild = SimpleNamespace(id=1, name="g")
        channel = SimpleNamespace(id=2)
        with mock.patch("core.episodic.append_episodic", fake_append):
            for is_bot, uid in ((True, 10), (False, 11)):
                msg = SimpleNamespace(guild=guild, channel=channel, attachments=[],
                                      author=SimpleNamespace(id=uid, bot=is_bot))
                run(cog._record_server_line(msg, "now playing something"))
        self.assertEqual(calls, [11])


class RememberSide(TempRoot):
    UID = 77

    def test_fold_starts_after_the_reply_when_due(self):
        from core import context_manager as cm

        calls = []

        async def fake_fold(bot, guild, uid, username=None, *, reason="count", force=False):
            calls.append(reason)
            return True

        ms.save(self.UID, "user", ms.empty("user"))
        ms.save(self.UID, "bot", ms.empty("bot"))
        with mock.patch.object(cs, "fold_person", fake_fold), \
                mock.patch.object(cm, "get_home_guild", lambda _b: SimpleNamespace(id=1)):
            for i in range(8):
                run(cm.remember_user(None, SimpleNamespace(id=1), self.UID, "sam", f"m{i}"))
                self.assertEqual(calls, [])
                if i < 7:
                    run(cm.remember_side(None, None, self.UID, "sam", f"r{i}", side="bot"))
            run(cm.remember_side(None, None, self.UID, "sam", "r7", side="bot"))
        self.assertEqual(calls, ["count"])


# ------------------------------------------------------------------ episodes


def _episode_reply(summary="P1 asked about HLS and P2 joked; it got fixed.", salience=0.6, topics=("hls", "music")):
    return json.dumps({"summary": summary, "outcome": "resolved", "tone": "playful", "topics": list(topics), "salience": salience})


class Episodes(TempRoot):
    GUILD = 1183815265402753094

    def _patched(self, replies):
        calls = []

        async def raw(prompt, **k):
            calls.append(prompt)
            return replies[min(len(calls) - 1, len(replies) - 1)]

        return calls, mock.patch("core.client.call_groq_raw", raw)

    def _record(self, text, at, *, author=1, channel=10, reply="ok"):
        run(ep.append_episodic(None, self.GUILD, "Home", [], channel_id=channel, author_id=author,
                               text=text, reply=reply, at=at))

    def setUp(self):
        super().setUp()
        self._mirror = mock.patch.object(ep, "write_mirror", mock.AsyncMock(return_value=True))
        self._mirror.start()

    def tearDown(self):
        self._mirror.stop()
        super().tearDown()

    def test_gap_closes_an_episode_into_a_record(self):
        calls, patch = self._patched([_episode_reply()])
        with patch:
            t = 1_700_000_000.0
            self._record("how do I fix HLS music streaming", t, author=111)
            self._record("the HLS music stream keeps dropping", t + 60, author=222)
            self.assertEqual(ep.read_records(self.GUILD), [])
            self._record("new topic after lunch", t + 25 * 60, author=111)
        records = ep.read_records(self.GUILD)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["participants"], [111, 222])
        self.assertEqual(rec["labels"], {"P1": 111, "P2": 222})
        self.assertEqual(rec["channel"], 10)
        self.assertEqual(rec["turns"], 2)
        self.assertEqual(rec["outcome"], "resolved")
        self.assertEqual(rec["tone"], "playful")
        self.assertTrue(0 <= rec["salience"] <= 1)
        self.assertIn("P1: how do I fix HLS music streaming", calls[0])
        self.assertIn("Eche: ok", calls[0])
        self.assertNotIn("111", calls[0])
        open_state = ep._load_open(self.GUILD)
        self.assertEqual(len(open_state["open"]["10"]["turns"]), 1)

    def test_topic_shift_and_max_turns(self):
        t = 1_700_000_000.0
        episode = {"turns": [], "end": t, "participants": []}
        for i in range(4):
            ep._add_turn(episode, ep._turn(f"music player volume issue {i}", 1, "", [], t + i))
        self.assertFalse(ep.is_boundary(episode, ep._turn("music volume again", 1, "", [], t + 400)))
        self.assertTrue(ep.is_boundary(episode, ep._turn("recommend pizza toppings tonight", 1, "", [], t + 400)))
        # Too soon after the last turn to call it a new topic.
        self.assertFalse(ep.is_boundary(episode, ep._turn("recommend pizza toppings tonight", 1, "", [], t + 30)))
        calls, patch = self._patched([_episode_reply()])
        with patch:
            for i in range(25):
                self._record(f"music player volume issue {i}", t + i, channel=11)
        self.assertEqual(len(ep.read_records(self.GUILD)), 1)
        self.assertNotIn("11", ep._load_open(self.GUILD)["open"])

    def test_failed_summary_retries_then_gives_up(self):
        _calls, patch = self._patched(["not json"])
        t = 1_700_000_000.0
        with patch:
            self._record("hello there friend", t)
            self._record("later", t + 30 * 60)
            self.assertEqual(len(ep._load_open(self.GUILD)["closing"]), 1)
            run(ep.close_pending(None, self.GUILD))
            run(ep.close_pending(None, self.GUILD))
        self.assertEqual(ep._load_open(self.GUILD)["closing"], [])
        self.assertEqual(ep.read_records(self.GUILD), [])

    def test_names_are_never_stored(self):
        _calls, patch = self._patched([_episode_reply(summary="P1 and Tony argued about Dale's bot.", topics=("Tony", "bots"))])
        t = 1_700_000_000.0
        with patch:
            self._record("Tony says the bot is broken", t)
            self._record("later", t + 30 * 60)
        rec = ep.read_records(self.GUILD)[0]
        self.assertNotIn("Tony", rec["summary"])
        self.assertNotIn("Dale", rec["summary"])
        self.assertEqual(rec["topics"], ["bots"])

    def test_links_and_salience_bump(self):
        older = {"id": "ep_old", "start": ep._iso(1000.0), "end": ep._iso(2000.0), "participants": [1],
                 "summary": "P1 fixed HLS music streaming.", "topics": ["hls", "music"], "salience": 0.5}
        new = {"id": "ep_new", "start": ep._iso(3000.0), "end": ep._iso(4000.0), "participants": [1],
               "summary": "P1 asked about HLS music again.", "topics": ["hls", "music"], "salience": 0.5}
        ep.link_related(new, [older])
        self.assertEqual(new["related"], ["ep_old"])
        self.assertEqual(older["related"], ["ep_new"])
        self.assertGreater(older["salience"], 0.5)

    def test_salience_signals(self):
        quiet = {"turns": [{"text": "hi"}], "participants": [1]}
        busy = {"turns": [{"text": "please remember this, I LOVE it!!", "tools": ["duckduckgo"]}] * 12, "participants": [1, 2]}
        self.assertLess(ep.score_salience(0.2, quiet), ep.score_salience(0.2, busy))
        self.assertLessEqual(ep.score_salience(5, busy), 1.0)
        self.assertGreaterEqual(ep.score_salience("junk", quiet), 0.0)

    def _store(self, records):
        ep.write_records(self.GUILD, records)

    def _rec(self, rid, days_ago, salience, topics, participants=(1,), channel=10, now=None, level=0, summary=None):
        now = now or time.time()
        end = now - days_ago * DAY
        return {"id": rid, "level": level, "guild": self.GUILD, "channel": channel, "start": ep._iso(end - 600),
                "end": ep._iso(end), "participants": list(participants),
                "labels": {f"P{i + 1}": p for i, p in enumerate(participants)}, "turns": 4,
                "summary": summary or f"P1 talked about {' and '.join(topics)}.", "outcome": "resolved",
                "tone": "calm", "topics": list(topics), "salience": salience, "related": [], "sources": []}

    def test_retrieval_ranks_and_fits(self):
        now = time.time()
        self._store([
            self._rec("a", 1, 0.3, ["cooking", "pasta"], participants=(5,), now=now),
            self._rec("b", 30, 0.9, ["music", "streaming"], participants=(5,), now=now),
            self._rec("c", 2, 0.5, ["music", "volume"], participants=(9,), channel=10, now=now),
            self._rec("d", 1, 0.2, ["weather"], participants=(5,), now=now),
        ])
        text = ep.recall(self.GUILD, speaker_id=9, channel_id=10, message="the music volume is too low", now=now)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("- "))
        self.assertIn("this person talked about music and volume", lines[0])
        self.assertIn(" here:", lines[0])
        self.assertLessEqual(len(lines), 3)
        self.assertNotIn("\n\n", text)
        self.assertLessEqual(len(ep.recall(self.GUILD, 9, 10, "music", limit=120, now=now)), 120)
        self.assertLessEqual(len(text), 600)

    def test_nightly_consolidation(self):
        now = time.time()
        self._store([
            self._rec("a", 9, 0.3, ["music", "streaming"], now=now),
            self._rec("b", 9, 0.7, ["music", "streaming", "hls"], participants=(1, 2), now=now),
            self._rec("c", 1, 0.3, ["music"], now=now),
        ])
        calls, patch = self._patched([json.dumps({"summary": "P1 and P2 fixed music streaming over a few days.",
                                                  "outcome": "resolved", "tone": "relieved", "topics": ["music"]})])
        with patch:
            made = run(ep.consolidate(None, self.GUILD, now=now))
        self.assertEqual(made, 1)
        records = {r["id"]: r for r in ep.read_records(self.GUILD)}
        self.assertNotIn("a", records)  # low salience original is gone
        self.assertIn("b", records)  # high salience original stays, pointing at the summary
        self.assertIn("c", records)  # too new
        weekly = [r for r in records.values() if r.get("level") == 1]
        self.assertEqual(len(weekly), 1)
        self.assertEqual(records["b"]["consolidated_into"], weekly[0]["id"])
        self.assertEqual(sorted(weekly[0]["sources"]), ["a", "b"])
        self.assertEqual(weekly[0]["participants"], [1, 2])
        self.assertIn("P2", calls[0])  # labels are rebuilt for the cluster
        self.assertGreater(ep.consolidated_at(self.GUILD), 0)

    def test_failed_consolidation_changes_nothing(self):
        now = time.time()
        rows = [self._rec("a", 9, 0.3, ["music"], now=now), self._rec("b", 9, 0.4, ["music"], now=now)]
        self._store(rows)
        _calls, patch = self._patched(["ERROR: down"])
        with patch:
            self.assertEqual(run(ep.consolidate(None, self.GUILD, now=now)), 0)
        self.assertEqual([r["id"] for r in ep.read_records(self.GUILD)], ["a", "b"])

    def test_mirror_fits_the_pin(self):
        now = time.time()
        self._store([self._rec(f"r{i}", i, 0.5, ["topic"], now=now, summary="P1 " + "talked a lot. " * 20) for i in range(30)])
        text = ep.mirror_text(ep.read_records(self.GUILD), 1800, now=now)
        self.assertLessEqual(len(text), 1800)
        self.assertNotRegex(text, r"\bP\d+\b")


class EpisodeMigration(TempRoot):
    GUILD = 1083466447164018738

    def test_old_pin_becomes_history_with_backup(self):
        folder = os.path.join(self.root, "memories", str(self.GUILD))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "recent.txt"), "w", encoding="utf-8") as fh:
            fh.write("2026-11-20 past Dialog: hi\n")
        pin = SimpleNamespace(content=f"Episodic {self.GUILD}: Home\nSummary:\n- People argued about Tony's music bot.\n- Plans for a movie night.\n")
        thread = SimpleNamespace()

        async def ensure(_bot, _name):
            return thread

        async def read(_thread, _prefix):
            return pin, True

        with mock.patch("core.discord_store.ensure_bot_thread", ensure), \
                mock.patch("core.discord_store.read_pinned", read):
            self.assertTrue(run(ep.migrate_if_needed(SimpleNamespace(guilds=[]), self.GUILD)))
            self.assertFalse(run(ep.migrate_if_needed(SimpleNamespace(guilds=[]), self.GUILD)))
        records = ep.read_records(self.GUILD)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["level"], 2)
        self.assertIn("movie night", records[0]["summary"])
        self.assertNotIn("Tony", records[0]["summary"])
        with open(os.path.join(folder, "episodes_legacy.json"), encoding="utf-8") as fh:
            backup = json.load(fh)
        self.assertIn("Tony", backup["pin"])
        self.assertEqual(backup["recent"], ["2026-11-20 past Dialog: hi"])
        self.assertFalse(os.path.isfile(os.path.join(folder, "recent.txt")))

    def test_unreadable_pin_is_tried_again(self):
        async def ensure(_bot, _name):
            return SimpleNamespace()

        async def read(_thread, _prefix):
            return None, False

        with mock.patch("core.discord_store.ensure_bot_thread", ensure), \
                mock.patch("core.discord_store.read_pinned", read):
            self.assertFalse(run(ep.migrate_if_needed(SimpleNamespace(guilds=[]), self.GUILD)))
        self.assertFalse(ep._meta(self.GUILD).get("migrated"))


# ------------------------------------------------------------------ maintenance


class Maintenance(TempRoot):
    def test_consolidation_due(self):
        from core import memory_maintenance as mm
        from datetime import datetime

        night = datetime(2026, 11, 23, 5, 0).timestamp()
        self.assertTrue(mm.consolidation_due(0, night))
        self.assertFalse(mm.consolidation_due(night - 3600, night))
        self.assertTrue(mm.consolidation_due(datetime(2026, 11, 22, 5, 0).timestamp(), night))
        early = datetime(2026, 11, 23, 2, 0).timestamp()
        self.assertFalse(mm.consolidation_due(datetime(2026, 11, 22, 5, 0).timestamp(), early))

    def test_sweep_folds_idle_people(self):
        from core import memory_maintenance as mm

        pr.push(31, "user", "hi")
        pr.push(32, "user", "hi")
        seen = []

        async def fake_fold(bot, guild, uid, username=None, *, reason="count", force=False):
            seen.append((uid, reason))
            return True

        later = time.time() + 31 * 60
        with mock.patch.object(cs, "fold_person", fake_fold), \
                mock.patch("core.context_manager.get_home_guild", lambda _b: None):
            report = run(mm.sweep(SimpleNamespace(guilds=[]), now=later))
        self.assertEqual(sorted(seen), [(31, "idle"), (32, "idle")])
        self.assertEqual(report["folds"], 2)


if __name__ == "__main__":
    unittest.main()
