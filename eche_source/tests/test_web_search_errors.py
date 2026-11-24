"""Gemini web search: error-body logging and the one free-tier fallback.

No network: post_search is mocked."""
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
from core import tools as T  # noqa: E402
from core import tools_web_search as W  # noqa: E402
from tests.test_web_search_gemini import KEY, reply  # noqa: E402

CTX = T.ToolContext(bot=None, message=None)
HOST = "generativelanguage" + ".googleapis.com"


def quota_body(limit="0", quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier", extra_message=""):
    return {"error": {
        "code": 429,
        "status": "RESOURCE_EXHAUSTED",
        "message": (f"You exceeded your current quota. Quota exceeded for metric: {HOST}/"
                    f"generate_content_free_tier_requests, limit: {limit}, model: gemini-3.5-flash-lite. "
                    f"See https://{HOST}/v1beta/models?key={KEY} and key {KEY}. " + extra_message),
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{
                "quotaMetric": f"{HOST}/generate_content_free_tier_requests",
                "quotaId": quota_id,
                "quotaDimensions": {"location": "global", "model": "gemini-3.5-flash-lite"},
                "quotaValue": limit,
            }]},
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "RATE_LIMIT_EXCEEDED"},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"},
        ],
    }}


class Base(unittest.TestCase):
    def setUp(self):
        self.audits = []
        for p in (
            mock.patch.object(W, "gemini_key", return_value=KEY),
            mock.patch.object(W, "search_model", return_value=W.DEFAULT_MODEL),
            mock.patch("core.audit.api", side_effect=lambda *a, **k: self.audits.append((a, k))),
        ):
            p.start()
            self.addCleanup(p.stop)

    def search(self, responses, model=None):
        """responses: {model: (status, body)}; records the models called."""
        self.models = []

        async def post(url, headers, body, key):
            name = url.split("/models/", 1)[1].split(":", 1)[0]
            self.models.append(name)
            self.assertNotIn(KEY, url)
            self.assertNotIn("x-goog-api-key", headers)
            return responses[name]

        out = io.StringIO()
        patches = [mock.patch.object(W, "post_search", new=post)]
        if model:
            patches.append(mock.patch.object(W, "search_model", return_value=model))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            result = asyncio.run(W.web_search(CTX, {"query": "capital of france"}))
        self.log = out.getvalue()
        return result


class ErrorBody(unittest.TestCase):
    def test_parses_status_message_quota_reason_retry(self):
        info = W.error_info(quota_body(), KEY)
        self.assertEqual(info["status"], "RESOURCE_EXHAUSTED")
        self.assertEqual(info["reason"], "RATE_LIMIT_EXCEEDED")
        self.assertEqual(info["retry"], "34s")
        self.assertEqual(info["violations"], [{
            "quotaMetric": f"{HOST}/generate_content_free_tier_requests",
            "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            "limit": "0",
        }])
        self.assertLessEqual(len(info["message"]), 300)
        self.assertNotIn(KEY, info["message"])
        self.assertNotIn("key=", info["message"].replace("key=[redacted]", ""))
        self.assertIn("limit: 0", info["message"])

    def test_scrub(self):
        out = W.scrub(f"bad {KEY} at https://x.test/a?alt=json&key={KEY} key=abc " + "x" * 400, KEY)
        self.assertNotIn(KEY, out)
        self.assertNotIn("abc", out)
        self.assertIn("[url removed]", out)
        self.assertLessEqual(len(out), 300)
        self.assertNotIn("AIza", W.scrub("AIza" + "Z" * 35))

    def test_quota_kinds(self):
        self.assertEqual(W.quota_kind(W.error_info(quota_body("0"))), "free_zero")
        self.assertEqual(W.quota_kind(W.error_info(quota_body("500"))), "free")
        self.assertEqual(W.quota_kind(W.error_info(quota_body(
            "15", "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))), "rate")
        self.assertEqual(W.quota_kind(W.error_info(quota_body(
            "1500", "GenerateRequestsPerDayPerProjectPerModel"))), "daily")
        self.assertEqual(W.quota_kind(W.error_info({"error": {"message": "Resource exhausted"}})), "")

    def test_odd_bodies_do_not_break(self):
        for body in (None, "x", {"error": "x"}, {"error": {"details": ["x", {"violations": ["y"]}]}}):
            W.error_info(body)
            self.assertEqual(W.classify_http(500, body).status, "unreachable")


class Fallback(Base):
    def test_429_falls_back_once_and_logs_which_model_answered(self):
        result = self.search({
            W.DEFAULT_MODEL: (429, quota_body()),
            W.FALLBACK_MODEL: (200, reply()),
        })
        self.assertEqual(self.models, [W.DEFAULT_MODEL, W.FALLBACK_MODEL])
        self.assertEqual(result.outcome, "")
        self.assertIn("Paris", result.text)
        self.assertIn(f"model: {W.FALLBACK_MODEL} (fallback after HTTP 429 on {W.DEFAULT_MODEL})", result.detail)
        self.assertIn(f"[web_search] answered by {W.FALLBACK_MODEL}", self.log)
        self.assertIn(f"[web_search] {W.DEFAULT_MODEL} HTTP 429 RESOURCE_EXHAUSTED RATE_LIMIT_EXCEEDED:", self.log)
        self.assertIn("quota: metric=", self.log)
        self.assertIn("id=GenerateRequestsPerDayPerProjectPerModel-FreeTier limit=0", self.log)
        self.assertIn("retry after 34s", self.log)
        rows = [k for _, k in self.audits]
        self.assertEqual([(r["model"], r["outcome"], r["http_status"]) for r in rows],
                         [(W.DEFAULT_MODEL, "refusal", "429"), (W.FALLBACK_MODEL, "success", "200")])
        self.assertEqual(rows[0]["reason"], "free_zero")

    def test_403_no_grounding_falls_back(self):
        body = {"error": {"code": 403, "status": "PERMISSION_DENIED",
                          "message": "Grounding with Google Search is not available on the free tier."}}
        self.search({W.DEFAULT_MODEL: (403, body), W.FALLBACK_MODEL: (200, reply())})
        self.assertEqual(self.models, [W.DEFAULT_MODEL, W.FALLBACK_MODEL])

    def test_no_fallback_for_other_errors(self):
        for http, body in ((404, {"error": {"message": "not found"}}), (500, {}),
                           (400, {"error": {"message": "API key not valid", "details": [{"reason": "API_KEY_INVALID"}]}})):
            self.search({W.DEFAULT_MODEL: (http, body)})
            self.assertEqual(self.models, [W.DEFAULT_MODEL], http)

    def test_no_loop_when_the_fallback_model_is_configured(self):
        result = self.search({W.FALLBACK_MODEL: (429, quota_body("500"))}, model=W.FALLBACK_MODEL)
        self.assertEqual(self.models, [W.FALLBACK_MODEL])
        self.assertEqual(result.text, C.LOOKUP_QUOTA_FREE)

    def test_fallback_failure_says_billing_when_limit_is_zero(self):
        result = self.search({
            W.DEFAULT_MODEL: (429, quota_body("0")),
            W.FALLBACK_MODEL: (429, quota_body("500")),
        })
        self.assertEqual(self.models, [W.DEFAULT_MODEL, W.FALLBACK_MODEL])  # exactly two calls
        self.assertEqual((result.outcome, result.status), ("refusal", "quota"))
        self.assertEqual(result.text, C.LOOKUP_QUOTA_FREE_ZERO)
        self.assertIn("billing", result.text)
        self.assertIn(f"[web_search] {W.FALLBACK_MODEL} also failed (HTTP 429)", self.log)
        self.assertIn(f"tried: fallback after HTTP 429 on {W.DEFAULT_MODEL}", result.detail)

    def test_fallback_failure_real_daily_quota(self):
        daily = quota_body("1500", "GenerateRequestsPerDayPerProjectPerModel")
        result = self.search({W.DEFAULT_MODEL: (429, daily), W.FALLBACK_MODEL: (429, daily)})
        self.assertEqual(result.text, C.LOOKUP_QUOTA)

    def test_key_never_in_log_detail_text_or_audit(self):
        for responses in (
            {W.DEFAULT_MODEL: (429, quota_body()), W.FALLBACK_MODEL: (429, quota_body())},
            {W.DEFAULT_MODEL: (429, quota_body()), W.FALLBACK_MODEL: (200, reply())},
        ):
            result = self.search(responses)
            for blob in (self.log, result.text, result.detail, repr(self.audits)):
                self.assertNotIn(KEY, blob)
                self.assertNotIn("x-goog-api-key", blob)
            for _, row in self.audits:
                self.assertNotIn("message", row)

    def test_new_messages_are_failed_lookups_and_status_lines(self):
        from core.tools_thread import _STATUS_LINE
        for text in (C.LOOKUP_QUOTA_FREE_ZERO, C.LOOKUP_QUOTA_FREE, C.LOOKUP_QUOTA_RATE):
            self.assertIn(text, C._LOOKUP_FAILED)
            self.assertTrue(_STATUS_LINE.match(text), text)


if __name__ == "__main__":
    unittest.main()
