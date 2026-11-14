# tests/test_age_vision_local.py
# Finished-image vision checks: Grok vision on the xAI key first, then the
# hardcoded local Ollama vision model, then block. No settings. Must fail
# closed on a missing model, timeout, or anything but YES/NO, and keep the
# strict 18+ rules. No real images: only placeholder base64 bytes.
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

    def test_old_setting_is_ignored(self):
        with mock.patch("core.secrets.public_value", return_value="qwen2.5vl:3b"):
            self.assertEqual(C.age_vision_model(), "qwen3-vl:4b-instruct")
            self._run(_ollama("NO"))
        self.assertEqual(self.sent[0][1]["model"], "qwen3-vl:4b-instruct")

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
        for word in ("anime", "cartoon", "chibi", "doll", "real or identifiable person", "clearly an adult",
                     "If unsure, answer YES."):
            self.assertIn(word, prompt)
        # Images with no people or characters at all are not age-blocked in 18+ mode.
        self.assertIn("no people, characters, or human-like figures at all", prompt)

    def test_yes_blocks(self):
        self.assertEqual(self._run(_ollama("YES")), "age-vision-yes")

    def test_unclear_answers_block_and_name_the_field(self):
        for text in ("", "Maybe", "The person appears to be an adult.", "<think>x</think>"):
            with self.subTest(text=text):
                self.errors.clear()
                self.assertTrue(self._run(_ollama(text)))
        self.assertTrue(any("ollama pull qwen3-vl:4b-instruct" in e for e in self.errors))

    def test_missing_model_blocks_and_names_the_field(self):
        err = urllib.error.HTTPError(C.OLLAMA_CHAT_URL, 404, "not found", {}, None)
        with mock.patch.object(C, "_http_error_payload", return_value=(404, 'model "qwen3-vl:4b-instruct" not found')):
            self.assertEqual(self._run(side=err), "age-vision-error")
        self.assertTrue(self.errors)
        self.assertIn("ollama pull qwen3-vl:4b-instruct", self.errors[-1])
        self.assertNotIn("Settings", self.errors[-1])

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

    # ---- Grok vision first, local backup ---------------------------------

    def _xai(self, model="grok-vision-test"):
        return [
            mock.patch.object(C, "_xai_key", return_value="xai-test-key"),
            mock.patch.object(C, "_xai_vision_model", return_value=model),
        ]

    def _chain(self, xai_reply=None, xai_side=None, local_reply=None, local_side=None, adult=False, fn=None):
        calls = []

        def opener(req, timeout=None):
            body = json.loads(req.data.decode())
            calls.append((req.full_url, body, dict(req.headers)))
            if req.full_url.endswith("/chat/completions"):
                if xai_side is not None:
                    raise xai_side
                return _Resp({"choices": [{"message": {"content": xai_reply}}]})
            if local_side is not None:
                raise local_side
            return _ollama(local_reply)

        patches = self._xai()
        for p in patches:
            p.start()
        try:
            with mock.patch("urllib.request.urlopen", side_effect=opener):
                out = fn() if fn else C._age_vision_sync(IMG, adult=adult)
        finally:
            for p in patches:
                p.stop()
        return out, calls

    def test_xai_is_primary(self):
        out, calls = self._chain(xai_reply="NO")
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1)
        url, body, headers = calls[0]
        self.assertTrue(url.startswith(C._xai_api_base()))
        self.assertEqual(body["model"], "grok-vision-test")
        self.assertEqual(body["temperature"], 0)
        content = body["messages"][0]["content"]
        self.assertEqual(content[0]["image_url"]["url"], "data:image/png;base64,AAAA")
        self.assertEqual(content[-1]["text"], C.AGE_VISION_PROMPT)
        self.assertEqual(headers.get("Authorization"), "Bearer xai-test-key")
        self.assertEqual(C.age_vision_backend(), "ollama")  # outside the patch: no key

    def test_xai_yes_blocks_without_asking_local(self):
        out, calls = self._chain(xai_reply="YES", local_reply="NO")
        self.assertEqual(out, "age-vision-yes")
        self.assertEqual(len(calls), 1)

    def test_xai_adult_mode_uses_the_strict_prompt(self):
        out, calls = self._chain(xai_reply="NO", adult=True)
        self.assertEqual(calls[0][1]["messages"][0]["content"][-1]["text"], C.AGE_VISION_ADULT_PROMPT)

    def test_xai_failure_falls_back_to_local(self):
        out, calls = self._chain(xai_side=TimeoutError("slow"), local_reply="NO")
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0], C.OLLAMA_CHAT_URL)
        out, calls = self._chain(xai_side=TimeoutError("slow"), local_reply="YES")
        self.assertEqual(out, "age-vision-yes")

    def test_xai_unclear_reply_falls_back_to_local(self):
        out, calls = self._chain(xai_reply="I cannot say", local_reply="NO")
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 2)

    def test_both_failing_blocks(self):
        out, _ = self._chain(xai_side=OSError("down"), local_side=OSError("down"))
        self.assertEqual(out, "age-vision-error")
        out, _ = self._chain(xai_reply="maybe", local_reply="maybe")
        self.assertTrue(out)

    def test_no_xai_model_falls_back_to_local(self):
        patches = [mock.patch.object(C, "_xai_key", return_value="xai-test-key"),
                   mock.patch.object(C, "_xai_vision_model", return_value="")]
        for p in patches:
            p.start()
        try:
            out = self._run(_ollama("NO"))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(out, "")
        self.assertEqual(self.sent[0][0], C.OLLAMA_CHAT_URL)

    def test_model_picker_uses_image_input_text_output_and_skips_imagine(self):
        models = [
            {"id": "grok-imagine-image-2.0", "input_modalities": ["text", "image"], "output_modalities": ["image"]},
            {"id": "grok-imagine-x", "input_modalities": ["text", "image"], "output_modalities": ["text"]},
            {"id": "grok-text-only", "input_modalities": ["text"], "output_modalities": ["text"], "created": 99},
            {"id": "grok-4.7", "input_modalities": ["text", "image"], "output_modalities": ["text"], "created": 5},
            {"id": "grok-4.7-fast-non-reasoning", "input_modalities": ["text", "image"], "output_modalities": ["text"], "created": 4},
        ]
        self.assertEqual(C._pick_xai_vision_model(models), "grok-4.7-fast-non-reasoning")
        self.assertEqual(C._pick_xai_vision_model(models[:3]), "")
        self.assertEqual(C._pick_xai_vision_model([]), "")

    def test_model_list_is_queried_and_cached(self):
        C.reset_xai_vision_model_cache()
        payload = {"models": [{"id": "grok-vision-a", "input_modalities": ["text", "image"], "output_modalities": ["text"]}]}
        seen = []

        def opener(req, timeout=None):
            seen.append((req.full_url, req.get_method(), dict(req.headers)))
            return _Resp(payload)

        with mock.patch.object(C, "_xai_key", return_value="xai-test-key"), \
                mock.patch("urllib.request.urlopen", side_effect=opener):
            self.assertEqual(C._xai_vision_model(), "grok-vision-a")
            self.assertEqual(C._xai_vision_model(), "grok-vision-a")
        C.reset_xai_vision_model_cache()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0][0].endswith("/language-models"))
        self.assertEqual(seen[0][1], "GET")

    def test_image_check_chain(self):
        def image_check(adult=False):
            ignore = frozenset({"S12"}) if adult else None
            return lambda: C._classify_images_sync("", IMG, ignore)

        out, calls = self._chain(xai_reply="NO", fn=image_check())
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["messages"][0]["content"][-1]["text"], C.IMAGE_CHECK_PROMPT)
        out, calls = self._chain(xai_reply="YES", fn=image_check())
        self.assertEqual(out, "classifier-unsafe")
        out, calls = self._chain(xai_reply="NO", fn=image_check(adult=True))
        self.assertEqual(calls[0][1]["messages"][0]["content"][-1]["text"], C.IMAGE_CHECK_ADULT_PROMPT)
        out, calls = self._chain(xai_side=OSError("down"), local_reply="NO", fn=image_check())
        self.assertEqual(out, "")
        self.assertEqual(calls[1][0], C.OLLAMA_CHAT_URL)
        local = calls[1][1]
        self.assertEqual(local["model"], "qwen3-vl:4b-instruct")
        self.assertEqual(local["messages"][0]["images"], ["AAAA"])
        self.assertEqual(local["messages"][0]["content"], C.IMAGE_CHECK_PROMPT)
        out, _ = self._chain(xai_side=OSError("down"), local_side=OSError("down"), fn=image_check())
        self.assertEqual(out, "classifier-error")


if __name__ == "__main__":
    unittest.main()
