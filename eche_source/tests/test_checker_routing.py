# Routing, format selection, missing-model fail-closed, settings audit, GUI errors.
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import classifier as C
from core.secrets import (
    ENV_MAP,
    PUBLIC_KEYS,
    SECRET_KEYS,
    save_all,
)
from gui.widgets.dialogs import match_config_hint, present_failure


def _resp(text, *, status=200, raw=None):
    class _R(io.BytesIO):
        def __init__(self, payload):
            super().__init__(payload)
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = raw if raw is not None else json.dumps({"choices": [{"message": {"content": text}}]}).encode()
    return _R(payload)


def _ollama_resp(text):
    return _resp("", raw=json.dumps({"message": {"role": "assistant", "content": text}}).encode())


def _settings(**values):
    """Old checker settings keys. They must have no effect any more."""
    def lookup(key):
        return values.get(key, "")
    return mock.patch.object(C, "_checker_setting", side_effect=lookup)


def _override(**specs):
    """Test-only checker pin (production has no settings for checkers)."""
    return mock.patch.dict(C._CHECKER_OVERRIDES, specs, clear=True)


def _pulled(*models):
    names = frozenset(m.lower() for m in models)
    return mock.patch.object(C, "_ollama_local_models", return_value=names)


RETIRED = (
    "content_filter_provider",
    "content_filter_model",
    "child_check_provider",
    "child_check_model",
    "image_check_provider",
    "image_check_model",
    "age_vision_ollama_model",
    "blocked_words",
)




class RoutingTests(unittest.TestCase):


    def test_image_check_spec_is_always_the_local_vision_model(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk-real"}, clear=False), _pulled():
            image = C._resolve_checker("image_check")
        self.assertEqual((image.provider, image.model, image.problem), ("ollama", "qwen3-vl:4b-instruct", ""))



    def test_checker_fields_are_not_settings_or_env(self):
        for key in RETIRED:
            self.assertNotIn(key, PUBLIC_KEYS)
            self.assertNotIn(key, ENV_MAP)
        from core.secrets import RETIRED_PUBLIC_KEYS
        self.assertEqual(set(RETIRED) | {"tool_asset"}, set(RETIRED_PUBLIC_KEYS))
        self.assertEqual(set(RETIRED), set(C.RETIRED_CHECKER_KEYS))


    def test_ollama_tag_match(self):
        with _pulled("llama-guard3:8b", "foo:latest"):
            self.assertTrue(C.ollama_has_model("llama-guard3:8b"))
            self.assertTrue(C.ollama_has_model("foo"))
            self.assertFalse(C.ollama_has_model("qwen3-vl:4b-instruct"))
        with mock.patch.object(C, "_ollama_local_models", return_value=None):
            self.assertFalse(C.ollama_has_model("llama-guard3:8b"))




class MissingModelTests(unittest.TestCase):
    def setUp(self):
        self._prev = C.tests_are_allowing()
        C.allow_in_tests(False)
        C.force_backend(None)
        C.clear_cache()
        self.logs = []
        self.events = []

    def tearDown(self):
        C.allow_in_tests(self._prev)
        C.clear_cache()

    def _capture_gui(self):
        def log(message, channel=""):
            self.logs.append((channel, message))
        def emit(event, data=None):
            self.events.append((event, data or {}))
        return mock.patch.multiple("core.gui_bridge", log=log, emit=emit)

    def _http_error(self, code, body, url="https://api.groq.com/openai/v1/chat/completions"):
        import urllib.error
        def fake(req, timeout=None):
            raise urllib.error.HTTPError(url, code, "not found", hdrs={}, fp=io.BytesIO(body.encode()))
        return fake

    def test_detects_status_and_body_shapes(self):
        import urllib.error
        err = urllib.error.HTTPError("https://x", 404, "no", hdrs={}, fp=io.BytesIO(b'{"error":"no"}'))
        self.assertTrue(C.is_missing_model_error(err, status=404))
        self.assertTrue(C.is_missing_model_error(body='{"error":{"code":"model_not_found"}}', status=400))
        self.assertTrue(C.is_missing_model_error(body="model 'foo' not found, try pulling it first", status=404))
        self.assertFalse(C.is_missing_model_error(body="rate limited", status=429))





class SettingsMigrationTests(unittest.TestCase):
    def test_save_all_drops_retired_checker_keys_and_does_not_crash(self):
        tmp = tempfile.mkdtemp(prefix="eche-checker-")
        (Path(tmp) / "config").mkdir()
        path = Path(tmp) / "config" / "settings.json"
        old = {key: "something" for key in RETIRED}
        old["owner_id"] = "123"
        path.write_text(json.dumps(old), encoding="utf-8")
        records = []
        def capture(event, **fields):
            records.append((event, dict(fields)))
        from core.secrets import load_all, public_value
        with mock.patch("core.secrets._user_root", return_value=tmp), \
             mock.patch("core.audit.safe_activity", side_effect=capture):
            loaded = load_all(tmp)
            for key in RETIRED:
                self.assertEqual(public_value(key, tmp), "")
            payload = {k: loaded.get(k, "") for k in list(PUBLIC_KEYS) + list(SECRET_KEYS)}
            save_all(payload, root=tmp)
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in RETIRED:
            self.assertNotIn(key, data)
        self.assertEqual(data.get("owner_id"), "123")
        saves = [fields for event, fields in records if event == "settings-save"]
        self.assertTrue(saves)
        self.assertFalse(any(k.startswith(RETIRED) for k in saves[-1]))


class GuiErrorSurfaceTests(unittest.TestCase):
    def test_config_hints_point_to_the_pull_command(self):
        title, message, hint = match_config_hint(
            "Safety checker model is missing or retired. Backend: ollama. Model: llama-guard3:8b. "
            "Update the content filter model: run `ollama pull llama-guard3:8b` on this PC."
        )
        self.assertIn("safety checker", title.lower())
        self.assertIn("no settings", message)
        self.assertIn("ollama pull", hint)
        title, message, hint = match_config_hint(
            "Age-vision model timed out; the image was blocked. Backend: ollama. Model: qwen3-vl:4b-instruct."
        )
        self.assertIn("age", title.lower())
        self.assertIn("qwen3-vl:4b-instruct", hint)
        self.assertIsNone(match_config_hint("Age-vision model: qwen3-vl:4b-instruct"))

    def test_error_event_is_presented_in_the_gui(self):
        from gui.main import MainWindow
        message = (
            "Safety checker model is missing or retired. Backend: ollama. "
            "Model: llama-guard3:8b. Update the content filter model: run `ollama pull llama-guard3:8b` on this PC."
        )
        class Harness:
            _tb_active = False
            def __init__(self):
                self.logs = []
                self.failures = []
                self._tb_buffer = []
            def append_log(self, text):
                self.logs.append(text)
            def _present_failure(self, text, **kwargs):
                self.failures.append(text)
            def set_loading(self, *a, **k):
                pass
            def set_status(self, *a, **k):
                pass
        h = Harness()
        MainWindow.handle_bot_output(h, json.dumps({"event": "error", "data": {"message": message}}))
        self.assertTrue(h.failures)
        self.assertIn("ollama pull", h.failures[0])
        self.assertTrue(any("ERROR" in line or message in line for line in h.logs))

        h2 = Harness()
        MainWindow.handle_bot_output(h2, json.dumps({"event": "log", "data": {"message": message, "channel": "error"}}))
        self.assertTrue(h2.failures)
        self.assertIn("ollama pull", h2.failures[0])

    def test_present_failure_uses_the_config_hint(self):
        titles = []
        def fake_show_error(parent, title, message="", hint="", **kwargs):
            titles.append((title, message, hint))
        with mock.patch("gui.widgets.dialogs.show_error", side_effect=fake_show_error):
            present_failure(
                None,
                "Safety checker model is missing or retired. Backend: ollama. Model: llama-guard3:8b. "
                "Update the child check model: run `ollama pull llama-guard3:8b` on this PC.",
                log_fn=None,
            )
        self.assertTrue(titles)
        title, message, hint = titles[0]
        self.assertIn("safety checker", title.lower())
        self.assertIn("ollama pull", hint)
