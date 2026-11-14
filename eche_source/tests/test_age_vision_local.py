# tests/test_age_vision_local.py
# Age vision without a cloud key uses a local Ollama vision model. It must
# fail closed on a missing model, timeout, or anything but YES/NO, and keep
# the strict 18+ rules. No real images: only placeholder base64 bytes.
from __future__ import annotations

import asyncio
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from core import classifier as C  # noqa: E402

IMG = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]


class _Resp:
    def __init__(self, payload=None, raw=None):
        self.status = 200
        self.headers = {}
        self._raw = raw if raw is not None else json.dumps(payload or {}).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ollama(text):
    return _Resp({"model": "qwen3-vl:4b-instruct", "message": {"role": "assistant", "content": text}, "done": True})


class LocalAgeVisionTests(unittest.TestCase):
    def setUp(self):
        self._prev_test_mode = C._TEST_MODE
        C.allow_in_tests(False)
        self.sent = []
        self.errors = []
        self._patches = [
            mock.patch.object(C, "vision_backend_available", return_value=False),
            mock.patch.object(C, "_emit_checker_error", side_effect=self.errors.append),
            mock.patch("core.secrets.public_value", return_value=""),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        C._TEST_MODE = self._prev_test_mode

    def _run(self, reply=None, side=None, adult=False, images=IMG):
        def opener(req, timeout=None):
            self.sent.append((req.full_url, json.loads(req.data.decode()), timeout))
            if side is not None:
                raise side
            return reply

        with mock.patch("urllib.request.urlopen", side_effect=opener):
            return C._age_vision_sync(images, adult=adult)

    def test_default_model_and_backend(self):
        self.assertEqual(C.age_vision_backend(), "ollama")
        self.assertEqual(C.age_vision_model(), "qwen3-vl:4b-instruct")
        self.assertTrue(C.model_is_vision_capable("qwen3-vl:4b-instruct"))
        self.assertTrue(C.model_is_vision_capable("qwen2.5vl:3b"))

    def test_setting_overrides_model(self):
        with mock.patch("core.secrets.public_value", return_value="qwen2.5vl:3b"):
            self.assertEqual(C.age_vision_model(), "qwen2.5vl:3b")
            self._run(_ollama("NO"))
        self.assertEqual(self.sent[0][1]["model"], "qwen2.5vl:3b")

    def test_request_shape(self):
        self.assertEqual(self._run(_ollama("NO")), "")
        url, body, timeout = self.sent[0]
        self.assertEqual(url, C.OLLAMA_CHAT_URL)
        self.assertEqual(body["messages"][0]["images"], ["AAAA"])
        self.assertEqual(body["messages"][0]["content"], C.AGE_VISION_PROMPT)
        self.assertFalse(body["stream"])
        self.assertFalse(body["think"])
        self.assertEqual(body["keep_alive"], C.OLLAMA_KEEP_ALIVE)
        self.assertEqual(body["options"]["num_ctx"], 4096)
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertEqual(timeout, C.AGE_VISION_OLLAMA_TIMEOUT)

    def test_adult_mode_uses_the_strict_prompt(self):
        self._run(_ollama("NO"), adult=True)
        prompt = self.sent[0][1]["messages"][0]["content"]
        self.assertEqual(prompt, C.AGE_VISION_ADULT_PROMPT)
        for word in ("anime", "cartoon", "chibi", "doll", "real or identifiable person", "clearly an adult"):
            self.assertIn(word, prompt)

    def test_yes_blocks(self):
        self.assertEqual(self._run(_ollama("YES")), "age-vision-yes")

    def test_unclear_answers_block_and_name_the_field(self):
        for text in ("", "Maybe", "The person appears to be an adult.", "<think>x</think>"):
            with self.subTest(text=text):
                self.errors.clear()
                self.assertTrue(self._run(_ollama(text)))
        self.assertTrue(any("age_vision_ollama_model" in e for e in self.errors))

    def test_missing_model_blocks_and_names_the_field(self):
        err = urllib.error.HTTPError(C.OLLAMA_CHAT_URL, 404, "not found", {}, None)
        with mock.patch.object(C, "_http_error_payload", return_value=(404, 'model "qwen3-vl:4b-instruct" not found')):
            self.assertEqual(self._run(side=err), "age-vision-error")
        self.assertTrue(self.errors)
        self.assertIn("age_vision_ollama_model", self.errors[-1])
        self.assertIn("ollama pull", self.errors[-1])

    def test_timeout_blocks(self):
        self.assertEqual(self._run(side=TimeoutError("timed out")), "age-vision-error")
        self.assertIn("timed out", self.errors[-1])

    def test_ollama_down_blocks(self):
        self.assertEqual(self._run(side=urllib.error.URLError("refused")), "age-vision-error")

    def test_unreadable_reply_blocks(self):
        self.assertEqual(self._run(_Resp(raw=b"<html>")), "age-vision-error")

    def test_remote_or_missing_images_block_without_a_request(self):
        remote = [{"type": "image_url", "image_url": {"url": "https://example.com/x.png"}}]
        self.assertEqual(self._run(_ollama("NO"), images=remote), "age-vision-error")
        self.assertEqual(self._run(_ollama("NO"), images=[]), "age-vision-error")
        self.assertFalse(self.sent)

    def test_async_runs_off_the_loop_with_a_deadline(self):
        async def go():
            with mock.patch("urllib.request.urlopen", return_value=_ollama("NO")) as op:
                verdict = await C.age_vision_async(IMG)
            return verdict, op.call_count

        verdict, calls = asyncio.run(go())
        self.assertEqual(verdict, "")
        self.assertEqual(calls, 1)
        self.assertEqual(C._age_vision_deadline(), C.AGE_VISION_OLLAMA_DEADLINE)

    def test_async_deadline_blocks(self):
        def slow(*a, **k):
            import time
            time.sleep(0.3)
            return _ollama("NO")

        async def go():
            with mock.patch("urllib.request.urlopen", side_effect=slow), \
                    mock.patch.object(C, "_age_vision_deadline", return_value=0.05):
                return await C.age_vision_async(IMG)

        self.assertEqual(asyncio.run(go()), "age-vision-error")

    def test_cloud_key_keeps_the_scout_pin(self):
        with mock.patch.object(C, "vision_backend_available", return_value=True), \
                mock.patch.object(C, "_age_vision_post", return_value="NO") as cloud, \
                mock.patch.object(C, "_age_vision_post_ollama") as local:
            self.assertEqual(C._age_vision_sync(IMG), "")
        cloud.assert_called_once()
        local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
