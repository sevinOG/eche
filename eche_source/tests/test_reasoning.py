# tests/test_reasoning.py
# Offline checks for Ollama thinking support. No network: requests.post is
# replaced with a fake Ollama that answers /api/show, /api/chat, and /v1.
#
# Run from eche_source:  python -m unittest tests.test_reasoning -v

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

BASE = "http://localhost:11434"


class FakeResponse:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body


class FakeOllama:
    """Scripted Ollama. chat_replies is consumed in order, one per chat call."""

    def __init__(self, caps=None, chat_replies=None, show_status=200, v1_replies=None):
        self.caps = caps
        self.show_status = show_status
        self.chat_replies = list(chat_replies or [])
        self.v1_replies = list(v1_replies or [])
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None, **_kw):
        self.calls.append((url, json))
        if url.endswith("/api/show"):
            if self.show_status != 200:
                return FakeResponse(self.show_status, {"error": "boom"})
            body = {"modelfile": "", "details": {}}
            if self.caps is not None:
                body["capabilities"] = list(self.caps)
            return FakeResponse(200, body)
        if url.endswith("/api/chat"):
            status, body = self.chat_replies.pop(0)
            return FakeResponse(status, body)
        if url.endswith("/v1/chat/completions"):
            status, body = self.v1_replies.pop(0)
            return FakeResponse(status, body)
        raise AssertionError(f"unexpected URL {url}")

    def chat_bodies(self) -> list[dict]:
        return [body for url, body in self.calls if not url.endswith("/api/show")]

    def show_count(self) -> int:
        return sum(1 for url, _ in self.calls if url.endswith("/api/show"))


def native(content="", thinking="", done_reason="stop", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if thinking:
        msg["thinking"] = thinking
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return 200, {"model": "m", "message": msg, "done": True, "done_reason": done_reason}


class ReasoningTestBase(unittest.TestCase):
    model = "qwen3:0.6b"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="eche_test_")
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        self.env = mock.patch.dict(os.environ, {
            "ECHE_USER_ROOT": self.root,
            "ECHE_PROVIDER": "ollama",
            "GROQ_MODEL": self.model,
            "OLLAMA_API_URL": "",
        })
        self.env.start()
        for name in ("OLLAMA_NUM_CTX", "ECHE_REASONING_BUDGET", "ECHE_REASONING_MODELS",
                     "ECHE_REASONING_TEMPERATURE", "ECHE_REASONING_TOP_P",
                     "ECHE_REASONING_TOP_K"):
            os.environ.pop(name, None)
        ot.clear_cache()
        self.traces: list[tuple[str, str, str]] = []
        gui_bridge.set_reasoning_handler(lambda p, m, t: self.traces.append((p, m, t)))
        self.settings({})

    def tearDown(self):
        gui_bridge.set_reasoning_handler(None)
        self.env.stop()
        ot.clear_cache()
        shutil.rmtree(self.root, ignore_errors=True)

    def settings(self, values: dict):
        with open(os.path.join(self.root, "config", "settings.json"), "w", encoding="utf-8") as fh:
            json.dump(values, fh)

    def run_post(self, fake: FakeOllama, *, reasoning=None, max_tokens=64, temperature=0.7):
        payload = asyncio.run(client._build_payload(
            [{"role": "user", "content": "hi"}], self.model,
            max_tokens=max_tokens, temperature=temperature, reasoning=reasoning,
        ))
        with mock.patch("requests.post", fake.post):
            data = client._sync_post(client._api_url(), payload, client._headers())
        return payload, data

    def reply_of(self, data) -> str:
        return client._message_text(client._extract_message(data))


class CapableModelTests(ReasoningTestBase):
    def test_capable_model_gets_think_budget_ctx_and_qwen_sampling(self):
        self.settings({"reasoning_ollama": "1", "ollama_num_ctx": "4096",
                       "reasoning_budget": "512"})
        fake = FakeOllama(caps=["completion", "tools", "thinking"],
                          chat_replies=[native("Five.", thinking="2+3 is 5")])
        _payload, data = self.run_post(fake)
        body = fake.chat_bodies()[0]
        self.assertTrue(fake.calls[-1][0].endswith("/api/chat"))
        self.assertIs(body["think"], True)
        self.assertEqual(body["options"]["num_ctx"], 4096)
        self.assertEqual(body["options"]["num_predict"], 64 + 512)
        self.assertEqual(body["options"]["temperature"], 0.6)
        self.assertEqual(body["options"]["top_p"], 0.95)
        self.assertEqual(body["options"]["top_k"], 20)
        self.assertEqual(self.reply_of(data), "Five.")
        self.assertEqual([t[2] for t in self.traces], ["2+3 is 5"])

    def test_capable_model_switch_off_sends_think_false(self):
        fake = FakeOllama(caps=["completion", "thinking"], chat_replies=[native("Hi.")])
        self.run_post(fake)
        body = fake.chat_bodies()[0]
        self.assertIs(body["think"], False)
        self.assertEqual(body["options"]["num_predict"], 64)
        self.assertEqual(body["options"]["num_ctx"], 8192)
        self.assertEqual(body["options"]["temperature"], 0.7)
        self.assertNotIn("top_k", body["options"])

    def test_background_call_is_forced_off(self):
        self.settings({"reasoning_ollama": "1", "reasoning_models": "qwen3:0.6b=on"})
        fake = FakeOllama(caps=["completion", "thinking"], chat_replies=[native("ok")])
        self.run_post(fake, reasoning=False, temperature=0.4)
        body = fake.chat_bodies()[0]
        self.assertIs(body["think"], False)
        self.assertEqual(body["options"]["temperature"], 0.4)

    def test_capabilities_are_cached(self):
        self.settings({"reasoning_ollama": "1"})
        fake = FakeOllama(caps=["completion", "thinking"],
                          chat_replies=[native("a", thinking="t"), native("b", thinking="t")])
        self.run_post(fake)
        self.run_post(fake)
        self.assertEqual(fake.show_count(), 1)

    def test_sampling_overrides_win(self):
        self.settings({"reasoning_ollama": "1", "reasoning_temperature": "0.3",
                       "reasoning_top_k": "40", "reasoning_top_p": "junk"})
        fake = FakeOllama(caps=["completion", "thinking"],
                          chat_replies=[native("x", thinking="t")])
        self.run_post(fake)
        opts = fake.chat_bodies()[0]["options"]
        self.assertEqual(opts["temperature"], 0.3)
        self.assertEqual(opts["top_k"], 40)
        self.assertEqual(opts["top_p"], 0.95)

    def test_async_path_matches(self):
        self.settings({"reasoning_ollama": "1"})
        fake = FakeOllama(caps=["completion", "thinking"])
        captured = {}

        class _Resp:
            status = 200

            async def text(self):
                return json.dumps(native("Async reply.", thinking="t")[1])

            async def json(self, content_type=None):
                return native("Async reply.", thinking="t")[1]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _Session:
            def __init__(self, *a, **k):
                pass

            def post(self, url, json=None, headers=None):
                captured["url"], captured["body"] = url, json
                return _Resp()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        payload = asyncio.run(client._build_payload([{"role": "user", "content": "hi"}], self.model, 64, 0.7))
        with mock.patch("requests.post", fake.post), \
                mock.patch.object(client.aiohttp, "ClientSession", _Session):
            data = asyncio.run(client._async_post(client._api_url(), payload, client._headers()))
        self.assertEqual(fake.show_count(), 1)
        self.assertTrue(captured["url"].endswith("/api/chat"))
        self.assertIs(captured["body"]["think"], True)
        self.assertEqual(self.reply_of(data), "Async reply.")


class IncapableModelTests(ReasoningTestBase):
    model = "gemma3:12b-abliterated"

    def test_incapable_model_never_gets_think(self):
        self.settings({"reasoning_ollama": "1"})
        fake = FakeOllama(caps=["completion", "vision"], chat_replies=[native("Hello there.")])
        _payload, data = self.run_post(fake)
        body = fake.chat_bodies()[0]
        self.assertNotIn("think", body)
        self.assertEqual(body["options"]["num_predict"], 64)
        self.assertEqual(body["options"]["temperature"], 0.7)
        self.assertEqual(self.reply_of(data), "Hello there.")

    def test_incapable_model_switch_off_also_omits(self):
        fake = FakeOllama(caps=["completion"], chat_replies=[native("Hi.")])
        self.run_post(fake)
        self.assertNotIn("think", fake.chat_bodies()[0])

    def test_forced_on_still_omits_for_listed_incapable(self):
        self.settings({"reasoning_models": "gemma3:12b-abliterated=on"})
        fake = FakeOllama(caps=["completion"], chat_replies=[native("Hi.")])
        self.run_post(fake)
        self.assertNotIn("think", fake.chat_bodies()[0])


class RetryTests(ReasoningTestBase):
    def test_reject_think_retries_without_field(self):
        # /api/show fails, so capabilities are unknown. An explicit "on" still asks.
        self.settings({"reasoning_models": "qwen3:0.6b=on"})
        fake = FakeOllama(show_status=500, chat_replies=[
            (400, {"error": '"qwen3:0.6b" does not support thinking'}),
            native("Plain reply."),
        ])
        payload, data = self.run_post(fake)
        first, second = fake.chat_bodies()
        self.assertIs(first["think"], True)
        self.assertNotIn("think", second)
        self.assertEqual(second["options"]["num_predict"], 64)
        self.assertEqual(self.reply_of(data), "Plain reply.")
        # The refusal is remembered: the next request does not send think.
        self.assertFalse(ot.can_think(ot.cached_capabilities(BASE, self.model)))
        fake2 = FakeOllama(show_status=500, chat_replies=[native("Again.")])
        self.run_post(fake2)
        self.assertNotIn("think", fake2.chat_bodies()[0])

    def test_empty_reply_retries_with_think_off(self):
        self.settings({"reasoning_ollama": "1", "reasoning_budget": "256"})
        fake = FakeOllama(caps=["completion", "thinking"], chat_replies=[
            native("", thinking="long trace that ate the cap", done_reason="length"),
            native("The actual answer."),
        ])
        payload, data = self.run_post(fake)
        first, second = fake.chat_bodies()
        self.assertIs(first["think"], True)
        self.assertEqual(first["options"]["num_predict"], 64 + 256)
        self.assertIs(second["think"], False)
        self.assertEqual(second["options"]["num_predict"], 64)
        self.assertEqual(self.reply_of(data), "The actual answer.")
        self.assertEqual(len(self.traces), 1)
        self.assertEqual(len(fake.chat_bodies()), 2)

    def test_empty_reply_without_think_does_not_retry(self):
        fake = FakeOllama(caps=["completion"], chat_replies=[
            native("<think>only thinking, never closed", done_reason="length"),
        ])
        _payload, data = self.run_post(fake)
        self.assertEqual(len(fake.chat_bodies()), 1)
        self.assertEqual(self.reply_of(data), "")


class V1FallbackTests(ReasoningTestBase):
    def test_native_missing_falls_back_to_v1_and_strips_think(self):
        self.settings({"reasoning_ollama": "1"})
        fake = FakeOllama(
            caps=["completion", "thinking"],
            chat_replies=[(404, "404 page not found")],
            v1_replies=[(200, {"choices": [{
                "message": {"role": "assistant",
                            "content": "<think>\nweighing it up\n</think>\n\nHello from v1."},
                "finish_reason": "stop",
            }]})],
        )
        _payload, data = self.run_post(fake)
        urls = [url for url, _ in fake.calls if not url.endswith("/api/show")]
        self.assertTrue(urls[0].endswith("/api/chat"))
        self.assertTrue(urls[1].endswith("/v1/chat/completions"))
        v1_body = fake.chat_bodies()[1]
        self.assertNotIn("options", v1_body)
        self.assertNotIn("think", v1_body)
        self.assertNotIn("reasoning_effort", v1_body)
        self.assertEqual(v1_body["top_p"], 0.95)
        self.assertEqual(self.reply_of(data), "Hello from v1.")
        self.assertEqual(client._visible_reply(self.reply_of(data)), "Hello from v1.")
        self.assertEqual([t[2] for t in self.traces], ["weighing it up"])

    def test_proxy_url_uses_v1_shape_and_off_sends_effort_none(self):
        # A URL that is neither /v1/chat/completions nor /api/chat is an
        # OpenAI-style proxy. It keeps the /v1 request shape from the start.
        os.environ["OLLAMA_API_URL"] = "http://proxy.local/openai/chat"
        fake = FakeOllama(caps=["completion", "thinking"])
        reply = {"choices": [{"message": {"role": "assistant",
                                          "content": "<think>x</think>Short."},
                              "finish_reason": "stop"}]}
        fake.post_orig = fake.post

        def post(url, json=None, headers=None, timeout=None, **_kw):
            if url.endswith("/api/show"):
                return fake.post_orig(url, json=json)
            fake.calls.append((url, json))
            return FakeResponse(200, reply)

        payload = asyncio.run(client._build_payload([{"role": "user", "content": "hi"}], self.model, 64, 0.7))
        with mock.patch("requests.post", post):
            data = client._sync_post(client._api_url(), payload, client._headers())
        url, body = fake.calls[-1]
        self.assertEqual(url, "http://proxy.local/openai/chat")
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertNotIn("think", body)
        self.assertEqual(self.reply_of(data), "Short.")

    def test_model_not_found_404_is_not_a_fallback(self):
        fake = FakeOllama(caps=None, chat_replies=[
            (404, {"error": 'model "qwen3:0.6b" not found, try pulling it first'}),
        ])
        _payload, data = self.run_post(fake)
        self.assertIn("error", data)
        self.assertEqual(len(fake.chat_bodies()), 1)


class ModeAndSettingsTests(unittest.TestCase):
    def test_resolve_think_table(self):
        yes, no, unknown = frozenset({"thinking"}), frozenset({"completion"}), None
        cases = {
            ("auto", yes): True, ("auto", no): None, ("auto", unknown): None,
            ("on", yes): True, ("on", no): None, ("on", unknown): True,
            ("off", yes): False, ("off", no): None, ("off", unknown): None,
            ("high", yes): "high", ("low", no): None, ("medium", unknown): "medium",
        }
        for (mode, caps), want in cases.items():
            self.assertEqual(ot.resolve_think(mode, caps), want, (mode, caps))

    def test_parse_and_format_model_modes(self):
        parsed = ot.parse_model_modes("Qwen3.5:9B=on; gemma3:12b=off\nfoo=bogus;gpt-oss:20b=high")
        self.assertEqual(parsed, {"qwen3.5:9b": "on", "gemma3:12b": "off", "gpt-oss:20b": "high"})
        self.assertEqual(ot.parse_model_modes('{"granite4.1:8b": "auto"}'), {"granite4.1:8b": "auto"})
        self.assertEqual(ot.format_model_modes({"b": "on", "a": "0"}), "a=off; b=on")
        self.assertEqual(ot.parse_model_modes(ot.format_model_modes(parsed)), parsed)

    def test_model_mode_precedence(self):
        modes = {"qwen3:0.6b": "off", "llama3": "on"}
        self.assertEqual(ot.model_mode("qwen3:0.6b", True, modes), "off")
        self.assertEqual(ot.model_mode("llama3:latest", False, modes), "on")
        self.assertEqual(ot.model_mode("other", True, modes), "auto")
        self.assertEqual(ot.model_mode("other", False, modes), "off")

    def test_clamps(self):
        self.assertEqual(ot.clamp_int("", 8192, 512, 262144), 8192)
        self.assertEqual(ot.clamp_int("100", 8192, 512, 262144), 512)
        self.assertEqual(ot.clamp_int("16384", 8192, 512, 262144), 16384)
        self.assertEqual(ot.clamp_int("abc", 1024, 0, 8192), 1024)

    def test_qwen_family(self):
        for name in ("qwen3:0.6b", "huihui_ai/qwen3.5-abliterated:27b", "qwq:32b"):
            self.assertTrue(ot.is_qwen_family(name), name)
        for name in ("gemma3:12b", "granite4.1:8b", "gpt-oss:20b"):
            self.assertFalse(ot.is_qwen_family(name), name)
        self.assertEqual(ot.thinking_sampling("gemma3:12b", 0.7), {"temperature": 0.7})

    def test_new_keys_are_public_settings(self):
        from core.secrets import PUBLIC_KEYS
        for key in ("reasoning_budget", "ollama_num_ctx", "reasoning_models",
                    "reasoning_temperature", "reasoning_top_p", "reasoning_top_k"):
            self.assertIn(key, PUBLIC_KEYS)

    def test_native_url_mapping(self):
        self.assertEqual(client._ollama_native_url(BASE + "/v1/chat/completions"), BASE + "/api/chat")
        self.assertEqual(client._ollama_native_url(BASE + "/api/chat/"), BASE + "/api/chat")
        self.assertIsNone(client._ollama_native_url("http://proxy/chat"))
        self.assertEqual(ot.base_url(BASE + "/v1/chat/completions"), BASE)
        self.assertEqual(ot.base_url(BASE + "/api/chat"), BASE)


if __name__ == "__main__":
    unittest.main()
