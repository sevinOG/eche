"""core.local_queue: one queue for Eche's local Ollama calls. Mock-only (no Ollama, no network)."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

from core import local_queue as Q

GB = Q.GB


def run(c):
    return asyncio.run(c)


class FakeOllama:
    """What /api/ps would say. Running a job "loads" its model; unload removes it (unless stuck)."""

    def __init__(self, loaded=None, sizes=None, stuck=()):
        self.loaded = dict(loaded or {})     # name -> bytes on the card
        self.sizes = dict(sizes or {})       # name -> weight bytes (/api/tags)
        self.stuck = set(stuck)
        self.unloads = []
        self.ps_calls = 0

    async def ps(self, base):
        self.ps_calls += 1
        return list(self.loaded.items())

    async def unload(self, base, name):
        self.unloads.append(name)
        if name not in self.stuck:
            self.loaded.pop(name, None)

    async def tag_size(self, base, model):
        return self.sizes.get(model)


def make_queue(fake, **kw):
    q = Q.LocalQueue()
    q.ps, q.unload, q.tag_size = fake.ps, fake.unload, fake.tag_size
    q.budget = Q.Budget(int(24 * GB), int(4 * GB))
    q.unload_poll = 0.001
    for key, value in kw.items():
        setattr(q, key, value)
    return q


def job(fake, order, model, tag=None, gate=None, size=4 * GB, active=None):
    async def fn():
        if active is not None:
            active.append(1)
            assert len(active) == 1, "two local jobs ran at once"
        order.append(tag or model)
        fake.loaded[Q.norm(model)] = size
        if gate is not None:
            await gate.wait()
        await asyncio.sleep(0)
        if active is not None:
            active.pop()
        return tag or model
    return fn


class Ordering(unittest.TestCase):
    def test_one_at_a_time_oldest_first(self):
        fake, order, active = FakeOllama(), [], []

        async def main():
            q = make_queue(fake)
            gate = asyncio.Event()
            first = asyncio.create_task(q.run("m", job(fake, order, "m", "1", gate, active=active), base="b"))
            await asyncio.sleep(0.01)
            rest = [asyncio.create_task(q.run("m", job(fake, order, "m", str(i), active=active), base="b"))
                    for i in (2, 3, 4)]
            await asyncio.sleep(0.01)
            self.assertEqual(q.status()["waiting"], 3)
            self.assertEqual(q.status()["running"], {"model": "m", "label": "chat"})
            gate.set()
            return await asyncio.gather(first, *rest)
        self.assertEqual(run(main()), ["1", "2", "3", "4"])
        self.assertEqual(order, ["1", "2", "3", "4"])

    def test_loaded_model_jumps_ahead_but_capped_so_nothing_starves(self):
        fake, order = FakeOllama(loaded={"a": 4 * GB}), []

        async def main():
            q = make_queue(fake, max_bypass=2)
            gate = asyncio.Event()
            first = asyncio.create_task(q.run("a", job(fake, order, "a", "a0", gate), base="b"))
            await asyncio.sleep(0.01)
            tasks = [asyncio.create_task(q.run("b", job(fake, order, "b", "B"), base="b"))]
            for i in range(1, 5):
                tasks.append(asyncio.create_task(q.run("a", job(fake, order, "a", f"a{i}"), base="b")))
            await asyncio.sleep(0.01)
            gate.set()
            await asyncio.gather(first, *tasks)
        run(main())
        # B (not loaded) is passed by loaded-model jobs at most twice, then runs.
        self.assertEqual(order, ["a0", "a1", "a2", "B", "a3", "a4"])

    def test_batch_wait_cap(self):
        fake, order = FakeOllama(loaded={"a": 4 * GB}), []

        async def main():
            q = make_queue(fake, batch_wait=0.0)
            gate = asyncio.Event()
            first = asyncio.create_task(q.run("a", job(fake, order, "a", "a0", gate), base="b"))
            await asyncio.sleep(0.01)
            tasks = [asyncio.create_task(q.run("b", job(fake, order, "b", "B"), base="b")),
                     asyncio.create_task(q.run("a", job(fake, order, "a", "a1"), base="b"))]
            await asyncio.sleep(0.01)
            gate.set()
            await asyncio.gather(first, *tasks)
        run(main())
        self.assertEqual(order, ["a0", "B", "a1"])

    def test_status_event_reaches_listeners_and_gui(self):
        fake, seen, events = FakeOllama(), [], []

        async def main():
            q = make_queue(fake)
            q.listeners.append(seen.append)
            with mock.patch("core.gui_bridge.emit", side_effect=lambda e, d=None: events.append(e)):
                await q.run("qwen3-vl:4b-instruct", job(fake, [], "qwen3-vl:4b-instruct"), base="b", label="watch")
        run(main())
        self.assertIn({"model": "qwen3-vl:4b-instruct", "label": "watch"}, [s["running"] for s in seen])
        self.assertIn("gpu_queue", events)


class Budget(unittest.TestCase):
    def test_plan_evicts_least_recently_used_owned_only(self):
        b = Q.Budget(int(24 * GB), int(4 * GB))  # 20 GB usable
        owned = [("recent", 8 * GB), ("old", 8 * GB)]  # most recent first
        self.assertEqual(b.plan([], owned, 8 * GB), ["old"])
        self.assertEqual(b.plan([], owned, 2 * GB), [])
        self.assertEqual(b.plan([], owned, 18 * GB), ["old", "recent"])
        # Another program's 4 GB model is counted, never in the plan.
        self.assertEqual(b.plan([4 * GB], owned, 4 * GB), ["old"])
        self.assertEqual(b.plan([10 * GB], owned, 4 * GB), ["old", "recent"])

    def test_unknown_sizes_do_not_fit(self):
        b = Q.Budget(int(24 * GB), int(4 * GB))
        owned = [("recent", 2 * GB), ("old", 2 * GB)]
        self.assertEqual(b.plan([], owned, None), ["old", "recent"])
        self.assertEqual(b.plan([None], owned, 1 * GB), ["old", "recent"])
        self.assertEqual(b.plan([], [("x", None)], 1 * GB), ["x"])

    def test_sizes_from_name(self):
        self.assertAlmostEqual(Q.guess_bytes("orcarouter/Qwen3.8-27B-Uncensored:latest") / GB, 27 * 0.62, places=3)
        self.assertEqual(Q.guess_bytes("llama3"), None)
        self.assertEqual(Q.norm("Model:Latest"), "model")

    def test_env_budget(self):
        with mock.patch.dict(os.environ, {"ECHE_GPU_VRAM_GB": "16", "ECHE_GPU_RESERVE_GB": "2"}):
            self.assertEqual(Q.Budget.from_env().usable, 14 * GB)

    def test_queue_evicts_lru_owned_and_never_foreign(self):
        fake = FakeOllama(sizes={"new:8b": 7 * GB})

        async def main():
            q = make_queue(fake)
            await q.run("old:8b", job(fake, [], "old:8b", size=8 * GB), base="b")
            await q.run("recent:8b", job(fake, [], "recent:8b", size=8 * GB), base="b")
            fake.loaded["waerhouse-model"] = 2 * GB  # someone else's
            await q.run("new:8b", job(fake, [], "new:8b", size=8 * GB), base="b")
        run(main())
        self.assertEqual(fake.unloads, ["old:8b"])
        self.assertIn("waerhouse-model", fake.loaded)
        self.assertIn("recent:8b", fake.loaded)

    def test_unknown_new_model_evicts_every_owned_one(self):
        fake = FakeOllama()

        async def main():
            q = make_queue(fake)
            await q.run("a:1b", job(fake, [], "a:1b", size=1 * GB), base="b")
            await q.run("b:1b", job(fake, [], "b:1b", size=1 * GB), base="b")
            await q.run("mystery", job(fake, [], "mystery"), base="b")
        run(main())
        self.assertEqual(fake.unloads, ["a:1b", "b:1b"])


class Unloading(unittest.TestCase):
    def test_unload_is_checked_gone(self):
        fake = FakeOllama()
        polls = {"n": 0}
        real_ps = fake.ps

        async def slow_ps(base):  # still listed for two polls after the unload
            if fake.unloads and polls["n"] < 2 and "a:16b" in fake.unloads:
                polls["n"] += 1
                return list(fake.loaded.items()) + [("a:16b", 10 * GB)]
            return await real_ps(base)

        async def main():
            q = make_queue(fake)
            q.ps = slow_ps
            await q.run("a:16b", job(fake, [], "a:16b", size=10 * GB), base="b")
            return await q.run("b:16b", job(fake, [], "b:16b", "done", size=10 * GB), base="b")
        self.assertEqual(run(main()), "done")
        self.assertEqual(fake.unloads, ["a:16b"])
        self.assertEqual(polls["n"], 2)

    def test_unload_that_never_finishes_runs_the_job_directly(self):
        fake = FakeOllama(stuck={"a:16b"})
        logs = []

        async def main():
            q = make_queue(fake, unload_timeout=0.02)
            await q.run("a:16b", job(fake, [], "a:16b", size=10 * GB), base="b")
            order = []
            with mock.patch.object(Q, "dprint", side_effect=logs.append):
                value = await q.run("b:16b", job(fake, order, "b:16b", "direct", size=10 * GB), base="b")
            return value, order
        value, order = run(main())
        self.assertEqual((value, order), ("direct", ["direct"]))  # ran once, outside the queue
        self.assertTrue(any("did not leave the GPU" in line for line in logs))
        self.assertTrue(any("running it directly" in line for line in logs))


class Timeouts(unittest.TestCase):
    def test_queue_wait_limit_fails_clearly_and_leaves_the_line(self):
        fake = FakeOllama()

        async def main():
            q = make_queue(fake, queue_timeout=0.03)
            gate = asyncio.Event()
            first = asyncio.create_task(q.run("m", job(fake, [], "m", gate=gate), base="b"))
            await asyncio.sleep(0.01)
            ran = []
            with self.assertRaises(Q.QueueBusy):
                await q.run("m", job(fake, ran, "m", "late"), base="b")
            self.assertEqual(q.status()["waiting"], 0)
            gate.set()
            await first
            await asyncio.sleep(0.01)
            return ran
        self.assertEqual(run(main()), [])  # the abandoned job never runs later

    def test_run_limit_fails_clearly_and_the_queue_moves_on(self):
        fake = FakeOllama()

        async def hang():
            await asyncio.sleep(10)

        async def main():
            q = make_queue(fake)
            with self.assertRaises(Q.QueueTimeout):
                await q.run("m", hang, base="b", run_timeout=0.03)
            return await q.run("m", job(fake, [], "m", "next"), base="b")
        self.assertEqual(run(main()), "next")

    def test_job_errors_pass_through_once(self):
        fake = FakeOllama()
        calls = []

        async def boom():
            calls.append(1)
            raise ValueError("model said no")

        async def main():
            q = make_queue(fake)
            with self.assertRaises(ValueError):
                await q.run("m", boom, base="b")
        run(main())
        self.assertEqual(calls, [1])


class Fallback(unittest.TestCase):
    def test_broken_ps_runs_directly(self):
        fake = FakeOllama()

        async def broken(base):
            raise RuntimeError("bug")

        async def main():
            q = make_queue(fake)
            q.ps = broken
            return await q.run("m", job(fake, [], "m", "ok"), base="b")
        self.assertEqual(run(main()), "ok")

    def test_unreadable_ps_still_runs_in_the_queue(self):
        fake = FakeOllama()

        async def main():
            q = make_queue(fake)
            q.ps = mock.AsyncMock(return_value=None)
            return await q.run("m", job(fake, [], "m", "ok"), base="b")
        self.assertEqual(run(main()), "ok")

    def test_another_event_loop_runs_directly(self):
        fake = FakeOllama()
        q = make_queue(fake)
        other = asyncio.new_event_loop()
        self.addCleanup(other.close)
        q._loop = other
        self.assertEqual(run(q.run("m", job(fake, [], "m", "ok"), base="b")), "ok")
        self.assertEqual(fake.ps_calls, 0)

    def test_remote_and_off_never_queue(self):
        async def fn():
            return "direct"
        with mock.patch.object(Q, "get_queue", side_effect=AssertionError("queued")):
            self.assertEqual(run(Q.run_local("https://api.groq.com/openai/v1/chat/completions", "m", fn)), "direct")
            self.assertEqual(run(Q.run_local("http://192.168.1.4:11434/v1/chat/completions", "m", fn)), "direct")
            with mock.patch.dict(os.environ, {"ECHE_LOCAL_QUEUE": "off"}):
                self.assertEqual(run(Q.run_local("http://localhost:11434/v1/chat/completions", "m", fn)), "direct")
        self.assertTrue(Q.is_local("http://[::1]:11434/api/chat"))
        self.assertEqual(Q.base_of("http://localhost:11500/v1/chat/completions"), "http://localhost:11500")


class ClientWiring(unittest.TestCase):
    def setUp(self):
        Q.reset_queue()
        self.addCleanup(Q.reset_queue)

    def test_local_ollama_chat_goes_through_the_queue(self):
        from core import client as C

        seen = []

        async def fake_run_local(url, model, fn, **kw):
            seen.append((url, model, kw["label"]))
            return await fn()
        with mock.patch.object(C, "_provider_backend", return_value="ollama"), \
                mock.patch.object(C, "_sync_post", return_value={"choices": []}), \
                mock.patch.object(Q, "run_local", new=fake_run_local):
            data = run(C._ollama_post("http://localhost:11434/v1/chat/completions", {"model": "llama3"}, {}, 90))
        self.assertEqual(data, {"choices": []})
        self.assertEqual(seen, [("http://localhost:11434/v1/chat/completions", "llama3", "chat")])

    def test_busy_and_timeout_fail_clearly_and_remote_skips_the_queue(self):
        from core import client as C

        with mock.patch.object(C, "_provider_backend", return_value="ollama"), \
                mock.patch.object(Q, "run_local", new=mock.AsyncMock(side_effect=Q.QueueBusy())):
            self.assertEqual(run(C._ollama_post("http://localhost:11434/x", {}, {})), {"error": C.LOCAL_QUEUE_BUSY})
        with mock.patch.object(C, "_provider_backend", return_value="ollama"), \
                mock.patch.object(Q, "run_local", new=mock.AsyncMock(side_effect=Q.QueueTimeout())):
            self.assertEqual(run(C._ollama_post("http://localhost:11434/x", {}, {})),
                             {"error": C.LOCAL_QUEUE_TIMEOUT})
        with mock.patch.object(C, "_provider_backend", return_value="openrouter"), \
                mock.patch.object(C, "_sync_post", return_value={"ok": 1}), \
                mock.patch.object(Q, "run_local", side_effect=AssertionError("queued")):
            self.assertEqual(run(C._ollama_post("https://openrouter.ai/api/v1/chat/completions", {}, {})), {"ok": 1})

    def test_watch_vision_waits_in_the_same_queue(self):
        from core import tools_watch as W

        seen = []

        async def fake_run_local(url, model, fn, **kw):
            seen.append((model, kw["label"]))
            return await fn()
        with mock.patch.object(Q, "run_local", new=fake_run_local), \
                mock.patch.object(W, "_ollama_chat", new=mock.AsyncMock(
                    return_value=(200, {"message": {"content": "[0:01] x\nOverall: x."}}))):
            text, reason = run(W.describe_local([(1.0, b"\xff\xd8placeholder")], "p"))
        self.assertEqual((reason, seen), ("", [("qwen3-vl:4b-instruct", "watch")]))
        with mock.patch.object(Q, "run_local", new=mock.AsyncMock(side_effect=Q.QueueBusy())):
            self.assertEqual(run(W.describe_local([(1.0, b"\xff\xd8placeholder")], "p")), ("", "busy"))

    def test_child_safety_classifier_is_not_queued(self):
        from pathlib import Path

        text = (Path(Q.__file__).resolve().parents[1] / "core" / "classifier.py").read_text(encoding="utf-8")
        self.assertNotIn("local_queue", text)


if __name__ == "__main__":
    unittest.main()
