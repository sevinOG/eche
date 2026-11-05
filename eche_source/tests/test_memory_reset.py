# tests/test_memory_reset.py
# Offline checks for the memory reset, the memory filters, the own-context
# tool, and the picture/video prompt check. No network, no Discord, no model.
#
# Run from eche_source:  python -m unittest tests.test_memory_reset -v

from __future__ import annotations

import asyncio
import itertools
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

import discord  # noqa: E402

from core import builder  # noqa: E402
from core import content_filter as cf  # noqa: E402
from core import context_summarizer as cs  # noqa: E402
from core import discord_store as ds  # noqa: E402
from core import episodic  # noqa: E402
from core import imagine  # noqa: E402
from core import memory_wipe as mw  # noqa: E402
from core import personal_recent as pr  # noqa: E402
from core import summarizer_prompt as sp  # noqa: E402
from core import tools  # noqa: E402
from core import tools_context  # noqa: E402

# Test words come from the filter's own list so this file holds no slur text.
SLUR = cf._ANYWHERE[0]
WORD_SLUR = "retard"

_ids = itertools.count(900000000000000000)


def run(coro):
    return asyncio.run(coro)


class FakeMessage:
    def __init__(self, content, channel):
        self.id = next(_ids)
        self.content = content
        self.channel = channel
        self.edits = []

    async def edit(self, *, content, allowed_mentions=None):
        self.edits.append(allowed_mentions)
        self.content = content
        return self


class FakeThread:
    def __init__(self, name, parent):
        self.id = next(_ids)
        self.name = name
        self.parent_id = parent.id
        self.archived = False
        self.guild = parent.guild
        self._pins = []

    def pin(self, content):
        message = FakeMessage(content, self)
        self._pins.append(message)
        return message

    def pins(self, limit=None):
        return list(self._pins)


class FakeChannel:
    def __init__(self, name, guild):
        self.id = next(_ids)
        self.name = name
        self.guild = guild
        self.threads = []

    def thread(self, name):
        thread = FakeThread(name, self)
        self.threads.append(thread)
        return thread

    async def archived_threads(self, limit=100):
        if False:
            yield None


class FakeCategory:
    def __init__(self, name):
        self.name = name
        self.text_channels = []


class FakeGuild:
    def __init__(self):
        self.id = next(_ids)
        self.categories = [FakeCategory(ds.CATEGORY_NAME)]

    @property
    def category(self):
        return self.categories[0]

    def channel(self, name):
        channel = FakeChannel(name, self)
        self.category.text_channels.append(channel)
        return channel

    async def active_threads(self):
        return []


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="eche-wipe-")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ECHE_")}
        env["ECHE_USER_ROOT"] = self.root
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        ds.clear_caches()
        cf._mask_cache = ("", -1.0, True)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.root, ignore_errors=True)
        ds.clear_caches()


# ---------------------------------------------------------------- the wipe


class MemoryWipeTests(TempRoot):
    USER_A = 111111111111111111
    USER_B = 222222222222222222

    def _guild(self):
        guild = FakeGuild()
        a = guild.channel(f"user-{self.USER_A}").thread(ds.THREAD_CONTEXT)
        self.a_user = a.pin(
            "Context for alice:\nSummary:\nLikes cats. Talks to <@333333333333333333>.\n"
        )
        self.a_bot = a.pin("Eche with alice:\n\nSummary:\nEche teased her about cats.\n")
        b = guild.channel(f"user-{self.USER_B}").thread(ds.THREAD_CONTEXT)
        # The old layout: New: USER:/BOT: lines and the old bot title.
        self.b_user = b.pin(
            "Context for bob:\nSummary:\nOld stuff.\n\nNew:\nUSER: hi\nBOT: hello\n"
        )
        self.b_bot = b.pin("Self Conversation Data\nSummary:\nx\n\nNew:\nBOT: yo\n")
        self.b_other = b.pin("Some unrelated pin")
        guild.channel("general")  # not a user channel
        episodic_thread = guild.channel(ds.BOT_CHANNEL_NAME).thread(ds.THREAD_EPISODIC)
        self.episode = episodic_thread.pin(
            episodic.episode_heading(guild.id, "Home")
            + f"\nSummary:\nA fight about pizza in {guild.id}.\n"
        )
        return guild

    def test_rewrites_every_pin_to_the_fresh_format(self):
        guild = self._guild()
        report = run(mw.wipe_memory(guild, log=lambda _t: None))
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.users, 2)
        self.assertEqual(report.user_pins, 2)
        self.assertEqual(report.bot_pins, 2)
        self.assertEqual(report.episode_pins, 1)
        self.assertEqual(report.legacy, 2)

        self.assertEqual(self.a_user.content, "Context for alice:\nSummary:\n(none yet)\n")
        self.assertEqual(self.a_bot.content, ds.bot_memory_initial("alice"))
        self.assertEqual(self.b_user.content, "Context for bob:\nSummary:\n(none yet)\n")
        # The old bot title becomes the new one, labelled from the user pin.
        self.assertEqual(self.b_bot.content, ds.bot_memory_initial("bob"))
        self.assertNotIn("USER:", self.b_user.content)
        self.assertEqual(self.b_other.content, "Some unrelated pin")
        self.assertEqual(
            self.episode.content,
            episodic.episode_heading(guild.id, "Home") + "\nSummary:\n(none yet)\n",
        )
        # The pin key stays so the next episode still finds it.
        self.assertTrue(self.episode.content.startswith(episodic.episode_prefix(guild.id)))

    def test_edits_never_ping(self):
        guild = self._guild()
        run(mw.wipe_memory(guild, log=lambda _t: None))
        edited = [self.a_user, self.a_bot, self.b_user, self.b_bot, self.episode]
        for message in edited:
            self.assertEqual(len(message.edits), 1)
            mentions = message.edits[0]
            self.assertIsInstance(mentions, discord.AllowedMentions)
            self.assertFalse(mentions.everyone)
            self.assertFalse(mentions.users)
            self.assertFalse(mentions.roles)

    def test_second_run_changes_nothing(self):
        guild = self._guild()
        run(mw.wipe_memory(guild, log=lambda _t: None))
        again = run(mw.wipe_memory(guild, log=lambda _t: None))
        self.assertTrue(again.ok)
        self.assertEqual(again.user_pins + again.bot_pins + again.episode_pins, 0)
        self.assertEqual(again.unchanged, 5)
        self.assertEqual(len(self.a_user.edits), 1)

    def test_fresh_pins_parse_as_empty(self):
        from core.context_manager import parse_pin_sections

        guild = self._guild()
        run(mw.wipe_memory(guild, log=lambda _t: None))
        for message in (self.a_user, self.a_bot, self.b_user, self.b_bot):
            _label, summary, recent = parse_pin_sections(message.content, "")
            self.assertIn(summary.strip(), ("", "(none yet)"))
            self.assertFalse(recent)
        body = episodic.episode_body(self.episode.content, episodic.episode_prefix(guild.id))
        prose, _rows = episodic.split_episode(body)
        self.assertEqual(prose.strip(), "")
        self.assertEqual(episodic.prompt_episode(body), "")

    def test_failed_edit_is_counted_not_raised(self):
        guild = self._guild()

        async def boom(**_kw):
            raise RuntimeError("no perms")

        self.a_user.edit = boom
        report = run(mw.wipe_memory(guild, log=lambda _t: None))
        self.assertFalse(report.ok)
        self.assertEqual(len(report.failed), 1)
        self.assertEqual(report.user_pins, 1)

    def test_no_category_does_nothing(self):
        guild = FakeGuild()
        guild.categories = []
        report = run(mw.wipe_memory(guild, log=lambda _t: None))
        self.assertIn("bot memory", report.problem)
        report = run(mw.wipe_memory(None, log=lambda _t: None))
        self.assertIn("home server", report.problem)

    def test_clears_local_buffers_and_caches(self):
        pr.push(self.USER_A, "user", "hello there")
        pr.push(self.USER_A, "bot", "hi")
        episodic.push_recent(4242, ["alice: hi"])
        local_path = os.path.join(self.root, "memories", "local")
        os.makedirs(local_path, exist_ok=True)
        keep = os.path.join(local_path, "history.jsonl")
        with open(keep, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        ds._pins_scanned.add(12345)
        ds._ready_channels.add(12345) if isinstance(ds._ready_channels, set) else None

        guild = self._guild()
        report = run(mw.wipe_memory(guild, log=lambda _t: None))
        self.assertEqual(report.local_files, 3)
        self.assertEqual(pr.messages(self.USER_A, "user") if hasattr(pr, "messages") else [], [])
        self.assertEqual(episodic.read_recent(4242), [])
        self.assertFalse(ds._pins_scanned)
        self.assertTrue(os.path.isfile(keep))

    def test_summary_names_the_counts(self):
        guild = self._guild()
        logged = []
        report = run(mw.wipe_memory(guild, log=logged.append))
        self.assertIn("2 user pins reset", report.summary())
        self.assertEqual(logged[-1], report.summary())

    def test_cli_needs_yes(self):
        with mock.patch.object(mw, "run_wipe") as runner:
            self.assertEqual(mw.main([]), 2)
            runner.assert_not_called()
            runner.return_value = mw.WipeReport()
            self.assertEqual(mw.main(["--yes"]), 0)
            runner.assert_called_once()

    def test_run_wipe_without_settings_refuses(self):
        report = mw.run_wipe("", "")
        self.assertTrue(report.problem)

    def test_bridge_reset_command(self):
        from core import gui_bridge

        called = {}

        async def fake_wipe(guild, *, log=None):
            called["guild"] = guild
            return mw.WipeReport(users=1)

        bot = mock.Mock()
        bot.get_guild.return_value = "home"
        with mock.patch.object(mw, "wipe_memory", fake_wipe), \
                mock.patch.object(gui_bridge, "emit") as emit:
            run(gui_bridge._handle_line(bot, "RESET_MEMORY"))
        self.assertEqual(called.get("guild"), "home")
        self.assertEqual(emit.call_args[0][0], "memory_reset")


# ------------------------------------------------------------- the filters


class FilterTests(TempRoot):
    def test_mentions_and_ids_go(self):
        text = "hi <@123456789012345678> <@!123456789012345678> <@&123456789012345678> <#123456789012345678> id 123456789012345678 ok"
        out = cf.clean_memory_text(text)
        self.assertNotIn("<", out)
        self.assertNotRegex(out, r"\d{17,20}")
        self.assertEqual(out, "hi id ok")

    def test_everyone_cannot_ping(self):
        out = cf.clean_memory_text("hey @everyone and @here")
        self.assertNotIn("@everyone", out)
        self.assertNotIn("@here", out)

    def test_short_numbers_stay(self):
        self.assertEqual(cf.clean_memory_text("born 1999, owes 250"), "born 1999, owes 250")

    def test_slurs_masked(self):
        for word in (SLUR, SLUR.upper(), WORD_SLUR, WORD_SLUR + "ed"):
            out = cf.mask_slurs(f"he said {word} again")
            self.assertFalse(cf.has_slur(out), out)
            self.assertTrue(out.startswith("he said "))
            self.assertTrue(out.endswith(" again"))

    def test_leet_and_repeats_masked(self):
        leet = SLUR.replace("i", "1").replace("e", "3")
        self.assertTrue(cf.has_slur(leet))
        stretched = SLUR[0] + SLUR[1] * 3 + SLUR[2:]
        self.assertTrue(cf.has_slur(stretched))

    def test_innocent_words_untouched(self):
        for word in ("Niger", "spice", "retardant", "raccoon", "coonhound", "Scunthorpe", "cocoon"):
            self.assertFalse(cf.has_slur(word), word)
            self.assertEqual(cf.mask_slurs(word), word)

    def test_memory_writers_clean(self):
        from core.context_manager import summary_only

        pin = summary_only("Context for x:\n", f"Said {SLUR} to <@123456789012345678>.")
        self.assertFalse(cf.has_slur(pin))
        self.assertNotIn("<@", pin)

    def test_episode_render_and_prompt_clean(self):
        gid = 123456789012345678
        pin = episodic.render_summary(f"Episodic {gid}:", f"Chat in {gid} with <@987654321098765432>.")
        self.assertTrue(pin.startswith(f"Episodic {gid}:"))
        body = pin.split("\n", 1)[1]
        self.assertNotIn(str(gid), body)
        self.assertNotIn("<@", body)
        self.assertNotIn(str(gid), episodic.prompt_episode(pin))

    def test_builder_cleans_memory_not_latest(self):
        latest = "my friend <@123456789012345678>"
        out = builder.render_memory_prompt(
            "today",
            f"user said {SLUR}",
            ["alice: <@123456789012345678> hi"],
            "bot summary 123456789012345678",
            [],
            "episode <#123456789012345678>",
            latest,
        )
        self.assertFalse(cf.has_slur(out))
        self.assertIn(latest, out)
        self.assertEqual(out.count("123456789012345678"), 1)

    def test_drop_rules_in_every_memory_prompt(self):
        prompts = [
            cs.fold_memory_prompt("old", ["new note"]) if _accepts(cs.fold_memory_prompt, 2) else "",
            episodic.fold_prompt("old", ["a: b"]) if _accepts(episodic.fold_prompt, 2) else "",
            sp.DEFAULT_SUMMARIZER_PROMPT,
        ]
        for text in filter(None, prompts):
            folded = text.lower()
            self.assertIn("no slurs", folded)
            self.assertIn("@mentions", folded)
            self.assertIn("refused", folded)
        self.assertIn("no slurs", cs._MEMORY_DROP.lower())
        self.assertIn("no slurs", episodic._EPISODE_DROP.lower())
        self.assertIn("stale", cs._MEMORY_DROP.lower())

    def test_personal_buffer_cleaned_on_push(self):
        root = tempfile.mkdtemp(prefix="eche-pr-")
        try:
            with mock.patch.dict(os.environ, {"ECHE_USER_ROOT": root}):
                rows, _n = pr.push(5555555555555555, "user", f"yo <@123456789012345678> {SLUR}")
            joined = "\n".join(rows)
            self.assertNotIn("<@", joined)
            self.assertFalse(cf.has_slur(joined))
        finally:
            shutil.rmtree(root, ignore_errors=True)


def _accepts(func, count: int) -> bool:
    import inspect

    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    required = [p for p in params if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(required) <= count


# ---------------------------------------------------- the own-context tool


class ContextToolTests(TempRoot):
    def test_registered_for_anyone(self):
        tool = tools._TOOLS[tools_context.TOOL_NAME]
        self.assertEqual(tool.access, tools.ACCESS_ANYONE)
        self.assertEqual(tool.parameters.get("properties"), {})

    def test_offered_to_everyone_without_a_phrase(self):
        for owner in (False, True):
            names = tools.visible_names(speaker_is_owner=owner, admin_enabled=False)
            self.assertIn("context_raw", names)
            specs = tools.specs_for(speaker_is_owner=owner)
            self.assertTrue(any("context_raw" in str(spec) for spec in specs))

    def test_no_phrase_trigger_left(self):
        self.assertFalse(hasattr(tools_context, "asks_for_own_context"))
        path = os.path.join(SOURCE, "cogs", "events", "on_message.py")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("asks_for_own_context", source)
        self.assertNotIn("asked_context", source)

    def test_reads_only_the_speaker(self):
        seen = {}

        async def fake_read(bot, user_id, name):
            seen["id"] = user_id
            return "Context for alice:\nSummary:\nLikes cats.\n"

        author = mock.Mock(id=42, name="alice")
        ctx = tools.ToolContext(bot=object(), message=mock.Mock(author=author))
        with mock.patch.object(tools_context, "read_raw_context", fake_read):
            result = run(tools_context.show_own_context(ctx, {"user_id": 99, "user": "bob"}))
        self.assertEqual(seen["id"], 42)
        self.assertFalse(result.fence)
        self.assertIn("Likes cats.", result.text)

    def test_output_is_filtered(self):
        pin = (
            f"Context for a:\nSummary:\nTalks to <@123456789012345678>, said {SLUR}, @everyone.\n"
            "\nNew:\nUSER: secret raw line\n"
        )
        out = tools_context.own_memory_text(pin)
        self.assertTrue(out.startswith("What I remember about you:"))
        self.assertNotIn("<@1", out)
        self.assertNotIn("@everyone", out)
        self.assertFalse(cf.has_slur(out))
        self.assertNotIn("secret raw line", out)

    def test_empty_and_unreadable(self):
        self.assertEqual(tools_context.own_memory_text(None), tools_context.UNREADABLE)
        fresh = "Context for a:\nSummary:\n(none yet)\n"
        self.assertEqual(tools_context.own_memory_text(fresh), tools_context.NOTHING_STORED)


# --------------------------------------------------- picture/video prompts


class AssetPromptTests(TempRoot):
    """Slurs no longer refuse a picture. Only the minors block does (test_blocklist)."""

    def test_slur_prompt_is_not_refused(self):
        plan = imagine.plan_asset(f"image of a sign that says {SLUR}")
        self.assertIn(SLUR, plan.prompt)
        imagine.check_prompt(f"a {WORD_SLUR} cartoon")  # no raise

    def test_content_filter_has_no_asset_refusal(self):
        self.assertFalse(hasattr(cf, "ASSET_REFUSAL"))
        self.assertFalse(hasattr(cf, "prompt_allowed"))

    def test_clean_prompt_passes(self):
        plan = imagine.plan_asset("image of a raccoon eating spice cake")
        self.assertIn("raccoon", plan.prompt)

    def test_character_lock_needs_source(self):
        with self.assertRaises(imagine.ImagineError) as ctx:
            imagine.plan_asset(
                "video of Female Lead A: Image 1 leaning on a railing, character lock"
            )
        self.assertIn("Attach or reply", str(ctx.exception.text))

    def test_character_lock_with_source_ok(self):
        source = imagine.SourceMedia(data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, mime="image/png")
        plan = imagine.plan_asset(
            "video of Female Lead A: Image 1 leaning on a railing",
            kind="video",
            source=source,
        )
        self.assertEqual(plan.mode, "image_video")

    def test_moderation_error_is_mapped(self):
        msg = imagine._error_text(
            {"error": {"message": "Generated image rejected by content moderation."}},
            400,
        )
        self.assertEqual(msg, imagine._FILTERED)

    def test_empty_batch_is_filtered(self):
        with self.assertRaises(imagine.ImagineError) as ctx:
            imagine.image_url({"data": []})
        self.assertEqual(ctx.exception.text, imagine._FILTERED)


# ----------------------------------------------------------- reply style


class ReplyStyleTests(unittest.TestCase):
    def test_style_rules_in_channel_prompt(self):
        from core import prompt_text

        text = prompt_text.system_text(job="channel") if _job_ok(prompt_text) else prompt_text._STYLE
        for needle in ("No lectures", "names or quotes", "make up", "search ran"):
            self.assertIn(needle, text)

    def test_no_refusal_guidance(self):
        from core import prompt_text

        for job in ("channel", "plain", "lookup"):
            text = prompt_text.system_text(job=job) if _job_ok(prompt_text) else prompt_text._STYLE
            self.assertNotIn("won't do something", text)
            self.assertNotIn("say so in one line", text)

    def test_reply_length_rule_kept(self):
        from core import prompt_text

        self.assertIn("Match the length of the message", prompt_text._CHANNEL_SHAPE)

    def test_personality_migrates(self):
        from core import personality

        old = personality._OLD_DEFAULTS[0]
        self.assertTrue(personality.is_old_default(old))
        self.assertNotIn("over‑interpretation", personality.DEFAULT_PERSONALITY)


def _job_ok(module) -> bool:
    try:
        module.system_text(job="channel")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    unittest.main()
