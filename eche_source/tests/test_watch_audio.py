"""watch_video on audio clips (mp3, m4a, ogg/opus voice messages, wav, flac...). Mock-only.

No real audio, video, Whisper, ffmpeg or network: clip bytes are placeholders, the
transcript and the probe are fakes. Nothing here makes a sound, picture or video.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from core import tools as T
from core import tools_watch as W
from core import watch_listen as L
from core.blocklist import REFUSAL
from tests.test_watch_video import Att, Base, msg, run


def voice(data=b"opus-bytes", **kw):
    kw.setdefault("name", "voice-message.ogg")
    kw.setdefault("ctype", "audio/ogg")
    return Att(data, **kw)


class Detection(unittest.TestCase):
    def kinds(self, att):
        return [kind for kind, _ref in W._video_refs(msg([att]))]

    def test_audio_by_content_type_or_extension(self):
        for name, ctype in (
            ("voice-message.ogg", "audio/ogg"),
            ("song.mp3", None),
            ("song.mp3", "application/octet-stream"),
            ("memo.m4a", "audio/x-m4a"),
            ("take.flac", ""),
            ("take.wav", "audio/wav"),
            ("note.opus", None),
            ("clip.aac", "audio/aac"),
            ("speech.webm", "audio/webm"),
            ("noext", "audio/mpeg"),
        ):
            with self.subTest(name=name, ctype=ctype):
                self.assertEqual(self.kinds(Att(b"a", name, ctype)), ["audio"])

    def test_videos_stay_videos_and_others_are_counted_as_other(self):
        self.assertEqual(self.kinds(Att(b"v", "clip.webm", "video/webm")), ["attachment"])
        self.assertEqual(self.kinds(Att(b"v", "clip.mp4", None)), ["attachment"])
        self.assertEqual(self.kinds(Att(b"p", "notes.pdf", "application/pdf")), [])
        self.assertEqual(W._other_files(msg([voice(), Att(b"p", "notes.pdf", "application/pdf")])), 1)

    def test_messages_and_description_mention_audio(self):
        for line in (W.NO_VIDEO, W.NOT_A_VIDEO, W._DESCRIPTION):
            self.assertIn("audio", line)
        self.assertIn("voice message", W._DESCRIPTION)
        from core import prompt_text as P
        self.assertIn("audio clip", P.TOOL_LINES["watch_video"])


class AudioClips(Base):
    def setUp(self):
        super().setUp()
        self.probe = mock.patch.object(W, "probe_bytes", return_value=(42.0, False, True))
        self.probe_mock = self.probe.start()
        self.addCleanup(self.probe.stop)
        self.suffixes = []

    def _transcribe(self, video, suffix=".mp4"):
        self.suffixes.append(suffix)
        return super()._transcribe(video, suffix)

    def tool(self, message, args=None):
        with self._model():
            return run(W.watch_video(T.ToolContext(bot=None, message=message), args or {}))

    def test_voice_message_gets_length_and_transcript_without_frames_or_vision(self):
        self.heard = L.Heard(L.HEARD, [{"start": 1.2, "end": 2.0, "text": "meet at eight"},
                                       {"start": 30.0, "end": 31.0, "text": "bring snacks"}])
        result = self.tool(msg([voice()]))
        self.assertTrue(result.for_model)
        self.assertEqual(result.text, "Audio clip, 0:42 long.\nHeard: [0:01] meet at eight; [0:30] bring snacks")
        W.sample_frames.assert_not_called()
        W._ollama_chat.assert_not_awaited()
        self.assertEqual(self.model_calls, [])
        self.assertEqual(self.suffixes, [".ogg"])
        self.assertIn("source: audio", result.detail)
        self.assertIn("audio: heard segments=2", result.detail)

    def test_same_audit_row_first_fail_closed(self):
        self.heard = L.Heard(L.HEARD, [{"start": 0.0, "end": 1.0, "text": "hi"}])
        self.tool(msg([voice()]))
        self.assertEqual(len(self.audits), 1)
        row = self.audits[0]
        self.assertEqual((row["source"], row["kinds"], len(row["items"])), ("watch", ["audio"], 1))
        self.assertEqual(row["ids"], {"user_id": 7, "guild_id": 8, "channel_id": 5})
        W._seen.clear()
        with mock.patch("core.chat_media.record_viewed", return_value=False):
            result = self.tool(msg([voice(b"other")]))
        self.assertEqual((result.text, result.status), (W.NO_AUDIT, "audit"))
        self.assertEqual(self.transcribed, 1)  # nothing transcribed without the row

    def test_whisper_missing_says_so_clearly(self):
        self.heard = L.Heard(L.NOT_INSTALLED)
        result = self.tool(msg([voice()]))
        self.assertEqual((result.text, result.status, result.for_model), (W.NO_LISTEN, "no_whisper", False))
        self.assertIn("faster-whisper", result.text)

    def test_other_listen_states(self):
        cases = ((L.Heard(L.LOADING), W.LISTEN_LOADING, "loading"),
                 (L.Heard(L.OFF), W.LISTEN_OFF, "listen_off"),
                 (L.Heard(L.FAILED, note="busy"), W.LISTEN_FAILED, "transcribe"),
                 (L.Heard(L.NO_AUDIO), W.NO_SOUND, "no_audio"))
        for heard, line, status in cases:
            with self.subTest(status=status):
                W._seen.clear()
                self.heard = heard
                result = self.tool(msg([voice()]))
                self.assertEqual((result.text, result.status), (line, status))
                self.assertEqual(W._seen, {})

    def test_silence_and_no_sound_stream(self):
        self.heard = L.Heard(L.HEARD, [])
        self.assertIn("Heard: no speech", self.tool(msg([voice()])).text)
        W._seen.clear()
        self.probe_mock.return_value = (3.0, False, False)
        before = self.transcribed
        result = self.tool(msg([voice(b"x2")]))
        self.assertEqual((result.text, result.status), (W.NO_SOUND, "no_audio"))
        self.assertEqual(self.transcribed, before)

    def test_caps_too_large_and_ten_minutes(self):
        big = voice(size=26 * 1024 * 1024)
        result = self.tool(msg([big]))
        self.assertEqual((result.text, result.status), (W.TOO_LARGE, "too_large"))
        big.read.assert_not_awaited()
        self.probe_mock.return_value = (1500.0, False, True)
        self.heard = L.Heard(L.HEARD, [{"start": 1.0, "end": 2.0, "text": "long one"}])
        result = self.tool(msg([voice(b"long")]))
        self.assertIn("Audio clip, 25:00 long. Only the first 10:00 was transcribed.", result.text)
        self.assertLessEqual(len(result.text), W.RESULT_CHARS)

    def test_output_check_blocks_and_is_not_cached(self):
        self.heard = L.Heard(L.HEARD, [{"start": 0.0, "end": 1.0, "text": "something"}])

        async def blocked(text):
            return True
        with mock.patch.object(W, "_blocked", new=blocked):
            result = self.tool(msg([voice()]))
        self.assertEqual((result.text, result.status), (REFUSAL, "blocked"))
        self.assertEqual(W._seen, {})

    def test_cached_per_clip_and_mime_from_extension(self):
        self.heard = L.Heard(L.HEARD, [{"start": 0.0, "end": 1.0, "text": "hey"}])
        self.tool(msg([Att(b"same", "memo.m4a", None)]))
        self.tool(msg([Att(b"same", "memo.m4a", None)]))
        self.assertEqual(self.transcribed, 1)
        self.assertEqual(self.suffixes, [".m4a"])

    def test_found_in_the_replied_to_message(self):
        self.heard = L.Heard(L.HEARD, [{"start": 0.0, "end": 1.0, "text": "from the reply"}])
        result = self.tool(msg(ref=msg([voice()], mid=9)))
        self.assertIn("from the reply", result.text)


if __name__ == "__main__":
    unittest.main()
