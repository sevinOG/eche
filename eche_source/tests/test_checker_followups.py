# H1, M1-M3, L1-L6 follow-ups for settings-routed checkers.
from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import classifier as C
from core.secrets import ENV_MAP, PUBLIC_KEYS


def _resp(text, *, status=200, raw=None):
    class _R(io.BytesIO):
        def __init__(self, payload):
            super().__init__(payload)
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = raw if raw is not None else json.dumps(
        {"choices": [{"message": {"content": text}}]}
    ).encode()
    return _R(payload)


def _ollama_generate(text):
    return _resp("", raw=json.dumps({"response": text}).encode())


def _settings(**values):
    def lookup(key):
        return values.get(key, "")
    return mock.patch.object(C, "_checker_setting", side_effect=lookup)


def _override(**specs):
    """Test-only checker pin (production has no checker settings)."""
    return mock.patch.dict(C._CHECKER_OVERRIDES, specs, clear=True)




class GuardPromptAndParseTests(unittest.TestCase):
    def setUp(self):
        self._prev = C.tests_are_allowing()
        C.allow_in_tests(False)
        C.force_backend(None)
        C.clear_cache()
        self.captured = []

    def tearDown(self):
        C.allow_in_tests(self._prev)
        C.clear_cache()

    def _open(self, reply="safe"):
        def fake(req, timeout=0):
            body = json.loads(req.data.decode()) if req.data else {}
            self.captured.append((req.full_url, body, dict(req.headers)))
            if "11434" in req.full_url and "/api/generate" in req.full_url:
                return _ollama_generate(reply)
            if "11434" in req.full_url:
                return _resp("", raw=json.dumps({"message": {"content": reply}}).encode())
            return _resp(reply)
        return fake





    def test_yes_no_parse_rejects_extra_lines(self):
        self.assertIs(C._parse_child_verdict("NO"), False)
        self.assertIs(C._parse_child_verdict("YES"), True)
        self.assertIsNone(C._parse_child_verdict("NO\nextra"))
        self.assertIsNone(C._parse_child_verdict("NO\nYES"))
        self.assertIsNone(C._parse_child_verdict("YES\nS4"))



class NameMatchAndSettingsTests(unittest.TestCase):

    def test_unreadable_settings_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="eche-settings-") as raw:
            path = Path(raw) / "config"
            path.mkdir()
            (path / "settings.json").write_text("{not json", encoding="utf-8")
            with mock.patch("core.secrets._user_root", return_value=raw):
                self.assertTrue(C.settings_file_problem())
                spec = C._resolve_checker("image_check")
                self.assertEqual(spec.problem, "settings_unreadable")
                C.allow_in_tests(False)
                try:
                    with mock.patch("core.audit.api"):
                        self.assertEqual(C._child_check_sync("hello"), "child-check-error")
                finally:
                    C.allow_in_tests(True)

    def test_image_check_has_no_settings_and_uses_the_local_vision_backup(self):
        for key in ("image_check_provider", "image_check_model", "age_vision_ollama_model"):
            self.assertNotIn(key, PUBLIC_KEYS)
            self.assertNotIn(key, ENV_MAP)
        with _settings(image_check_provider="cloud", image_check_model="llama-guard3:8b"):
            spec = C._resolve_checker("image_check")
        self.assertEqual((spec.provider, spec.model, spec.problem), ("ollama", "qwen3-vl:4b-instruct", ""))
        self.assertTrue(C.model_is_vision_capable(spec.model))
        with _override(image_check=("ollama", "llama-guard3:8b")):
            self.assertEqual(C._resolve_checker("image_check").problem, "not_vision")

    def test_image_classify_fails_closed_when_grok_and_local_both_fail(self):
        prev = C.tests_are_allowing()
        C.allow_in_tests(False)
        C.clear_cache()
        try:
            with mock.patch.object(C, "_xai_key", return_value="xai-test"), \
                 mock.patch.object(C, "_xai_vision_model", return_value="grok-vision-test"), \
                 mock.patch("urllib.request.urlopen", side_effect=OSError("down")), \
                 mock.patch.object(C, "_child_check_sync", return_value=""):
                out = asyncio.run(C.classify_async("hi", images=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]))
            self.assertTrue(out)
        finally:
            C.allow_in_tests(prev)
            C.clear_cache()

    def test_blank_changes_are_logged_on_both_sides(self):
        from core.audit import record
        with tempfile.TemporaryDirectory(prefix="eche-audit-") as raw:
            os.environ["ECHE_AUDIT_PATH"] = str(Path(raw) / "audit.log")
            rec = record("settings-save", content_filter_model_old="", content_filter_model_new="llama-guard3:8b")
            self.assertEqual(rec.get("content_filter_model_old"), "")
            self.assertEqual(rec.get("content_filter_model_new"), "llama-guard3:8b")
            rec = record("settings-save", content_filter_model_old="llama-guard3:8b", content_filter_model_new="")
            self.assertEqual(rec.get("content_filter_model_new"), "")
            del os.environ["ECHE_AUDIT_PATH"]

    def test_checker_settings_are_not_watched_any_more(self):
        self.assertEqual(C._CHECKER_WATCH_KEYS, ())

    def test_no_backend_and_cache_logs_name_backend_and_model(self):
        prev = C.tests_are_allowing()
        C.allow_in_tests(False)
        C.clear_cache()
        logs = []

        def fake_api(*_a, **fields):
            logs.append(fields)

        spec = C.CheckerSpec("image_check", provider="cloud", model="qwen/qwen3.8-27b", fmt="yes_no", field="image_check_model")
        with mock.patch("core.audit.api", side_effect=fake_api), \
             mock.patch.object(C, "_emit_checker_error"):
            C._log_no_backend(spec)
        C.allow_in_tests(prev)
        kinds = [item.get("outcome") for item in logs]
        self.assertIn("error", kinds)
        for item in logs:
            self.assertEqual(item.get("backend"), "cloud")
            self.assertEqual(item.get("model"), "qwen/qwen3.8-27b")

    def test_skip_dirs_include_dist_and_build(self):
        from tests.test_no_media_gen import _SKIP_DIRS
        self.assertIn("dist", _SKIP_DIRS)
        self.assertIn("build", _SKIP_DIRS)


if __name__ == "__main__":
    unittest.main()


