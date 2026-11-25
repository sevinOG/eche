"""A clip addressed to Eche is watched before the model answers (live 2026-11-25 15:42-15:44 ET).

Scenario 1: a reply to Eche with a video was answered from an older caption ("hamburger
flavored Fanta"); watch_video never ran. Scenario 2: "Watch a video? I don't have eyes, boss...
use the asset tool on it first". Scenario 3: the model typed "[Watched the video from this
message] Okay, I'm watching it now. It's a 25-second clip." with nothing run.

Mocks only: no real clip, frame, picture or model is used.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from tests.test_refinement import ModelTurn, TempRoot, run  # noqa: E402
from core import builder  # noqa: E402
from core import tools as T  # noqa: E402
from core import tools_watch as W  # noqa: E402
from core import watch_auto as A  # noqa: E402

BOT_ID = 1
NOTES = (
    "Video, 0:25 long, with sound.\n[0:00] A can of hamburger flavored Fanta is opened.\n"
    "[0:12] A man takes a sip and spits it out.\nHeard: [0:13] \"that's disgusting\""
)


def video(name="clip.mp4", kind="video/mp4"):
    return SimpleNamespace(id=555, filename=name, content_type=kind, size=1000, url="https://cdn.invalid/x")


def picture():
    return SimpleNamespace(id=556, filename="pic.png", content_type="image/png", size=1000, url="https://cdn.invalid/p")


def author(uid):
    return SimpleNamespace(id=uid, name=f"u{uid}", display_name=f"U{uid}", bot=uid == BOT_ID)


def message(text, attachments=(), replied=None):
    ref = None
    if replied is not None:
        ref = SimpleNamespace(resolved=replied, message_id=replied.id)
    return SimpleNamespace(
        id=9001,
        content=text,
        author=author(42),
        channel=SimpleNamespace(id=99, send=mock.AsyncMock(), history=None, fetch_message=None),
        guild=SimpleNamespace(id=7, name="srv"),
        attachments=list(attachments),
        embeds=[],
        message_snapshots=[],
        reference=ref,
        mentions=[],
        to_reference=lambda **_kw: None,
    )


def eche_post(text="hamburger flavored Fanta", attachments=()):
    return SimpleNamespace(
        id=8000, content=text, author=author(BOT_ID), attachments=list(attachments), embeds=[],
        message_snapshots=[],
    )


class ChatTurn(TempRoot):
    """Drives OnMessage._answer_and_remember with the model and the watch mocked."""

    watch_tools = {"watch_video", "web_search"}

    def setUp(self):
        super().setUp()
        from cogs.events import on_message as om

        self.om = om
        self.cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=BOT_ID)))
        self.sent: list[str] = []
        self.watch_calls = 0
        self.watch_text = NOTES

        async def fake_send(_message, content):
            self.sent.append(content)

        async def fake_find(msg, wanted="", stats=None):
            refs = W._video_refs(msg)
            if not refs:
                replied = getattr(getattr(msg, "reference", None), "resolved", None)
                refs = W._video_refs(replied) if replied is not None else []
            if not refs:
                return b"", "", "", W.NO_VIDEO
            return b"fake-clip-bytes", "video/mp4", refs[0][0], ""

        async def fake_watch(data, mime, ids, notes=None):
            self.watch_calls += 1
            return self.watch_text

        self.first = mock.AsyncMock(return_value=ModelTurn(reply="Hamburger flavored Fanta, wild."))
        self.lookup = mock.AsyncMock(return_value=ModelTurn(reply="He tries hamburger Fanta and spits it out at 0:12."))
        self.patches = [
            mock.patch.object(om, "_send_reply", fake_send),
            mock.patch.object(om, "build_prompt", mock.AsyncMock(return_value="PROMPT about hamburger flavored Fanta")),
            mock.patch.object(om, "author_is_owner", mock.AsyncMock(return_value=False)),
            mock.patch.object(om, "admin_tools_enabled", lambda: False),
            mock.patch.object(om, "specs_for", lambda **_kw: [
                {"type": "function", "function": {"name": n, "description": "", "parameters": {}}}
                for n in sorted(self.watch_tools)
            ]),
            mock.patch.object(om, "visible_names", lambda **_kw: set(self.watch_tools)),
            mock.patch.object(om, "remember_user", mock.AsyncMock()),
            mock.patch.object(om, "remember_bot", mock.AsyncMock()),
            mock.patch.object(om, "call_groq_turn", self.first),
            mock.patch("core.client.call_groq_turn", self.lookup),
            mock.patch("core.announce.announce_tool", mock.AsyncMock(return_value=True)),
            mock.patch.object(T, "default_tools_enabled", lambda: True),
            mock.patch.object(T, "tool_prompt_enabled", lambda _name: True),
            mock.patch.object(W, "find_ffmpeg", lambda: "/usr/bin/ffmpeg"),
            mock.patch.object(W, "find_video", fake_find),
            mock.patch.object(W, "watch", fake_watch),
            mock.patch.object(self.cog, "_record_server_line", mock.AsyncMock()),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        super().tearDown()

    def answer(self, msg, media=None):
        media = media or SimpleNamespace(text="", images=[], summary="", memory="", reply_images=[])
        run(self.cog._answer_and_remember(msg, None, msg.content, msg.content, media))

    def lookup_prompt(self) -> str:
        return self.lookup.call_args.args[0]


class Scenario1ReplyWithVideo(ChatTurn):
    def test_reply_to_eche_with_a_video_is_watched_and_answered_from_the_notes(self):
        self.answer(message("lmao look", [video()], replied=eche_post()))
        self.first.assert_not_called()          # the model does not get to skip the watch
        self.assertEqual(self.watch_calls, 1)
        prompt = self.lookup_prompt()
        self.assertIn("Watch notes", prompt)
        self.assertIn("spits it out", prompt)
        self.assertIn("You watched it", prompt)
        self.assertEqual(self.sent, ["He tries hamburger Fanta and spits it out at 0:12."])

    def test_video_on_the_replied_to_message_is_watched(self):
        theirs = SimpleNamespace(id=8001, content="", author=author(43), attachments=[video()], embeds=[],
                                 message_snapshots=[])
        msg = message("<@1> what's going on here", replied=theirs)
        self.answer(msg)
        self.first.assert_not_called()
        self.assertEqual(self.watch_calls, 1)

    def test_audio_clip_counts(self):
        self.answer(message("<@1> what does he say", [video("voice-message.ogg", "audio/ogg")]))
        self.first.assert_not_called()
        self.assertTrue(self.lookup.called or self.sent)

    def test_toggle_off_means_no_auto_watch(self):
        self.watch_tools = {"web_search"}
        self.answer(message("lmao look", [video()], replied=eche_post()))
        self.first.assert_called_once()
        self.assertEqual(self.watch_calls, 0)

    def test_asset_edit_of_a_clip_is_not_hijacked(self):
        self.answer(message("<@1> turn this video into anime", [video()]))
        self.first.assert_called_once()
        self.assertEqual(self.watch_calls, 0)

    def test_tool_row_is_audited_through_execute(self):
        with mock.patch.object(T, "_audit_tool") as audit:
            self.answer(message("lmao look", [video()], replied=eche_post()))
        names = [c.args[0] for c in audit.call_args_list]
        self.assertIn("watch_video", names)


class Scenario2NoEyes(ChatTurn):
    def test_watch_this_video_runs_the_watch_instead_of_a_denial(self):
        self.first.return_value = ModelTurn(reply="Watch a video? I don't have eyes, boss... use the asset tool on it first")
        theirs = SimpleNamespace(id=8001, content="", author=author(43), attachments=[video()], embeds=[],
                                 message_snapshots=[])
        self.answer(message("<@1> watch this video", replied=theirs))
        self.first.assert_not_called()
        self.assertEqual(self.watch_calls, 1)
        self.assertNotIn("eyes", " ".join(self.sent))

    def test_denial_after_watching_falls_back_to_the_notes(self):
        self.lookup.return_value = ModelTurn(reply="I don't have eyes, boss.")
        self.answer(message("lmao look", [video()], replied=eche_post()))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith("Here's what I got from it:"))
        self.assertIn("spits it out", self.sent[0])
        self.assertNotIn("eyes", self.sent[0])

    def test_denial_with_no_clip_is_replaced_when_watching_is_on(self):
        self.first.return_value = ModelTurn(reply="Watch a video? I don't have eyes, boss... use the asset tool on it first")
        self.answer(message("<@1> can you even see stuff"))
        self.first.assert_called_once()
        self.assertEqual(self.sent, [A.CAN_WATCH])

    def test_denial_is_left_alone_when_watching_is_off(self):
        self.watch_tools = set()
        self.first.return_value = ModelTurn(reply="I can't watch videos here.")
        self.answer(message("<@1> can you watch videos"))
        self.assertEqual(self.sent, ["I can't watch videos here."])

    def test_memory_prompt_drops_old_denials(self):
        out = builder.render_memory_prompt(
            "Wed", "none", [], "Eche says it doesn't have eyes. Eche likes Fanta.",
            [
                "Watch a video? I don't have eyes, boss... use the asset tool on it first",
                "[Watched the video from this message] Okay, I'm watching it now. It's a 25-second clip.",
                "[I used watch_video] He spits out the Fanta at 0:12.",
                "lol ok",
            ],
            "", "=== MOST RECENT MESSAGE ===\nu42:\nhi",
        )
        self.assertNotIn("have eyes", out)
        self.assertNotIn("[Watched the video", out)
        self.assertNotIn("watching it now", out)
        self.assertIn("[I used watch_video] He spits out the Fanta at 0:12.", out)
        self.assertIn("lol ok", out)
        self.assertIn("Eche likes Fanta.", out)
        self.assertIn("Never write one yourself", out)


class Scenario3PretendWatched(ChatTurn):
    def test_typed_watch_record_without_a_watch_is_not_posted(self):
        self.first.return_value = ModelTurn(
            reply="[Watched the video from this message] Okay, I'm watching it now. It's a 25-second clip."
        )
        self.answer(message("<@1> so?"))  # no clip and no watch ask: nothing is watched
        self.assertEqual(self.watch_calls, 0)
        self.assertEqual(self.sent, [A.NOT_WATCHED])

    def test_did_you_watch_it_with_no_clip_anywhere_says_so(self):
        self.answer(message("<@1> did you watch it"))
        self.first.assert_not_called()
        self.assertEqual(self.sent, [W.NO_VIDEO])

    def test_same_video_again_really_watches_and_strips_markers(self):
        self.lookup.return_value = ModelTurn(
            reply="[Watched the video from this message] A guy opens hamburger Fanta and spits it out at 0:12."
        )
        self.answer(message("<@1> watch it", [video()], replied=eche_post()))
        self.first.assert_not_called()
        self.assertEqual(self.watch_calls, 1)
        self.assertEqual(self.sent, ["A guy opens hamburger Fanta and spits it out at 0:12."])

    def test_stall_after_watching_falls_back_to_the_notes(self):
        self.lookup.return_value = ModelTurn(reply="Okay, I'm watching it now. It's a 25-second clip.")
        self.answer(message("<@1> watch it", [video()], replied=eche_post()))
        self.assertTrue(self.sent[0].startswith("Here's what I got from it:"))

    def test_failed_watch_says_so_plainly(self):
        self.watch_text = ""   # no vision model could see the frames
        self.answer(message("<@1> watch it", [video()], replied=eche_post()))
        self.first.assert_not_called()
        self.lookup.assert_not_called()      # nothing to ground a description on
        self.assertEqual(self.sent, [W.CANT_SEE])

    def test_timeout_says_why(self):
        async def slow(*_a, **_k):
            raise TimeoutError()

        with mock.patch.object(W, "watch", slow):
            self.answer(message("<@1> watch it", [video()], replied=eche_post()))
        self.assertEqual(self.sent, [W.TIMED_OUT])

    def test_watch_that_did_not_run_says_it_could_not_watch(self):
        with mock.patch.object(T, "tool_prompt_enabled", lambda _name: False):
            self.first.return_value = ModelTurn(reply="Okay, I'm watching it now.")
            self.answer(message("<@1> watch it", [video()], replied=eche_post()))
        self.assertEqual(self.watch_calls, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("watching it now", self.sent[0])
        system = self.first.call_args.kwargs.get("extra_system") or ""
        self.assertIn("couldn't watch it", system)


class Planning(unittest.TestCase):
    def plan(self, msg, **kw):
        return run(A.plan(msg, msg.content, bot_user_id=BOT_ID, **kw))

    def test_reply_to_eches_own_clip_without_a_question_is_not_watched(self):
        call, reason = self.plan(message("nice", replied=eche_post(attachments=[video()])))
        self.assertIsNone(call)
        self.assertEqual(reason, "own-clip")

    def test_question_about_eches_own_clip_is_watched(self):
        call, reason = self.plan(message("what's happening in this", replied=eche_post(attachments=[video()])))
        self.assertEqual((call or {}).get("name"), "watch_video")

    def test_plain_picture_question_is_not_a_watch(self):
        call, _ = self.plan(message("what's this", [picture()]))
        self.assertIsNone(call)

    def test_watch_this_with_no_clip_looks_back(self):
        call, reason = self.plan(message("watch this video"))
        self.assertEqual(reason, "asked")

    def test_unrelated_words_about_a_video_do_not_watch(self):
        call, _ = self.plan(message("how should I word this video"))
        self.assertIsNone(call)

    def test_off_never_watches(self):
        call, reason = self.plan(message("lmao", [video()]), offered=False)
        self.assertIsNone(call)


class Cleaning(unittest.TestCase):
    def test_markers_are_stripped(self):
        self.assertEqual(A.strip_action_markers("[Watched the video from this message] cool dog"), "cool dog")
        self.assertEqual(A.strip_action_markers("[I used watch_video] cool"), "cool")
        self.assertEqual(A.strip_action_markers("[0:12] he trips"), "[0:12] he trips")

    def test_denials_and_pretending(self):
        self.assertTrue(A.denies_watching("I don't have eyes, boss"))
        self.assertTrue(A.denies_watching("I can't watch videos"))
        self.assertFalse(A.denies_watching("I can't believe that video"))
        self.assertTrue(A.pretends_watching("Okay, I'm watching it now."))
        self.assertFalse(A.pretends_watching("I watched Dune yesterday"))


if __name__ == "__main__":
    unittest.main()
