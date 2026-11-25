"""Live 2026-11-25 16:27-16:30 ET: local Qwen3-VL got HTTP 400 (6331 tokens > num_ctx 4096) on every
watch, and the transcript sat in the notes but the ~61-token channel reply only covered the frames.
Mock-only: placeholder frame bytes, fake Ollama, fake chat model.
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

from core import tools as T  # noqa: E402
from core import tools_watch as W  # noqa: E402
from core import watch_auto as A  # noqa: E402
from tests.test_refinement import ModelTurn, run  # noqa: E402
from tests.test_watch_local import local_ok  # noqa: E402
from tests.test_watch_video import Att, Base, msg  # noqa: E402


class LocalContext(Base):
    def tool(self, message):
        with self._model():
            return run(W.watch_video(T.ToolContext(bot=None, message=message), {}))

    def test_six_frames_fit_the_local_context(self):
        self.assertGreaterEqual(W.LOCAL_NUM_CTX, 6331 + W.DESCRIBE_TOKENS)
        W._ollama_chat = local_ok()
        self.tool(msg([Att(b"v")]))
        _base, body, _timeout = W._ollama_chat.await_args.args
        self.assertEqual(body["options"]["num_ctx"], W.LOCAL_NUM_CTX)

    def test_context_400_is_named_in_the_log_and_falls_back(self):
        W._ollama_chat = mock.AsyncMock(return_value=(
            400, '{"error":"request (6331 tokens) exceeds the available context size (4096 tokens)"}'))
        result = self.tool(msg([Att(b"v")]))
        self.assertTrue(result.for_model)
        self.assertEqual(len(self.model_calls), 1)          # chat model fallback still answers
        self.assertIn("vision: local http_400_context", result.detail)
        self.assertNotIn("6331", result.detail)              # never the response text


class WatchReply(unittest.TestCase):
    NOTES = ("Video, 0:17 long.\n[0:01] eyes under a hat, caption appears\n[0:16] same, new caption\n"
             "Overall: a static close-up.\nHeard: [0:00] he left his girl; [0:05] he got a new one")

    def test_prompt_says_heard_is_the_sound_and_frames_are_many(self):
        prompt = A.watch_prompt("PROMPT", self.NOTES)
        self.assertIn('"Heard:" is what is said or sung', prompt)
        self.assertIn("several frames", prompt)
        self.assertIn("Heard: [0:00] he left his girl", prompt)

    def test_watch_answer_gets_room_for_sight_and_sound(self):
        from cogs.events import on_message as om

        cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=1)))
        seen = []

        async def fake(prompt, **kw):
            seen.append(kw["max_completion_tokens"])
            return ModelTurn(reply="Captions about a breakup; he raps 'he left his girl' at 0:00.")
        with mock.patch("core.client.call_groq_turn", fake), \
                mock.patch("core.client.channel_completion_tokens", lambda: 61):
            spoken, _q = run(cog._speak_lookup("PROMPT", self.NOTES, tool="watch_video"))
            run(cog._speak_lookup("PROMPT", "search notes", tool="web_search"))
        self.assertIn("he left his girl", spoken)
        self.assertEqual(seen, [A.WATCH_REPLY_TOKENS, 61])   # other lookups keep the channel cap
        self.assertEqual(A.watch_reply_tokens(400), 400)    # a bigger setting is kept


if __name__ == "__main__":
    unittest.main()
