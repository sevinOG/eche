"""Live 2026-11-25 16:04 ET: the bot's loop froze in Thread.start() under the queue's /api/ps.

Mock-only: no Ollama, no Whisper model, no clip. One test talks to a tiny aiohttp server on
127.0.0.1 to prove an IP-literal base never touches DNS (so never starts an executor thread).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from core import local_queue as Q  # noqa: E402
from core import loop_watchdog as D  # noqa: E402
from core import tools_watch as W  # noqa: E402
from core import watch_listen as L  # noqa: E402


def run(c):
    return asyncio.run(c)


async def forever(*_a, **_k):
    await asyncio.Event().wait()


class IpLiteral(unittest.TestCase):
    def test_localhost_becomes_127_0_0_1_everywhere(self):
        self.assertEqual(Q.base_of("http://localhost:11434/v1/chat/completions"), "http://127.0.0.1:11434")
        self.assertEqual(Q.base_of("http://LOCALHOST/api"), "http://127.0.0.1:11434")
        self.assertEqual(Q.base_of("http://[::1]:11434/x"), "http://[::1]:11434")
        with mock.patch.dict(os.environ, {"OLLAMA_API_URL": "http://localhost:11434/v1/chat/completions"}):
            self.assertEqual(W.ollama_base(), "http://127.0.0.1:11434")

    def test_queue_http_never_resolves_a_name(self):
        from aiohttp import web

        async def main():
            app = web.Application()

            async def ps(_request):
                return web.json_response({"models": [{"name": "m:latest", "size_vram": 5}]})
            app.router.add_get("/api/ps", ps)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            loop = asyncio.get_running_loop()
            try:
                with mock.patch.object(loop, "getaddrinfo", side_effect=AssertionError("DNS lookup")), \
                        mock.patch.object(loop, "run_in_executor", side_effect=AssertionError("executor")):
                    # Even a "localhost" base goes out as 127.0.0.1.
                    return await Q._http_ps(f"http://localhost:{port}")
            finally:
                await runner.cleanup()
        self.assertEqual(run(main()), [("m:latest", 5)])


class BoundedPrepare(unittest.TestCase):
    def setUp(self):
        for name, value in (("PS_CALL_TIMEOUT_S", 0.05), ("UNLOAD_CALL_TIMEOUT_S", 0.05),
                            ("PREPARE_TIMEOUT_S", 0.3)):
            patch = mock.patch.object(Q, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def queue(self, ps=None, unload=None, tag_size=None):
        q = Q.LocalQueue()
        q.budget = Q.Budget(int(24 * Q.GB), int(4 * Q.GB))
        q.unload_poll = 0.001
        q.unload_timeout = 0.05

        async def none(*_a):
            return None
        q.ps, q.unload, q.tag_size = ps or none, unload or none, tag_size or none
        return q

    def timed(self, q):
        async def main():
            async def fn():
                return "ran"
            started = time.monotonic()
            value = await q.run("big-27b", fn, base="http://127.0.0.1:11434")
            return value, time.monotonic() - started
        return run(main())

    def test_hanging_ps_skips_the_budget_and_runs_the_job(self):
        value, took = self.timed(self.queue(ps=forever))
        self.assertEqual(value, "ran")
        self.assertLess(took, 1.0)

    def test_hanging_unload_runs_the_job_directly(self):
        async def ps(_base):
            return [("other-owned", 20 * Q.GB)]
        q = self.queue(ps=ps, unload=forever)
        q.owned["other-owned"] = time.monotonic()
        value, took = self.timed(q)
        self.assertEqual(value, "ran")
        self.assertLess(took, 1.0)

    def test_hanging_size_lookup_is_bounded(self):
        async def ps(_base):
            return []
        value, took = self.timed(self.queue(ps=ps, tag_size=forever))
        self.assertEqual(value, "ran")
        self.assertLess(took, 1.0)

    def test_whole_prepare_has_a_cap(self):
        q = self.queue()

        async def slow_prepare(_job):
            await asyncio.sleep(10)
        q._prepare = slow_prepare
        value, took = self.timed(q)
        self.assertEqual(value, "ran")
        self.assertLess(took, 1.5)


class Watchdog(unittest.TestCase):
    def test_stall_dumps_every_thread_once_and_recovers(self):
        lines = []
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "loop_stall.txt")
            loop = types.SimpleNamespace(pending=[])
            loop.call_soon_threadsafe = loop.pending.append   # a stuck loop never runs them
            dog = D.Watchdog(loop, stall_s=0.01, path_fn=lambda: path, log=lines.append)
            dog.beat -= 1
            dog.check_once()
            dog.check_once()   # same stall: no second dump
            text = open(path, encoding="utf-8").read()
            self.assertEqual(text.count("=== loop stalled"), 1)
            self.assertIn("Current thread", text)
            self.assertIn("= MainThread", text)
            self.assertIn("test_loop_freeze_hotfix.py", text)   # this thread's stack is in it
            self.assertTrue(any("stalled" in line for line in lines))
            for fn in loop.pending:   # the loop catches up
                fn()
            dog.check_once()
            self.assertFalse(dog.stalled)

    def test_dump_cap(self):
        loop = types.SimpleNamespace(call_soon_threadsafe=lambda fn: None)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "d.txt")
            dog = D.Watchdog(loop, stall_s=0.0, max_dumps=1, path_fn=lambda: path, log=lambda _l: None)
            for _ in range(3):
                dog.beat = time.monotonic() - 5
                dog.stalled = False
                dog.check_once()
            self.assertEqual(open(path, encoding="utf-8").read().count("=== loop stalled"), 1)

    def test_starts_from_setup_and_can_be_turned_off(self):
        with mock.patch.dict(os.environ, {"ECHE_LOOP_WATCHDOG": "off"}):
            self.assertIsNone(D.start(object()))
        src = open(os.path.join(SOURCE, "core", "bot.py"), encoding="utf-8").read()
        self.assertIn("start_loop_watchdog(asyncio.get_running_loop())", src)


class WhisperOutOfProcess(unittest.TestCase):
    def setUp(self):
        L._download = None
        self.addCleanup(setattr, L, "_download", None)

    def _extract_ok(self):
        def fake_run(args, timeout):
            with open(args[args.index("-f") + 2], "wb") as handle:
                handle.write(b"\0" * 8000)
            return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        return fake_run

    def test_bot_process_never_imports_faster_whisper(self):
        calls = []

        def fake_worker(args, timeout, check, **kw):
            calls.append((args, timeout, kw))
            with open(args[-1], "w", encoding="utf-8") as handle:
                json.dump({"state": "heard", "segments": [{"start": 1, "end": 2, "text": " hi  there "}],
                           "cut": False}, handle)
            return types.SimpleNamespace(returncode=0)
        poison = types.ModuleType("faster_whisper")   # importing it here would be the bug
        with mock.patch.object(L, "installed", return_value=True), \
                mock.patch.object(L, "_downloaded", return_value=True), \
                mock.patch.object(W, "find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(W, "_run", side_effect=self._extract_ok()), \
                mock.patch.object(L.subprocess, "run", side_effect=fake_worker), \
                mock.patch.object(L, "get_model", side_effect=AssertionError("in-process load")), \
                mock.patch.dict(sys.modules, {"faster_whisper": poison}):
            heard = L.transcribe(b"clip")
        self.assertEqual((heard.state, heard.segments), (L.HEARD, [{"start": 1.0, "end": 2.0, "text": "hi there"}]))
        args, timeout, kw = calls[0]
        self.assertIn(L.WORKER_FLAG, args)
        self.assertEqual(args[args.index(L.WORKER_FLAG) + 1], "transcribe")
        self.assertLessEqual(timeout, L.WORKER_TIMEOUT_S)
        self.assertEqual(kw["env"]["ECHE_RUNNING"], "WHISPER")

    def test_first_use_downloads_in_the_background_and_says_loading(self):
        started = []

        class Proc:
            def __init__(self, args, **kw):
                started.append(args)

            def poll(self):
                return None
        with mock.patch.object(L, "installed", return_value=True), \
                mock.patch.object(L, "_downloaded", return_value=False), \
                mock.patch.object(W, "find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(W, "_run", side_effect=self._extract_ok()), \
                mock.patch.object(L.subprocess, "Popen", Proc):
            self.assertEqual(L.transcribe(b"clip").state, L.LOADING)
            self.assertEqual(L.transcribe(b"clip").state, L.LOADING)
        self.assertEqual(len(started), 1)                  # one download process
        self.assertEqual(started[0][-2:], ["download", "small"])

    def test_worker_timeout_and_garbage(self):
        import subprocess

        with mock.patch.object(L.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertEqual(L.run_worker("small", "/tmp/none.wav").note, "timeout")
        with mock.patch.object(L.subprocess, "run", return_value=None):
            self.assertEqual(L.run_worker("small", "/tmp/eche-no-such.wav").note, "worker no result")

    def test_worker_env_has_no_secrets_and_command_shapes(self):
        env = {"DISCORD_TOKEN": "t", "XAI_API_KEY": "k", "GROQ_KEY": "g", "PATH": "/bin", "ECHE_RUNNING": "BOT"}
        with mock.patch.dict(os.environ, env, clear=True):
            out = L.worker_env()
        self.assertEqual(set(out) & {"DISCORD_TOKEN", "XAI_API_KEY", "GROQ_KEY"}, set())
        self.assertEqual(out["PATH"], "/bin")
        self.assertEqual(out["ECHE_RUNNING"], "WHISPER")
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", "C:\\Eche\\Eche.exe"):
            self.assertEqual(L.worker_command("download", "small"),
                             ["C:\\Eche\\Eche.exe", "--whisper-worker", "download", "small"])
        cmd = L.worker_command("download", "small")
        self.assertTrue(cmd[1].endswith("eche_app.py"))

    def test_worker_main_writes_the_result(self):
        class Seg:
            start, end, text, no_speech_prob = 0.5, 1.5, " hello ", 0.1

        class WhisperModel:
            def __init__(self, *a, **k):
                pass

            def transcribe(self, *_a, **_k):
                return iter([Seg()]), None
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=WhisperModel)}), \
                mock.patch.object(L, "_samples", return_value=[0.0]), \
                mock.patch.dict(os.environ, {"ECHE_USER_ROOT": folder}):
            L._model, L._model_name = None, ""
            out = os.path.join(folder, "r.json")
            try:
                self.assertEqual(L.worker_main(["transcribe", "tiny", "/x.wav", out]), 0)
            finally:
                L._model, L._model_name = None, ""
            data = json.load(open(out, encoding="utf-8"))
        self.assertEqual(data["state"], "heard")
        self.assertEqual(data["segments"][0]["text"], "hello")

    def test_app_entry_dispatches_the_worker_before_the_bot(self):
        src = open(os.path.join(SOURCE, "eche_app.py"), encoding="utf-8").read()
        self.assertLess(src.index('"--whisper-worker" in sys.argv'), src.index("want_bot ="))


if __name__ == "__main__":
    unittest.main()
