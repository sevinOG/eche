"""Grok (xAI Responses API) web search provider and the provider switch.

No network: post_search is mocked. No image or media generation."""
from __future__ import annotations

import asyncio
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import client as C  # noqa: E402
from core import search_grok as G  # noqa: E402
from core import tools as T  # noqa: E402
from core import tools_web_search as W  # noqa: E402
from tests.test_web_search_gemini import reply as gemini_reply  # noqa: E402

XKEY = "xai-" + "K" * 40
GKEY = "AIza" + "Q" * 35
CTX = T.ToolContext(bot=None, message=None)


def grok_reply(text="Paris is the capital of France.", *, extra_output=None, citations=None, annotations=None):
    out = [
        {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        {"type": "x_search_call", "id": "xs_1", "status": "completed"},
        {"type": "message", "role": "assistant", "status": "completed", "content": [{
            "type": "output_text", "text": text,
            "annotations": annotations if annotations is not None else [
                {"type": "url_citation", "url": "https://example.org/a", "title": "1", "start_index": 0, "end_index": 5},
                {"type": "url_citation", "url": "https://www.example.com/b", "title": "Example B"},
            ],
        }]},
    ]
    out += extra_output or []
    return {"id": "r1", "object": "response", "status": "completed", "output": out,
            "citations": citations if citations is not None else
            ["https://example.org/a", "https://x.com/i/status/1", "https://example.net/c", "http://insecure.test"]}


class Base(unittest.TestCase):
    provider = "grok"

    def setUp(self):
        self.audits = []
        for p in (
            mock.patch.object(W, "search_provider", side_effect=lambda: self.provider),
            mock.patch.object(G, "xai_key", return_value=XKEY),
            mock.patch.object(W, "gemini_key", return_value=GKEY),
            mock.patch("core.audit.api", side_effect=lambda *a, **k: self.audits.append((a, k))),
        ):
            p.start()
            self.addCleanup(p.stop)

    def search(self, status=200, data=None, exc=None, gemini=(200, None)):
        self.calls = []

        async def grok_post(url, headers, body, key):
            self.calls.append(("grok", url, headers, body, key))
            if exc is not None:
                raise exc
            return status, data

        async def gemini_post(url, headers, body, key):
            self.calls.append(("gemini", url, headers, body, key))
            return gemini[0], gemini[1] if gemini[1] is not None else gemini_reply()

        out = io.StringIO()
        with mock.patch.object(G, "post_search", new=grok_post), \
                mock.patch.object(W, "post_search", new=gemini_post), \
                contextlib.redirect_stdout(out):
            result = asyncio.run(W.web_search(CTX, {"query": "capital of france"}))
        self.log = out.getvalue()
        return result


class RequestShape(unittest.TestCase):
    def test_responses_call_with_only_web_and_x_search(self):
        url, headers, body = G.build_request("capital of france")
        self.assertEqual(url, "https://api.x.ai/v1/responses")
        self.assertEqual(body["model"], G.GROK_MODEL)
        self.assertEqual(body["tools"], [{"type": "web_search"}, {"type": "x_search"}])
        self.assertEqual(body["input"], [{"role": "user", "content": "capital of france"}])
        self.assertFalse(body["store"])
        self.assertIn("no_inline_citations", body["include"])
        self.assertNotIn("Authorization", headers)
        flat = repr(body).lower()
        for needle in ("image", "video", "code_interpreter", "mcp", "collections", "file_search"):
            self.assertNotIn(needle, flat)

    def test_model_is_a_constant_text_model(self):
        self.assertEqual(G.GROK_MODEL, "grok-4.20-0309-non-reasoning")
        from core.media_scan import is_image_model_id
        self.assertFalse(is_image_model_id(G.GROK_MODEL))

    def test_key_only_in_the_bearer_header(self):
        seen = {}

        class Resp:
            status = 200

            async def json(self, content_type=None):
                return {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class Session:
            def __init__(self, timeout=None):
                pass

            def post(self, url, json=None, headers=None):
                seen.update(url=url, headers=headers, json=json)
                return Resp()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        url, headers, body = G.build_request("q")
        with mock.patch.object(G.aiohttp, "ClientSession", Session):
            asyncio.run(G.post_search(url, headers, body, XKEY))
        self.assertEqual(seen["headers"]["Authorization"], f"Bearer {XKEY}")
        self.assertNotIn(XKEY, seen["url"])
        self.assertNotIn(XKEY, repr(seen["json"]))


class GrokResults(Base):
    def test_answer_and_citations_in_the_shared_shape(self):
        result = self.search(data=grok_reply())
        self.assertEqual([c[0] for c in self.calls], ["grok"])
        self.assertEqual(result.outcome, "")
        self.assertTrue(result.for_model)
        self.assertIn("Paris is the capital of France.", result.text)
        self.assertIn("Sources:\n1. example.org — https://example.org/a", result.text)
        self.assertIn("2. Example B — https://www.example.com/b", result.text)
        self.assertIn("3. x.com — https://x.com/i/status/1", result.text)
        self.assertNotIn("insecure", result.text)
        self.assertEqual(result.footer, "")
        self.assertIn("provider: grok", result.detail)
        self.assertIn(f"model: {G.GROK_MODEL}", result.detail)
        row = self.audits[-1]
        self.assertEqual(row[0], ("grok-search",))
        self.assertEqual((row[1]["outcome"], row[1]["model"], row[1]["status"]), ("success", G.GROK_MODEL, "grounded"))
        self.assertEqual(self.calls[0][4], XKEY)

    def test_inline_links_and_markdown_images_are_stripped(self):
        text = "Paris [[1]](https://example.org/a) is the capital. ![pic](https://img.test/p.jpg)"
        result = self.search(data=grok_reply(text))
        self.assertIn("Paris is the capital.", result.text)
        self.assertNotIn("img.test", result.text)
        self.assertNotIn("[[1]]", result.text)

    def test_image_output_is_refused(self):
        for extra in ([{"type": "image_generation_call", "id": "ig"}],
                      [{"type": "message", "content": [{"type": "output_image", "url": "https://i"}]}]):
            result = self.search(data=grok_reply(extra_output=extra))
            self.assertEqual((result.outcome, result.status), ("refusal", "media"))
            self.assertNotIn("Paris", result.text)

    def test_empty_and_refusal_replies(self):
        self.assertEqual(self.search(data={"output": []}).text, C.LOOKUP_XAI_REFUSED)
        refusal = {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}
        self.assertEqual(self.search(data=refusal).status, "refused")
        self.assertEqual(self.search(data="junk").outcome, "error")


class GrokFailures(Base):
    def test_missing_key(self):
        with mock.patch.object(G, "xai_key", return_value=""):
            result = self.search(data=grok_reply())
        self.assertEqual(self.calls, [])
        self.assertEqual((result.outcome, result.status, result.text), ("refusal", "no_key", C.LOOKUP_NO_XAI_KEY))

    def test_http_failures(self):
        cases = [
            (400, {"code": "Client specified an invalid argument",
                   "error": f"Incorrect API key provided: {XKEY}. Obtain one from the console."}, "bad_key",
             C.LOOKUP_XAI_BAD_KEY),
            (401, {"error": {"message": "Unauthorized", "type": "invalid_request_error"}}, "bad_key",
             C.LOOKUP_XAI_BAD_KEY),
            (403, {"code": "Forbidden", "error": "Your team has either used all available credits or reached "
                                                  "its monthly spending limit."}, "no_credits", C.LOOKUP_XAI_CREDITS),
            (429, {"code": "Too many requests", "error": "Rate limit exceeded"}, "quota", C.LOOKUP_XAI_RATE),
            (404, {"code": "Not found", "error": "The model does not exist"}, "model_missing", C.LOOKUP_MODEL),
            (500, None, "unreachable", C.LOOKUP_XAI_UNREACHABLE),
        ]
        for http, body, status, text in cases:
            result = self.search(status=http, data=body)
            self.assertEqual((result.status, result.text), (status, text), http)
            self.assertEqual(len(self.calls), 1, http)  # no retry, no other provider
            self.assertEqual(self.audits[-1][1]["http_status"], str(http))
            self.assertIn(f"[web_search] {G.GROK_MODEL} HTTP {http}", self.log)
            for blob in (self.log, result.text, result.detail, repr(self.audits)):
                self.assertNotIn(XKEY, blob)
            for _, row in self.audits:
                self.assertNotIn("message", row)

    def test_error_message_is_scrubbed_and_logged(self):
        body = {"code": "Client specified an invalid argument", "error": f"bad key {XKEY} at https://a.test/?key={XKEY}"}
        result = self.search(status=400, data=body)
        self.assertIn("bad key [key]", self.log)
        self.assertNotIn(XKEY, self.log + result.detail)

    def test_timeout(self):
        result = self.search(exc=asyncio.TimeoutError())
        self.assertEqual((result.status, result.outcome, result.text), ("unreachable", "error", C.LOOKUP_XAI_UNREACHABLE))

    def test_new_messages_are_failed_lookups_and_status_lines(self):
        from core.tools_thread import _STATUS_LINE
        for text in (C.LOOKUP_NO_XAI_KEY, C.LOOKUP_XAI_BAD_KEY, C.LOOKUP_XAI_CREDITS, C.LOOKUP_XAI_RATE,
                     C.LOOKUP_XAI_UNREACHABLE, C.LOOKUP_XAI_REFUSED, C.LOOKUP_MODEL, C.LOOKUP_NO_GROUNDING):
            self.assertIn(text, C._LOOKUP_FAILED)
            self.assertTrue(_STATUS_LINE.match(text), text)


class ProviderSetting(unittest.TestCase):
    def test_default_is_grok(self):
        for stored, expected in (("", "grok"), ("grok", "grok"), ("Gemini", "gemini"), ("bing", "grok"), (None, "grok")):
            with mock.patch("core.secrets.public_value", return_value=stored):
                self.assertEqual(W.search_provider(), expected, stored)

    def test_xai_key_reuses_the_existing_secret(self):
        with mock.patch("core.secrets.load_all", return_value={"xai_api_key": f"  {XKEY} "}):
            self.assertEqual(G.xai_key(), XKEY)
            self.assertEqual(W.provider_key("grok"), XKEY)


class ProviderSwitch(Base):
    def test_switch_routes_to_the_selected_provider(self):
        self.provider = "gemini"
        result = self.search(data=grok_reply())
        self.assertEqual([c[0] for c in self.calls], ["gemini"])
        self.assertIn("provider: gemini", result.detail)
        self.assertEqual(self.calls[0][4], GKEY)
        self.provider = "grok"
        result = self.search(data=grok_reply())
        self.assertEqual([c[0] for c in self.calls], ["grok"])
        self.assertEqual(self.calls[0][4], XKEY)

    def test_offered_only_with_the_selected_providers_key(self):
        W.register_web_search_tool()
        tool = T._resolve("web_search")
        with mock.patch.object(T, "tool_prompt_enabled", return_value=True):
            self.provider = "grok"
            with mock.patch.object(G, "xai_key", return_value=""):
                self.assertFalse(T._offered(tool, public_on=True, speaker_is_owner=False, admin_enabled=False))
            self.assertTrue(T._offered(tool, public_on=True, speaker_is_owner=False, admin_enabled=False))
            self.provider = "gemini"
            with mock.patch.object(W, "gemini_key", return_value=""):
                self.assertFalse(T._offered(tool, public_on=True, speaker_is_owner=False, admin_enabled=False))

    def test_provider_setting_is_public_and_no_new_secret(self):
        from core import secrets as S
        self.assertIn("search_provider", S.PUBLIC_KEYS)
        self.assertNotIn("search_provider", S.SECRET_KEYS)
        self.assertEqual([k for k in S.SECRET_KEYS if "xai" in k], ["xai_api_key"])


if __name__ == "__main__":
    unittest.main()
