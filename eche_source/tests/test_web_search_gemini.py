"""Gemini web search with Google Search grounding (owner 2026-11-23).

No network: post_search is mocked. No image or media generation."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import client as C  # noqa: E402
from core import tools as T  # noqa: E402
from core import tools_web_search as W  # noqa: E402

KEY = "AIza" + "Q" * 35
CTX = T.ToolContext(bot=None, message=None)


def run(coro):
    return asyncio.run(coro)


def reply(text="Paris is the capital of France.", *, chunks=None, queries=None, meta=True, finish="STOP"):
    cand = {"content": {"parts": [{"text": text}]}, "finishReason": finish}
    if meta:
        cand["groundingMetadata"] = {
            "groundingChunks": chunks if chunks is not None else [
                {"web": {"uri": "https://example.org/a", "title": "Example A"}},
                {"web": {"uri": "https://example.org/b", "title": "Example B"}},
                {"web": {"uri": "https://example.org/b2", "title": "Example B"}},
                {"web": {"uri": "http://insecure.example", "title": "Insecure"}},
                {"web": {"uri": "https://example.org/c", "title": "Example C"}},
                {"web": {"uri": "https://example.org/d", "title": "Example D"}},
            ],
            "webSearchQueries": queries if queries is not None else ["capital of france", "france capital city", "x"],
        }
    return {"candidates": [cand]}


class Base(unittest.TestCase):
    def setUp(self):
        self.audits = []
        patches = [
            mock.patch.object(W, "gemini_key", return_value=KEY),
            mock.patch.object(W, "search_model", return_value=W.DEFAULT_MODEL),
            mock.patch("core.audit.api", side_effect=lambda *a, **k: self.audits.append((a, k))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def search(self, status=200, data=None, exc=None, query="capital of france"):
        calls = []

        async def post(url, headers, body, key):
            calls.append((url, headers, body, key))
            if exc is not None:
                raise exc
            return status, data

        with mock.patch.object(W, "post_search", new=post):
            result = run(W.web_search(CTX, {"query": query}))
        self.calls = calls
        return result


class RequestShape(unittest.TestCase):
    def test_grounded_text_request_key_not_in_url_or_body(self):
        url, headers, body = W.build_request("capital of france", W.DEFAULT_MODEL)
        self.assertEqual(
            url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent")
        self.assertEqual(body["tools"], [{"google_search": {}}])
        self.assertNotIn("key=", url)
        self.assertNotIn("x-goog-api-key", headers)
        flat = repr(body)
        self.assertNotIn("responseModalities", flat)
        self.assertNotIn("IMAGE", flat)
        self.assertNotIn("generationConfig", flat.replace("'generationConfig'", "", 1))
        self.assertEqual(set(body["generationConfig"]), {"temperature", "maxOutputTokens"})

    def test_post_search_puts_the_key_in_the_header(self):
        seen = {}

        class Resp:
            status = 200

            async def json(self, content_type=None):
                return {"ok": 1}

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

        with mock.patch.object(W.aiohttp, "ClientSession", Session):
            status, data = run(W.post_search("https://u", {"Content-Type": "application/json"}, {"b": 1}, KEY))
        self.assertEqual((status, data), (200, {"ok": 1}))
        self.assertEqual(seen["headers"]["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, seen["url"])

    def test_module_has_no_media_output_markers(self):
        src = Path(W.__file__).read_text(encoding="utf-8")
        for needle in ("responseModalities", "response_modalities", "google.genai", "generate_images", "generateImages"):
            self.assertNotIn(needle, src)


class Results(Base):
    def test_answer_sources_and_links(self):
        result = self.search(data=reply())
        self.assertEqual(result.outcome, "")
        self.assertTrue(result.for_model)
        self.assertIn("Paris is the capital of France.", result.text)
        self.assertIn("Sources:\n1. Example A — https://example.org/a", result.text)
        self.assertIn("3. Example C — https://example.org/c", result.text)
        self.assertNotIn("Insecure", result.text)
        self.assertNotIn("Example D", result.text)
        self.assertEqual(result.text.count("Example B"), 1)
        self.assertTrue(result.footer.startswith("-# Google Search: "))
        self.assertIn("(<https://www.google.com/search?q=capital+of+france>)", result.footer)
        self.assertIn("france capital city", result.footer)
        self.assertNotIn("[x]", result.footer)  # two links at most
        self.assertEqual(self.audits[-1][1]["outcome"], "success")
        self.assertEqual(self.audits[-1][1]["status"], "grounded")
        self.assertEqual(self.calls[0][3], KEY)

    def test_ungrounded_answer_is_marked(self):
        result = self.search(data=reply(meta=False))
        self.assertIn("Paris", result.text)
        self.assertEqual(result.footer, "")
        self.assertIn("not grounded", result.detail)
        self.assertEqual(self.audits[-1][1]["status"], "ungrounded")

    def test_media_parts_are_refused(self):
        data = {"candidates": [{"content": {"parts": [{"text": "x"}, {"inlineData": {"mimeType": "image/png"}}]}}]}
        result = self.search(data=data)
        self.assertEqual(result.outcome, "refusal")
        self.assertEqual(result.status, "media")
        self.assertNotIn("x", result.footer)

    def test_blocked_prompt_and_empty_reply(self):
        self.assertEqual(self.search(data={"promptFeedback": {"blockReason": "SAFETY"}}).outcome, "refusal")
        self.assertEqual(self.search(data=reply("", finish="SAFETY")).status, "refused")
        self.assertEqual(self.search(data={"candidates": []}).outcome, "error")


class Failures(Base):
    def _err(self, http, reason="", message=""):
        payload = {"error": {"message": message, "status": "X",
                             "details": [{"reason": reason}] if reason else []}}
        return self.search(status=http, data=payload)

    def test_no_key(self):
        with mock.patch.object(W, "gemini_key", return_value=""):
            result = run(W.web_search(CTX, {"query": "q"}))
        self.assertEqual((result.outcome, result.status), ("refusal", "no_key"))
        self.assertEqual(result.text, C.LOOKUP_NO_KEY)

    def test_http_failures_map_to_clear_messages_and_outcomes(self):
        cases = [
            (400, "API_KEY_INVALID", "API key not valid.", "bad_key", "refusal", C.LOOKUP_BAD_KEY),
            (403, "", "Search grounding is not available on the free tier.", "no_grounding", "refusal",
             C.LOOKUP_NO_GROUNDING),
            (429, "", "Resource exhausted", "quota", "refusal", C.LOOKUP_QUOTA),
            (404, "", "model not found", "model_missing", "error", C.LOOKUP_MODEL),
            (500, "", "", "unreachable", "error", C.LOOKUP_UNREACHABLE),
        ]
        for http, reason, message, status, outcome, text in cases:
            result = self._err(http, reason, message)
            self.assertEqual((result.status, result.outcome, result.text), (status, outcome, text), http)
            self.assertEqual(self.audits[-1][1]["outcome"], outcome)
            self.assertEqual(self.audits[-1][1]["http_status"], str(http))
            self.assertNotIn(message or "zzz", repr(self.audits[-1]))

    def test_timeout_is_an_error(self):
        result = self.search(exc=asyncio.TimeoutError())
        self.assertEqual((result.status, result.outcome), ("unreachable", "error"))
        self.assertEqual(result.text, C.LOOKUP_UNREACHABLE)

    def test_new_messages_count_as_failed_lookups(self):
        for text in (C.LOOKUP_NO_KEY, C.LOOKUP_BAD_KEY, C.LOOKUP_NO_GROUNDING, C.LOOKUP_QUOTA,
                     C.LOOKUP_MODEL, C.LOOKUP_UNREACHABLE, C.LOOKUP_REFUSED):
            self.assertIn(text, C._LOOKUP_FAILED)

    def test_key_never_in_detail_audit_or_text(self):
        for http, data in ((200, reply()), (400, {"error": {"message": "API key not valid. " + KEY}})):
            result = self.search(status=http, data=data)
            for blob in (result.text, result.detail, result.footer, repr(self.audits)):
                self.assertNotIn(KEY, blob)

    def test_audit_redacts_gemini_keys(self):
        from core import audit as A
        red = A.redact({"gemini_api_key": KEY, "x-goog-api-key": KEY, "note": f"key {KEY}"})
        self.assertNotIn(KEY, repr(red))


class Registry(unittest.TestCase):
    def setUp(self):
        W.register_web_search_tool()

    def test_model_allowlist(self):
        for ok in ("gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-3-pro-preview"):
            self.assertTrue(W.text_model_ok(ok), ok)
        for bad in ("gemini-2.5-flash-image", "gemini-3-pro-image-preview", "imagen-4", "veo-3",
                    "gemini-2.5-flash-preview-tts", "gemini-live-2.5", "gemini-embedding-001",
                    "gpt-4o", "", "gemini-x/../y", "nano-banana"):
            self.assertFalse(W.text_model_ok(bad), bad)

    def test_bad_saved_model_falls_back(self):
        with mock.patch("core.secrets.public_value", return_value="gemini-2.5-flash-image"):
            self.assertEqual(W.search_model(), W.DEFAULT_MODEL)
        with mock.patch("core.secrets.public_value", return_value=""):
            self.assertEqual(W.search_model(), W.DEFAULT_MODEL)
        with mock.patch("core.secrets.public_value", return_value="Gemini-2.5-Flash"):
            self.assertEqual(W.search_model(), "gemini-2.5-flash")

    def test_duckduckgo_alias_resolves(self):
        self.assertIs(T._resolve("duckduckgo"), T._resolve("web_search"))
        self.assertNotIn("duckduckgo", T.registered_names())

    def test_old_lookup_checkbox_still_applies(self):
        values = {"tool_web_search": "", "tool_duckduckgo": "0"}
        with mock.patch("core.secrets.public_value", side_effect=lambda k: values.get(k, "")):
            self.assertFalse(T.tool_prompt_enabled("web_search"))
        values = {"tool_web_search": "1", "tool_duckduckgo": "0"}
        with mock.patch("core.secrets.public_value", side_effect=lambda k: values.get(k, "")):
            self.assertTrue(T.tool_prompt_enabled("web_search"))

    def test_not_offered_without_a_key(self):
        tool = T._resolve("web_search")
        with mock.patch.object(T, "tool_prompt_enabled", return_value=True):
            with mock.patch.object(W, "gemini_key", return_value=""):
                self.assertFalse(T._offered(tool, public_on=True, speaker_is_owner=False, admin_enabled=False))
            with mock.patch.object(W, "gemini_key", return_value=KEY):
                self.assertTrue(T._offered(tool, public_on=True, speaker_is_owner=False, admin_enabled=False))

    def test_failed_search_is_not_audited_as_success(self):
        rows = []
        with mock.patch.object(T, "default_tools_enabled", return_value=True), \
                mock.patch.object(T, "tool_prompt_enabled", return_value=True), \
                mock.patch.object(T, "_audit_tool", side_effect=lambda name, outcome, ctx, **kw: rows.append(outcome)), \
                mock.patch.object(W, "gemini_key", return_value=""):
            run(T.execute("duckduckgo", {"query": "q"}, CTX))
        self.assertEqual(rows, ["refusal"])

    def test_secret_and_public_keys(self):
        from core import secrets as S
        self.assertIn("gemini_api_key", S.SECRET_KEYS)
        self.assertNotIn("gemini_api_key", S.PUBLIC_KEYS)
        self.assertIn("gemini_search_model", S.PUBLIC_KEYS)
        self.assertIn("tool_web_search", S.PUBLIC_KEYS)
        self.assertNotIn("gemini_api_key", repr(getattr(S, "ENV_MAP", {})))


if __name__ == "__main__":
    unittest.main()
