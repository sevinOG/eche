# tests/test_reasoning_live.py
# Live checks against a running Ollama. Skipped unless ECHE_LIVE_OLLAMA=1.
#
#   ollama pull qwen3:0.6b && ollama pull gemma3:270m
#   ECHE_LIVE_OLLAMA=1 python -m unittest tests.test_reasoning_live -v
#
# ECHE_LIVE_THINK_MODEL / ECHE_LIVE_PLAIN_MODEL pick other tags.

from __future__ import annotations

import asyncio
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

from core import client  # noqa: E402
from core import gui_bridge  # noqa: E402
from core import ollama_thinking as ot  # noqa: E402

LIVE = os.environ.get("ECHE_LIVE_OLLAMA") == "1"
THINK_MODEL = os.environ.get("ECHE_LIVE_THINK_MODEL", "qwen3:0.6b")
PLAIN_MODEL = os.environ.get("ECHE_LIVE_PLAIN_MODEL", "gemma3:270m")


@unittest.skipUnless(LIVE, "set ECHE_LIVE_OLLAMA=1 with Ollama running")
class LiveOllama(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="eche_live_")
        os.makedirs(os.path.join(self.root, "config"))
        self.env = mock.patch.dict(os.environ, {
            "ECHE_USER_ROOT": self.root, "ECHE_PROVIDER": "ollama",
        })
        self.env.start()
        ot.clear_cache()
        self.traces = []
        gui_bridge.set_reasoning_handler(lambda p, m, t: self.traces.append(t))
        self.sent = []
        real = client.requests.post

        def spy(url, json=None, **kw):
            if not url.endswith("/api/show"):
                self.sent.append(json)
            return real(url, json=json, **kw)

        self.spy = mock.patch("requests.post", spy)
        self.spy.start()

    def tearDown(self):
        self.spy.stop()
        gui_bridge.set_reasoning_handler(None)
        self.env.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def use(self, model, settings):
        os.environ["GROQ_MODEL"] = model
        with open(os.path.join(self.root, "config", "settings.json"), "w") as fh:
            json.dump(settings, fh)

    def turn(self, prompt):
        return asyncio.run(client.call_groq_turn(
            prompt, max_completion_tokens=64, include_tool_note=False, job="plain",
        ))

    def test_capabilities_from_show(self):
        self.assertTrue(ot.can_think(ot.capabilities(client._api_url(), THINK_MODEL)))
        self.assertFalse(ot.can_think(ot.capabilities(client._api_url(), PLAIN_MODEL)))

    def test_thinking_model_auto(self):
        self.use(THINK_MODEL, {"reasoning_ollama": "1", "reasoning_budget": "1024"})
        turn = self.turn("What is 2 + 3? Answer in one short sentence.")
        self.assertIs(self.sent[0]["think"], True)
        self.assertEqual(self.sent[0]["options"]["top_k"], 20)
        self.assertTrue(self.traces, "no trace reached the reasoning window")
        self.assertTrue(turn.reply.strip(), "empty reply")
        self.assertNotIn("<think", turn.reply.lower())
        print(f"\n[{THINK_MODEL}] calls={len(self.sent)} reply={turn.reply!r} "
              f"trace={self.traces[0][:80]!r}")

    def test_thinking_model_off(self):
        self.use(THINK_MODEL, {"reasoning_models": f"{THINK_MODEL}=off"})
        turn = self.turn("Say hello in five words.")
        self.assertIs(self.sent[0]["think"], False)
        self.assertTrue(turn.reply.strip())
        self.assertNotIn("<think", turn.reply.lower())
        print(f"\n[{THINK_MODEL} off] reply={turn.reply!r}")

    def test_plain_model_with_switch_on(self):
        self.use(PLAIN_MODEL, {"reasoning_ollama": "1"})
        turn = self.turn("Say hello in five words.")
        self.assertNotIn("think", self.sent[0])
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(turn.reply.strip())
        print(f"\n[{PLAIN_MODEL}] reply={turn.reply!r}")

    def test_plain_model_forced_on_is_refused_then_recovers(self):
        # Pretend /api/show was unavailable so the explicit "on" is sent.
        self.use(PLAIN_MODEL, {"reasoning_models": f"{PLAIN_MODEL}=on"})
        with mock.patch.object(ot, "capabilities", lambda base, model: None):
            turn = self.turn("Say hello in five words.")
        self.assertIs(self.sent[0]["think"], True)
        self.assertNotIn("think", self.sent[1])
        self.assertTrue(turn.reply.strip())

    def test_heckle_is_forced_off(self):
        self.use(THINK_MODEL, {"reasoning_ollama": "1"})
        bodies = []
        real = client._ollama_request

        def record(url, payload):
            send_url, body = real(url, payload)
            bodies.append(body)
            return send_url, body

        # The heckle path posts with aiohttp, so record the built body instead.
        with mock.patch.object(client, "_ollama_request", record):
            out = asyncio.run(client.call_groq_simple("Write one short friendly sentence.", 300))
        self.assertIs(bodies[0]["think"], False)
        self.assertEqual(bodies[0]["options"]["temperature"], 0.9)
        self.assertIsInstance(out, str)


if __name__ == "__main__":
    unittest.main()
