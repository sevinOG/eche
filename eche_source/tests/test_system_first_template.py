"""Live 2026-11-18 23:37-23:39 ET: Ollama 500 "System message must be at the
beginning" from eriktonon/qwen3.8-uncensored:9b's chat template. Mock-only."""

import unittest
from unittest import mock

from core import client as C
from core.safety import SAFETY_FOOTER

JINJA_500 = (
    '{"error":"\\n------------\\nWhile executing CallExpression at line 85, column 32 in source:\\n'
    "...first %}            {{- raise_exception('System message must be at the beginnin...\\n"
    '                                           ^\\nError: Jinja Exception"}'
)
OK = {"model": "m", "message": {"role": "assistant", "content": "hi"}, "done": True, "done_reason": "stop"}


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text or ""
        self.headers = {}

    def json(self):
        return self._body


def _payload():
    return {
        "model": "eriktonon/qwen3.8-uncensored:9b",
        "max_tokens": 64,
        "messages": [
            {"role": "system", "content": SAFETY_FOOTER},
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "yo"},
            {"role": "system", "content": SAFETY_FOOTER},
        ],
    }


class SystemFirstTests(unittest.TestCase):
    def setUp(self):
        C._SYSTEM_FIRST_MODELS.clear()
        self.addCleanup(C._SYSTEM_FIRST_MODELS.clear)

    def test_transform_keeps_footer_last(self):
        out = C._system_first_messages(_payload()["messages"])
        self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "user"])
        self.assertTrue(out[0]["content"].startswith(SAFETY_FOOTER))
        self.assertIn("persona", out[0]["content"])
        self.assertEqual(out[-1]["content"], C.SYSTEM_NOTE_PREFIX + SAFETY_FOOTER)
        self.assertEqual(sum(m["role"] == "system" for m in out), 1)

    def test_detects_truncated_jinja_error(self):
        self.assertTrue(C._system_first_required(JINJA_500))
        self.assertFalse(C._system_first_required("model not found"))

    def test_retry_after_500_then_remembered(self):
        sent = []

        def fake_post(url, json=None, headers=None, timeout=None):
            sent.append(json)
            roles = [m["role"] for m in json["messages"]]
            if roles.count("system") > 1 or roles[-1] == "system":
                return _Resp(500, text=JINJA_500)
            return _Resp(200, OK, text="ok")

        with mock.patch.object(C, "_provider_backend", return_value="ollama"), \
             mock.patch.object(C, "_ollama_think_value", return_value=None), \
             mock.patch.object(C, "_audit_http"), \
             mock.patch.object(C.requests, "post", side_effect=fake_post):
            url = C._api_url()
            data = C._sync_post(url, _payload(), {})
            self.assertNotIn("error", data)
            self.assertEqual(len(sent), 2)
            last = sent[-1]["messages"]
            self.assertEqual(last[-1]["content"], C.SYSTEM_NOTE_PREFIX + SAFETY_FOOTER)
            # Remembered: the next call goes straight through.
            sent.clear()
            data = C._sync_post(url, _payload(), {})
            self.assertNotIn("error", data)
            self.assertEqual(len(sent), 1)

    def test_other_models_unchanged(self):
        with mock.patch.object(C, "_provider_backend", return_value="ollama"), \
             mock.patch.object(C, "_ollama_think_value", return_value=None):
            _url, body = C._ollama_request(C._api_url(), _payload())
        self.assertEqual([m["role"] for m in body["messages"]][-1], "system")


if __name__ == "__main__":
    unittest.main()
