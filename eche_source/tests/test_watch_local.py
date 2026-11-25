"""watch_video: local Qwen3-VL through Ollama, and faster-whisper on the CPU. Mock-only.

No Ollama, no Whisper, no network, no real video or pictures: frames are
placeholder JPEG-marker bytes, the Ollama call, the Whisper model and ffmpeg
are fakes. Nothing here makes a picture, video or sound.
"""
from __future__ import annotations

import base64
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from core import client as C
from core import tools as T
from core import tools_watch as W
from core import watch_listen as L
from core.blocklist import REFUSAL
from tests.test_watch_video import FRAME, Att, Base, msg, run

ROOT = Path(__file__).resolve().parents[1]


def local_ok(text="[0:01] a red ball rolls\n[0:05] it stops\n[0:09] dark\nOverall: a ball rolls."):
    return mock.AsyncMock(return_value=(200, {"message": {"role": "assistant", "content": text}}))


class LocalVision(Base):
    def tool(self, message, args=None):
        with self._model():
            return run(W.watch_video(T.ToolContext(bot=None, message=message), args or {}))

    def test_local_qwen3_vl_describes_and_chat_model_is_not_called(self):
        order = []
        W._ollama_chat = local_ok()  # restored by Base's patch.object cleanup
        W._ollama_chat.side_effect = lambda *a, **k: order.append("ollama") or (200, {
            "message": {"content": "[0:01] a red ball\n[0:05] it rolls\nOverall: a ball."}})
        self._record_orig = self._record

        def record(ids, **kw):
            order.append("audit")
            return self._record_orig(ids, **kw)
        with mock.patch("core.chat_media.record_viewed", side_effect=record):
            result = self.tool(msg([Att(b"v")]))
        self.assertTrue(result.for_model)
        self.assertIn("[0:01] a red ball", result.text)
        self.assertEqual(self.model_calls, [])
        self.assertEqual(order, ["audit", "ollama"])  # audit row first, fail closed
        base, body, timeout = W._ollama_chat.await_args.args
        self.assertEqual(base, "http://127.0.0.1:11434")
        self.assertEqual(body["model"], "qwen3-vl:4b-instruct")
        self.assertEqual(body["keep_alive"], 0)
        self.assertFalse(body["stream"])
        self.assertLessEqual(body["options"]["num_predict"], W.DESCRIBE_TOKENS)
        images = body["messages"][0]["images"]
        self.assertEqual(len(images), 3)
        self.assertEqual(base64.b64decode(images[0]), FRAME)
        self.assertIn("[0:01]", body["messages"][0]["content"])
        self.assertLessEqual(timeout, W.LOCAL_VISION_TIMEOUT_S)
        self.assertIn("vision: local model=qwen3-vl:4b-instruct", result.detail)

    def test_missing_model_falls_back_to_the_chat_model(self):
        W._ollama_chat = mock.AsyncMock(return_value=(404, 'model "qwen3-vl:4b-instruct" not found'))
        result = self.tool(msg([Att(b"v")]))
        self.assertTrue(result.for_model)
        self.assertEqual(len(self.model_calls), 1)
        self.assertIn("vision: local missing", result.detail)
        self.assertIn("vision: chat", result.detail)

    def test_unreachable_and_no_timestamps_fall_back(self):
        result = self.tool(msg([Att(b"v")]))  # Base: connection refused
        self.assertIn("vision: local unreachable", result.detail)
        self.assertEqual(len(self.model_calls), 1)
        W._seen.clear()
        W._ollama_chat = local_ok("I see a ball.")
        result = self.tool(msg([Att(b"v")]))
        self.assertIn("vision: local no_stamps", result.detail)
        self.assertEqual(len(self.model_calls), 2)

    def test_timeout_unloads_the_local_model_then_falls_back(self):
        calls = []

        async def fake(base, body, timeout):
            calls.append(body)
            if body.get("messages"):
                raise TimeoutError()
            return 200, {}
        W._ollama_chat = fake
        result = self.tool(msg([Att(b"v")]))
        self.assertTrue(result.for_model)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], {"model": "qwen3-vl:4b-instruct", "messages": [], "keep_alive": 0})
        self.assertIn("vision: local timeout", result.detail)

    def test_local_only_fails_clearly_and_chat_only_skips_ollama(self):
        with mock.patch.dict(os.environ, {"ECHE_WATCH_VISION": "local"}):
            result = self.tool(msg([Att(b"v")]))
        self.assertEqual((result.text, result.status), (W.CANT_SEE, "no_vision"))
        self.assertEqual(self.model_calls, [])
        self.assertIn("vision: local unreachable", result.detail)
        W._ollama_chat = local_ok()
        with mock.patch.dict(os.environ, {"ECHE_WATCH_VISION": "chat"}):
            result = self.tool(msg([Att(b"v2")]))
        self.assertTrue(result.for_model)
        W._ollama_chat.assert_not_awaited()

    def test_model_tag_setting_and_off(self):
        W._ollama_chat = local_ok()
        with mock.patch.dict(os.environ, {"ECHE_WATCH_VISION_MODEL": "qwen3-vl:8b-instruct"}):
            self.tool(msg([Att(b"v")]))
        self.assertEqual(W._ollama_chat.await_args.args[1]["model"], "qwen3-vl:8b-instruct")
        W._seen.clear()
        W._ollama_chat.reset_mock()
        with mock.patch.dict(os.environ, {"ECHE_WATCH_VISION_MODEL": "off"}):
            result = self.tool(msg([Att(b"v")]))
        W._ollama_chat.assert_not_awaited()
        self.assertIn("vision: local off", result.detail)

    def test_ollama_url_is_localhost_only(self):
        cases = {
            "": "http://127.0.0.1:11434",
            "http://localhost:11500/v1/chat/completions": "http://127.0.0.1:11500",
            "http://127.0.0.1/api/chat": "http://127.0.0.1:11434",
            "http://192.168.1.20:11434/v1/chat/completions": "http://127.0.0.1:11434",
            "https://ollama.example.com/v1/chat/completions": "http://127.0.0.1:11434",
            "http://localhost.example.com:11434": "http://127.0.0.1:11434",
            "not a url": "http://127.0.0.1:11434",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"OLLAMA_API_URL": raw}):
                self.assertEqual(W.ollama_base(), want)

    def test_local_description_takes_the_strict_output_check(self):
        seen_images_flag = []

        async def blocked(*texts, tool_calls=None):
            seen_images_flag.append(C._request_images.get(False))
            return True
        W._ollama_chat = local_ok()
        with mock.patch.object(C, "_output_blocked", new=blocked):
            result = self.tool(msg([Att(b"v")]))
        self.assertEqual((result.text, result.status), (REFUSAL, "blocked"))
        self.assertEqual(seen_images_flag, [True])
        self.assertEqual(self.model_calls, [])
        self.assertEqual(W._seen, {})

    def test_think_block_is_dropped(self):
        W._ollama_chat = local_ok("<think>hmm</think>[0:01] a cup\nOverall: a cup.")
        result = self.tool(msg([Att(b"v")]))
        self.assertNotIn("hmm", result.text)
        self.assertIn("[0:01] a cup", result.text)


class Listening(Base):
    def tool(self, message, args=None):
        W._ollama_chat = local_ok()
        with self._model():
            return run(W.watch_video(T.ToolContext(bot=None, message=message), args or {}))

    def test_transcript_folded_in_with_timestamps(self):
        self.heard = L.Heard(L.HEARD, [{"start": 1.4, "end": 2.0, "text": "watch this"},
                                       {"start": 7.0, "end": 8.0, "text": "no way"}])
        result = self.tool(msg([Att(b"v")]))
        self.assertIn("Heard: [0:01] watch this; [0:07] no way", result.text)
        self.assertIn("audio: heard segments=2", result.detail)
        self.assertNotIn("watch this", result.detail.split("\n\n")[0])  # the log header has no speech
        self.assertEqual(self.transcribed, 1)

    def test_no_sound_track_skips_whisper(self):
        W.sample_frames.return_value = ([(1.0, FRAME), (5.0, FRAME + b"2"), (9.0, FRAME + b"3")], 12.0, False)
        result = self.tool(msg([Att(b"v")]))
        self.assertIn("It has no sound track.", result.text)
        self.assertEqual(self.transcribed, 0)
        self.assertIn("audio: no_audio", result.detail)

    def test_silence_and_not_installed(self):
        self.heard = L.Heard(L.HEARD, [])
        self.assertIn("Heard: no speech", self.tool(msg([Att(b"v")])).text)
        W._seen.clear()
        self.heard = L.Heard(L.NOT_INSTALLED)
        result = self.tool(msg([Att(b"v")]))
        self.assertTrue(result.for_model)
        self.assertIn("not transcribed (local speech-to-text isn't installed)", result.text)

    def test_transcript_and_result_are_capped(self):
        self.heard = L.Heard(L.HEARD, [{"start": float(i), "end": i + 1.0, "text": "word " * 30}
                                       for i in range(80)], cut=True)
        self.reply = "\n".join(f"[0:0{i % 10}] " + "x" * 120 for i in range(12))
        W._ollama_chat = None
        with mock.patch.dict(os.environ, {"ECHE_WATCH_VISION": "chat"}):
            with self._model():
                result = run(W.watch_video(T.ToolContext(bot=None, message=msg([Att(b"v")])), {}))
        heard = [line for line in result.text.split("\n") if line.startswith("Heard:")][0]
        self.assertLessEqual(len(heard), L.TRANSCRIPT_CHARS + 40)
        self.assertLessEqual(len(result.text), W.RESULT_CHARS)

    def test_blocked_transcript_sends_refusal(self):
        self.heard = L.Heard(L.HEARD, [{"start": 0.0, "end": 1.0, "text": "something bad"}])

        async def blocked(text):
            return text.startswith("Heard:")
        with mock.patch.object(W, "_blocked", new=blocked):
            result = self.tool(msg([Att(b"v")]))
        self.assertEqual((result.text, result.status), (REFUSAL, "blocked"))
        self.assertEqual(W._seen, {})

    def test_still_downloading_is_not_cached(self):
        self.heard = L.Heard(L.LOADING)
        result = self.tool(msg([Att(b"v")]))
        self.assertIn("still downloading", result.text)
        self.tool(msg([Att(b"v")]))
        self.assertEqual(self.transcribed, 2)

    def test_audit_failure_means_no_listening(self):
        with mock.patch("core.chat_media.record_viewed", return_value=False):
            result = self.tool(msg([Att(b"v")]))
        self.assertEqual(result.status, "audit")
        self.assertEqual(self.transcribed, 0)


class WhisperModule(unittest.TestCase):
    def setUp(self):
        L._model, L._model_name, L._loading, L._load_error = None, "", None, ""
        self.addCleanup(setattr, L, "_model", None)
        self.addCleanup(setattr, L, "_model_name", "")
        self.addCleanup(setattr, L, "_loading", None)
        for key in ("ECHE_WATCH_WHISPER", "ECHE_WATCH_WHISPER_THREADS"):
            os.environ.pop(key, None)

    def test_settings(self):
        self.assertEqual(L.model_name(), "small")
        with mock.patch.dict(os.environ, {"ECHE_WATCH_WHISPER": "base"}):
            self.assertEqual(L.model_name(), "base")
        with mock.patch.dict(os.environ, {"ECHE_WATCH_WHISPER": "large-v3"}):
            self.assertEqual(L.model_name(), "small")  # only the CPU-sized ones
        with mock.patch.dict(os.environ, {"ECHE_WATCH_WHISPER": "off"}):
            self.assertEqual(L.transcribe(b"v").state, L.OFF)
        with mock.patch.dict(os.environ, {"ECHE_WATCH_WHISPER_THREADS": "64"}):
            self.assertEqual(L._threads(), L.MAX_THREADS)

    def test_not_installed_is_optional(self):
        with mock.patch.object(L, "installed", return_value=False):
            self.assertEqual(L.transcribe(b"v").state, L.NOT_INSTALLED)

    def test_one_transcription_at_a_time(self):
        with mock.patch.object(L, "installed", return_value=True):
            with L._busy:
                heard = L.transcribe(b"v")
        self.assertEqual((heard.state, heard.note), (L.FAILED, "busy"))

    def test_extract_args_and_no_audio(self):
        seen = []

        def fake_run(args, timeout):
            seen.append((args, timeout))
            return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
        with mock.patch.object(L, "installed", return_value=True), \
                mock.patch.object(W, "find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(W, "_run", side_effect=fake_run):
            self.assertEqual(L.transcribe(b"v").state, L.NO_AUDIO)
        args, timeout = seen[0]
        for flag, value in (("-ar", "16000"), ("-ac", "1"), ("-t", "600"), ("-map", "0:a:0")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertLessEqual(timeout, L.EXTRACT_TIMEOUT_S)

    def _fake_faster_whisper(self, made):
        class Seg:
            def __init__(self, start, text, nsp=0.1):
                self.start, self.end, self.text, self.no_speech_prob = start, start + 1, text, nsp

        class WhisperModel:
            def __init__(self, name, **kw):
                made.append((name, kw))

            def transcribe(self, path, **kw):
                made.append(("transcribe", kw))
                return iter([Seg(0.5, " hello  there "), Seg(3.0, "♪", nsp=0.9), Seg(4.0, "bye")]), None
        return types.SimpleNamespace(WhisperModel=WhisperModel)

    def test_loads_on_cpu_int8_into_the_data_folder_and_logs_without_secrets(self):
        made, logs = [], []
        with mock.patch.dict(sys.modules, {"faster_whisper": self._fake_faster_whisper(made)}), \
                mock.patch.dict(os.environ, {"ECHE_USER_ROOT": "/tmp", "HF_TOKEN": "hf_secret123"}), \
                mock.patch.object(L, "dprint", side_effect=logs.append), \
                mock.patch.object(L, "_samples", return_value=[0.0] * 16000), \
                mock.patch("os.makedirs"):
            model = L.get_model("small", wait_s=5)
            segments, cut = L.run_model(model, "/tmp/x.wav")
        name, kw = made[0]
        self.assertEqual(name, "small")
        self.assertEqual((kw["device"], kw["compute_type"], kw["cpu_threads"]), ("cpu", "int8", 8))
        self.assertEqual(kw["download_root"], os.path.join("/tmp", "models", "whisper"))
        self.assertTrue(made[1][1]["vad_filter"])
        self.assertEqual([s["text"] for s in segments], ["hello there", "bye"])  # no-speech segment dropped
        self.assertFalse(cut)
        self.assertTrue(any("downloading model 'small'" in line for line in logs))
        self.assertFalse(any("hf_secret123" in line for line in logs))

    def test_deadline_stops_the_transcript(self):
        made = []
        model = self._fake_faster_whisper(made).WhisperModel("small")
        with mock.patch.object(L, "_samples", return_value=[0.0] * 16000):
            segments, cut = L.run_model(model, "/tmp/x.wav", deadline_s=-1)
        self.assertEqual((segments, cut), ([], True))
        self.assertEqual(L.heard_line(L.Heard(L.HEARD, [{"start": 0, "text": "hi"}], cut=True), True, W.stamp),
                         "Heard: [0:00] hi … (transcript stopped early)")

    def test_load_failure_and_still_loading(self):
        class Broken:
            def __init__(self, *a, **k):
                raise OSError("disk full at C:\\Users\\x")
        with mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=Broken)}), \
                mock.patch("os.makedirs"):
            with self.assertRaises(RuntimeError) as caught:
                L.get_model("base", wait_s=5)
        self.assertEqual(str(caught.exception), "OSError")  # the type only, never the path or message
        L._loading = None
        gate = __import__("threading").Event()

        class Slow:
            def __init__(self, *a, **k):
                gate.wait(5)
        with mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=Slow)}), \
                mock.patch("os.makedirs"):
            self.assertIsNone(L.get_model("base", wait_s=0.05))
            gate.set()
            L._loading.join(5)
            self.assertIsNotNone(L.get_model("base", wait_s=1))


class WavSamples(unittest.TestCase):
    def test_16bit_mono_wav_becomes_float_samples(self):
        import tempfile
        import wave

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "s.wav")
            with wave.open(path, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\x00\x40" * 4 + b"\x00\x00" * 4)  # placeholder samples, not a recording
            try:
                samples = L._samples(path)
            except ImportError:
                self.skipTest("numpy not installed")
        self.assertEqual(len(samples), 8)
        self.assertAlmostEqual(float(samples[0]), 0.5)
        self.assertEqual(float(samples[-1]), 0.0)


class Packaging(unittest.TestCase):
    def test_optional_requirements_are_pinned_and_hashed(self):
        text = (ROOT / "requirements-whisper.txt").read_text(encoding="utf-8")
        reqs = [b for b in text.split("\n\n") if b.strip() and not b.lstrip().startswith("#")]
        names = set()
        for block in reqs:
            head = block.strip().splitlines()[0]
            self.assertIn("==", head, head)
            self.assertIn("--hash=sha256:", block, head)
            names.add(head.split("==")[0].lower())
        self.assertTrue({"faster-whisper", "ctranslate2", "onnxruntime", "av", "colorama"} <= names)
        main = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("faster-whisper", main)  # never part of the required install

    def test_build_installs_it_optionally_and_bundles_it_only_when_present(self):
        bat = (ROOT / "BUILD.bat").read_text(encoding="utf-8")
        line = bat.index("requirements-whisper.txt\"")
        after = bat[line:line + 400]
        self.assertIn("[WARN]", after)
        self.assertNotIn("exit /b", after)
        self.assertLess(bat.index('requirements.txt"'), line)
        spec = (ROOT / "build_exe.spec").read_text(encoding="utf-8")
        self.assertIn('"faster_whisper"', spec)
        self.assertIn("find_spec(pkg) is None", spec)


if __name__ == "__main__":
    unittest.main()
