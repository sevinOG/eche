"""watch_video: Eche's port of Waerhouse's watch tool. Mock-only.

No real video, pictures or network: frames are placeholder JPEG-marker bytes,
ffmpeg and the chat model are mocked. Nothing here makes a picture or video.
"""
from __future__ import annotations

import asyncio
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from core import client as C
from core import prompt_text as P
from core import tools as T
from core import tools_watch as W
from core.blocklist import REFUSAL

FRAME = b"\xff\xd8placeholder-frame"


def run(c):
    return asyncio.run(c)


class Att:
    def __init__(self, data=b"vid", name="clip.mp4", ctype="video/mp4", size=None):
        self.filename = name
        self.content_type = ctype
        self.size = len(data) if size is None else size
        self.url = "https://cdn.example/a/clip.mp4?ex=1&hm=secret"
        self.read = mock.AsyncMock(return_value=data)


class History:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, limit=None, before=None):
        self.calls.append((limit, before))

        async def gen():
            for row in self.rows[:limit]:
                yield row
        return gen()


def msg(attachments=(), embeds=(), ref=None, history=(), mid=10, fetched=None):
    channel = SimpleNamespace(id=5, history=History(list(history)),
                              fetch_message=mock.AsyncMock(return_value=fetched if fetched is not None else ref))
    return SimpleNamespace(
        id=mid, content="what happens in this", attachments=list(attachments), embeds=list(embeds),
        reference=(SimpleNamespace(resolved=ref, message_id=ref.id) if ref else None),
        channel=channel, author=SimpleNamespace(id=7), guild=SimpleNamespace(id=8),
    )


def described(n=3):
    return "\n".join(f"[0:0{i}] a blue square moves right" for i in range(n)) + "\nOverall: a square moves."


class Base(unittest.TestCase):
    def setUp(self):
        W._seen.clear()
        W._ffmpeg_seen.clear()
        self.audits = []
        self.precleared_during = None
        self.model_calls = []
        for p in (
            mock.patch.object(W, "find_ffmpeg", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(W, "sample_frames",
                              return_value=([(1.0, FRAME), (5.0, FRAME + b"2"), (9.0, FRAME + b"3")], 12.0, True)),
            mock.patch("core.chat_media.record_viewed", side_effect=self._record),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.reply = described()

    def _record(self, ids, *, source, items, kinds):
        self.audits.append({"ids": ids, "source": source, "items": items, "kinds": kinds})
        return True

    def _model(self):
        async def fake(prompt, **kw):
            self.model_calls.append((prompt, kw))
            self.precleared_during = set(C._precleared_images.get(frozenset()))
            return C.ModelTurn(self.reply, "", [], False)
        return mock.patch.object(C, "call_groq_turn", new=fake)

    def watch(self, message, args=None):
        with self._model():
            return run(W.watch_video(T.ToolContext(bot=None, message=message), args or {}))


class FramePicking(unittest.TestCase):
    def test_even_when_no_scene_changes(self):
        self.assertEqual(W.pick_times(12.0, []), [1.0, 3.0, 5.0, 7.0, 9.0, 11.0])
        self.assertEqual(len(W.pick_times(2.0, [])), W.MIN_FRAMES)

    def test_scenes_thinned_capped_and_first_frame_kept(self):
        scenes = [i * 7.0 for i in range(1, 60)]
        times = W.pick_times(1200.0, scenes)
        self.assertEqual(times[0], 0.0)
        self.assertLessEqual(len(times), W.MAX_FRAMES)
        self.assertTrue(all(t < W.MAX_SECONDS for t in times))
        self.assertEqual(times, sorted(times))

    def test_few_scenes_topped_up_without_near_duplicates(self):
        times = W.pick_times(30.0, [10.0])
        self.assertGreaterEqual(len(times), W.MIN_FRAMES)
        self.assertTrue(all(b - a >= 0.4 for a, b in zip(times, times[1:])))

    def test_max_frames_never_exceeds_cap(self):
        self.assertLessEqual(len(W.pick_times(600.0, [], max_frames=50)), W.MAX_FRAMES)


class FfmpegBounds(unittest.TestCase):
    def test_long_clip_samples_at_most_six_frames_with_bounded_timeouts(self):
        runs = []
        probe_err = (b"Duration: 00:20:00.00, start: 0\n  Stream #0:0: Video: h264\n"
                     b"  Stream #0:1: Audio: aac\n")
        scenes = "".join(f"pts_time:{i * 9}.0 " for i in range(1, 80)).encode()

        def fake_run(args, timeout):
            runs.append((args, timeout))
            if "-frames:v" in args:
                return subprocess.CompletedProcess(args, 0, FRAME, b"")
            if "null" in args:
                return subprocess.CompletedProcess(args, 0, b"", scenes)
            return subprocess.CompletedProcess(args, 1, b"", probe_err)

        with mock.patch.object(W, "find_ffmpeg", return_value="ffmpeg"), mock.patch.object(W, "_run", side_effect=fake_run):
            frames, duration, has_audio = W.sample_frames(b"vid", "video/mp4")
        self.assertEqual(duration, 1200.0)
        self.assertTrue(has_audio)
        self.assertLessEqual(len(frames), W.MAX_FRAMES)
        self.assertLessEqual(sum(1 for a, _t in runs if "-frames:v" in a), W.MAX_FRAMES)
        self.assertTrue(all(t <= W.SCENE_TIMEOUT_S for _a, t in runs))
        scene_args = next(a for a, _t in runs if "null" in a)
        self.assertIn(str(int(W.MAX_SECONDS)), scene_args)

    def test_no_video_stream_or_no_frames_fails(self):
        def no_video(args, timeout):
            return subprocess.CompletedProcess(args, 1, b"", b"Duration: 00:00:05.00\n Stream #0:0: Audio: aac\n")

        with mock.patch.object(W, "find_ffmpeg", return_value="ffmpeg"), mock.patch.object(W, "_run", side_effect=no_video):
            with self.assertRaises(ValueError):
                W.sample_frames(b"vid")
        with mock.patch.object(W, "find_ffmpeg", return_value=""):
            with self.assertRaises(ValueError) as caught:
                W.sample_frames(b"vid")
        self.assertEqual(str(caught.exception), W.NO_FFMPEG)

    def test_render_is_bounded(self):
        text = W.render(1300.0, "[0:01] x\n" * 500, False)
        self.assertLessEqual(len(text), W.RESULT_CHARS)
        self.assertIn("Only the first 10:00 was watched.", text)


class FindingTheVideo(Base):
    def test_own_attachment_first_and_pictures_ignored(self):
        data, mime, source, err = run(W.find_video(msg([Att(b"p", "a.png", "image/png"), Att(b"v1")])))
        self.assertEqual((data, mime, source, err), (b"v1", "video/mp4", "attachment", ""))

    def test_replied_to_then_recent_history(self):
        replied = msg([Att(b"from-reply")], mid=9)
        self.assertEqual(run(W.find_video(msg(ref=replied)))[0], b"from-reply")
        earlier = msg([Att(b"from-history")], mid=3)
        here = msg(history=[msg(mid=4), earlier])
        self.assertEqual(run(W.find_video(here))[0], b"from-history")
        self.assertEqual(here.channel.history.calls, [(W.HISTORY_LOOKBACK, here)])

    def test_embed_video_uses_the_media_fetch(self):
        embed = SimpleNamespace(video=SimpleNamespace(proxy_url="https://media.tenor.example/x.mp4", url=""),
                                image=None, thumbnail=None)
        with mock.patch("core.chat_media.fetch", new=mock.AsyncMock(return_value=(b"e", "video/mp4", ""))) as fetch:
            data, _m, source, _e = run(W.find_video(msg(embeds=[embed])))
        self.assertEqual((data, source), (b"e", "embed"))
        self.assertEqual(fetch.await_args.args[1], 25 * 1024 * 1024)

    def test_nothing_found_and_too_large(self):
        self.assertEqual(run(W.find_video(msg()))[3], W.NO_VIDEO)
        big = Att(b"v", size=26 * 1024 * 1024)
        self.assertEqual(run(W.find_video(msg([big])))[3], W.TOO_LARGE)
        big.read.assert_not_awaited()

    def test_named_message_is_fetched_from_this_channel_only(self):
        target = msg([Att(b"named")], mid=123456789012345678)
        here = msg(fetched=target, history=[msg([Att(b"other")])])
        link = "https://discord.com/channels/1/2/123456789012345678"
        self.assertEqual(run(W.find_video(here, link))[0], b"named")
        here.channel.fetch_message.assert_awaited_once_with(123456789012345678)
        self.assertEqual(run(W.find_video(here, "not an id"))[3], W.NO_VIDEO)


class WatchTool(Base):
    def test_success_goes_back_to_the_model_with_timestamps(self):
        result = self.watch(msg([Att(b"v")]))
        self.assertTrue(result.for_model)
        self.assertIn("Video, 0:12 long.", result.text)
        self.assertIn("[0:01]", result.text)
        self.assertIn("sound track that was not transcribed", result.text)
        self.assertNotIn("hm=secret", result.detail)
        prompt, kw = self.model_calls[0]
        self.assertEqual(kw["job"], "watch")
        self.assertIsNone(kw["tools"])
        self.assertEqual(len(kw["images"]), 3)
        self.assertLessEqual(kw["max_completion_tokens"], W.DESCRIBE_TOKENS)
        self.assertIn("[0:01]", prompt)

    def test_frames_take_the_viewed_media_path_audit_row_first(self):
        self.watch(msg([Att(b"v")]))
        self.assertEqual(len(self.audits), 1)
        row = self.audits[0]
        self.assertEqual((row["source"], row["kinds"]), ("watch", ["video"]))
        self.assertEqual(row["ids"], {"user_id": 7, "guild_id": 8, "channel_id": 5})
        self.assertEqual(len(row["items"]), 3)
        # Pre-cleared for that one call only, exactly like chat-turn video frames.
        self.assertEqual(len(self.precleared_during), 3)
        self.assertEqual(C._precleared_images.get(frozenset()), frozenset())

    def test_audit_failure_sends_nothing(self):
        with mock.patch("core.chat_media.record_viewed", return_value=False):
            result = self.watch(msg([Att(b"v")]))
        self.assertEqual(self.model_calls, [])
        self.assertEqual((result.text, result.status, result.for_model), (W.FAILED, "audit", False))

    def test_model_that_cannot_see_and_output_block(self):
        self.reply = C.IMAGES_DROPPED_NOTE
        result = self.watch(msg([Att(b"v")]))
        self.assertEqual((result.text, result.status), (W.CANT_SEE, "no_vision"))
        W._seen.clear()
        self.reply = REFUSAL
        result = self.watch(msg([Att(b"v2")]))
        self.assertEqual((result.text, result.status), (REFUSAL, "blocked"))
        self.assertEqual(W._seen, {})

    def test_cached_per_video(self):
        self.watch(msg([Att(b"v")]))
        self.watch(msg([Att(b"v")]))
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(W.sample_frames.call_count, 1)

    def test_busy_no_ffmpeg_no_video(self):
        async def busy():
            async with W._lock:
                return await W.watch_video(T.ToolContext(bot=None, message=msg([Att()])), {})
        self.assertEqual(run(busy()).text, W.BUSY)
        with mock.patch.object(W, "find_ffmpeg", return_value=""):
            self.assertEqual(self.watch(msg([Att()])).text, W.NO_FFMPEG)
        result = self.watch(msg())
        self.assertEqual((result.text, result.status, result.for_model), (W.NO_VIDEO, "not_found", False))
        self.assertEqual(self.model_calls, [])

    def test_frame_and_timeout_failures(self):
        W.sample_frames.side_effect = ValueError(W.NO_FRAMES)
        self.assertEqual(self.watch(msg([Att(b"a")])).text, W.NO_FRAMES)
        W.sample_frames.side_effect = ValueError("ffmpeg said /tmp/x secret")
        self.assertEqual(self.watch(msg([Att(b"b")])).text, W.FAILED)
        W.sample_frames.side_effect = None
        self.reply = "rate"
        with mock.patch.object(C, "call_groq_turn",
                               new=mock.AsyncMock(return_value=C.ModelTurn("x", "", [], True))):
            result = run(W.watch_video(T.ToolContext(bot=None, message=msg([Att(b"c")])), {}))
        self.assertEqual(result.status, "timeout")


class Registration(Base):
    def test_offered_to_anyone_with_ffmpeg_and_follows_default_tools(self):
        W.register_watch_tool()
        tool = T._resolve("watch_video")
        self.assertEqual(tool.access, T.ACCESS_ANYONE)
        with mock.patch.object(T, "tool_prompt_enabled", return_value=True):
            self.assertTrue(T._offered(tool, public_on=True, speaker_is_owner=False, admin_enabled=False))
            self.assertFalse(T._offered(tool, public_on=False, speaker_is_owner=False, admin_enabled=False))
            with mock.patch.object(W, "find_ffmpeg", return_value=""):
                self.assertFalse(T._offered(tool, public_on=True, speaker_is_owner=False, admin_enabled=False))

    def test_execute_refuses_when_default_tools_off(self):
        with mock.patch.object(T, "default_tools_enabled", return_value=False):
            result = run(T.execute("watch_video", {}, T.ToolContext(bot=None, message=msg([Att()]))))
        self.assertFalse(result.ran)

    def test_prompt_line_and_watch_job_has_no_voice(self):
        self.assertIn("watch_video", P.TOOL_LINES)
        spec = [{"type": "function", "function": {"name": "watch_video"}}]
        self.assertIn("watch_video:", P.tool_catalog(spec))
        text = P.system_text(job="watch", voice="SPEAK LIKE A PIRATE")
        self.assertNotIn("PIRATE", text)
        self.assertIn("one line per frame", text)

    def test_registered_by_the_chat_cog_import(self):
        source = (W.__file__.rsplit("core", 1)[0] + "cogs/events/on_message.py")
        with open(source, encoding="utf-8") as fh:
            self.assertIn("import core.tools_watch", fh.read())

    def test_module_has_no_generation_markers_or_api_hosts(self):
        from core.media_scan import source_has_generation

        with open(W.__file__, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(source_has_generation(text), "")
        for host in ("api.x.ai", "generativelanguage", "http://", "https://"):
            self.assertNotIn(host, text)


if __name__ == "__main__":
    unittest.main()
