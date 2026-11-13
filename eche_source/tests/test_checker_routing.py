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
    def lookup(key):
        return values.get(key, "")
    return mock.patch.object(C, "_checker_setting", side_effect=lookup)


class FormatFromNameTests(unittest.TestCase):
    def test_llama_guard_and_prompt_guard_and_yes_no(self):
        self.assertEqual(C.format_from_model_name("llama-guard3:8b"), "llama_guard")
        self.assertEqual(C.format_from_model_name("meta-llama/llama-guard-3-8b"), "llama_guard")
        self.assertEqual(C.format_from_model_name("meta-llama/llama-guard-4-12b"), "llama_guard")
        self.assertEqual(C.format_from_model_name("llama-guard-4-12b"), "llama_guard")
        self.assertEqual(C.format_from_model_name("meta-llama/Llama-Prompt-Guard-2-86M"), "prompt_guard")
        self.assertEqual(C.format_from_model_name("llama-prompt-guard-2"), "prompt_guard")
        self.assertEqual(C.format_from_model_name("qwen/qwen3.8-27b"), "yes_no")
        self.assertEqual(C.format_from_model_name("llama-3.1-8b-instant"), "yes_no")
        self.assertEqual(C.format_from_model_name("openrouter/free"), "yes_no")


class RoutingTests(unittest.TestCase):
    def test_blank_settings_use_the_chat_provider_and_model(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "openrouter", "GROQ_MODEL": "openrouter/free", "GROQ_API_KEY": "", "OPENROUTER_API_KEY": "sk-or-real"}, clear=False), \
             _settings():
            spec = C._resolve_checker("content_filter")
            self.assertEqual(spec.provider, "openrouter")
            self.assertEqual(spec.model, "openrouter/free")
            self.assertEqual(spec.fmt, "yes_no")
            self.assertEqual(spec.field, "chat model")
            self.assertEqual(C.classifier_model(), "openrouter/free")
            self.assertEqual(C.child_check_model(), "openrouter/free")
            key, url = C._endpoint()
            self.assertEqual(key, "sk-or-real")
            self.assertIn("openrouter", url)

    def test_settings_override_each_checker_independently(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "cloud", "GROQ_MODEL": "qwen/qwen3.8-27b", "GROQ_API_KEY": "gsk-real", "OPENROUTER_API_KEY": "sk-or-real"}, clear=False), \
             _settings(
                 content_filter_provider="openrouter",
                 content_filter_model="meta-llama/llama-guard-4-12b",
                 child_check_provider="ollama",
                 child_check_model="llama-guard3:8b",
             ):
            filt = C._resolve_checker("content_filter")
            child = C._resolve_checker("child_check")
            self.assertEqual(filt.provider, "openrouter")
            self.assertEqual(filt.model, "meta-llama/llama-guard-4-12b")
            self.assertEqual(filt.fmt, "llama_guard")
            self.assertEqual(filt.field, "content_filter_model")
            self.assertEqual(child.provider, "ollama")
            self.assertEqual(child.model, "llama-guard3:8b")
            self.assertEqual(child.fmt, "llama_guard")
            self.assertEqual(child.field, "child_check_model")
            self.assertEqual(C._endpoint()[1], C.OPENROUTER_URL)
            self.assertEqual(C._child_endpoint()[1], C.OLLAMA_CHAT_URL)

    def test_provider_without_a_model_fails_closed(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "cloud", "GROQ_MODEL": "qwen/qwen3.8-27b"}, clear=False), \
             _settings(content_filter_provider="ollama", content_filter_model=""):
            spec = C._resolve_checker("content_filter")
            self.assertEqual(spec.problem, "blank_model")
            self.assertEqual(spec.field, "content_filter_model")

    def test_prompt_guard_fails_closed(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "cloud", "GROQ_MODEL": "meta-llama/Llama-Prompt-Guard-2-86M"}, clear=False), \
             _settings():
            spec = C._resolve_checker("child_check")
            self.assertEqual(spec.problem, "prompt_guard")
            self.assertEqual(spec.field, "chat model")

    def test_env_map_does_not_include_checker_fields(self):
        for key in (
            "content_filter_provider",
            "content_filter_model",
            "child_check_provider",
            "child_check_model",
            "image_check_provider",
            "image_check_model",
        ):
            self.assertIn(key, PUBLIC_KEYS)
            self.assertNotIn(key, ENV_MAP)

    def test_env_model_vars_are_ignored(self):
        with mock.patch.dict(os.environ, {
            "ECHE_PROVIDER": "cloud",
            "GROQ_MODEL": "qwen/qwen3.8-27b",
            "CHILD_CHECK_MODEL": "meta-llama/llama-guard-4-12b",
            "CLASSIFIER_MODEL": "other-model",
        }, clear=False), _settings():
            self.assertEqual(C.child_check_model(), "qwen/qwen3.8-27b")
            self.assertEqual(C.classifier_model(), "qwen/qwen3.8-27b")


class PostedRequestTests(unittest.TestCase):
    def setUp(self):
        self._prev = C.tests_are_allowing()
        C.allow_in_tests(False)
        C.force_backend(None)
        C.clear_cache()
        self.captured = []

    def tearDown(self):
        C.allow_in_tests(self._prev)
        C.force_backend(None)
        C.clear_cache()

    def _open(self, reply="safe"):
        def fake(req, timeout=None):
            body = json.loads(req.data.decode()) if req.data else {}
            self.captured.append((req.full_url, body, timeout))
            if "11434" in req.full_url and req.full_url.endswith("/api/generate"):
                return _resp("", raw=json.dumps({"response": reply}).encode())
            if "11434" in req.full_url:
                return _ollama_resp(reply)
            return _resp(reply)
        return fake

    def test_groq_yes_no_request(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "cloud", "GROQ_MODEL": "qwen/qwen3.8-27b", "GROQ_API_KEY": "gsk-real"}, clear=False), \
             _settings(), \
             mock.patch("urllib.request.urlopen", side_effect=self._open("NO")), \
             mock.patch.object(C, "_child_check_sync", return_value=""):
            self.assertEqual(C.classify("hello there"), "")
        self.assertTrue(self.captured)
        url, body, _ = self.captured[0]
        self.assertEqual(url, C.GROQ_URL)
        self.assertEqual(body["model"], "qwen/qwen3.8-27b")
        self.assertEqual(body["temperature"], 0)
        blob = json.dumps(body)
        self.assertIn("Answer only YES or NO", blob)
        self.assertNotIn("BEGIN UNSAFE CONTENT CATEGORIES", blob)

    def test_openrouter_llama_guard_request(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "openrouter", "GROQ_MODEL": "meta-llama/llama-guard-4-12b", "OPENROUTER_API_KEY": "sk-or-real", "GROQ_API_KEY": ""}, clear=False), \
             _settings(), \
             mock.patch("urllib.request.urlopen", side_effect=self._open("safe")), \
             mock.patch.object(C, "_child_check_sync", return_value=""):
            self.assertEqual(C.classify("hello there"), "")
        url, body, _ = self.captured[0]
        self.assertIn("openrouter", url)
        self.assertEqual(body["model"], "meta-llama/llama-guard-4-12b")
        roles = [m["role"] for m in body["messages"]]
        self.assertNotIn("system", roles)
        self.assertEqual(roles[0], "user")

    def test_ollama_llama_guard_child_uses_raw_custom_category(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "ollama", "GROQ_MODEL": "llama-guard3:8b", "GROQ_API_KEY": "", "OPENROUTER_API_KEY": ""}, clear=False), \
             _settings(), \
             mock.patch("urllib.request.urlopen", side_effect=self._open("unsafe\nS1")):
            self.assertEqual(C._child_check_sync("a child in a garden"), "child-check-unsafe")
        url, body, _ = self.captured[0]
        self.assertEqual(url, C.OLLAMA_GENERATE_URL)
        self.assertTrue(body.get("raw"))
        self.assertIn("Minors or child-like characters", body["prompt"])
        self.assertFalse(body.get("think"))

    def test_think_blocks_are_stripped_before_yes_no_parse(self):
        raw = "<think>the text looks unsafe</think>\nNO"
        self.assertEqual(C._verdict_to_check(raw, "yes_no"), "")
        self.assertEqual(C._verdict_to_check("<think>ok</think>\nYES", "yes_no"), "classifier-unsafe")
        self.assertEqual(C._verdict_to_check("<think>x</think>\nsafe", "llama_guard"), "")

    def test_prompt_guard_classify_fails_closed_and_does_not_post(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "cloud", "GROQ_MODEL": "llama-prompt-guard-2", "GROQ_API_KEY": "gsk-real"}, clear=False), \
             _settings(), \
             mock.patch("urllib.request.urlopen", side_effect=self._open("safe")):
            self.assertEqual(C.classify("hello"), "classifier-error")
        self.assertFalse(self.captured)


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

    def test_groq_model_not_found_fails_closed_and_names_the_field(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "cloud", "GROQ_MODEL": "retired-model", "GROQ_API_KEY": "gsk-real"}, clear=False), \
             _settings(), \
             self._capture_gui(), \
             mock.patch("urllib.request.urlopen", side_effect=self._http_error(400, '{"error":{"code":"model_not_found","message":"The model does not exist"}}')):
            self.assertEqual(C.classify("hello"), "classifier-error")
        joined = " ".join(m for _c, m in self.logs) + " " + " ".join(str(e) for e in self.events)
        self.assertIn("cloud", joined)
        self.assertIn("retired-model", joined)
        self.assertIn("chat model", joined)
        self.assertTrue(any(ev == "error" for ev, _ in self.events))

    def test_openrouter_404_names_content_filter_model(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "cloud", "GROQ_MODEL": "qwen/qwen3.8-27b", "OPENROUTER_API_KEY": "sk-or-real", "GROQ_API_KEY": "gsk-real"}, clear=False), \
             _settings(content_filter_provider="openrouter", content_filter_model="old-or-model"), \
             self._capture_gui(), \
             mock.patch("urllib.request.urlopen", side_effect=self._http_error(404, '{"error":{"message":"model not found"}}', url=C.OPENROUTER_URL)):
            self.assertEqual(C.classify("hello"), "classifier-error")
        joined = " ".join(m for _c, m in self.logs)
        self.assertIn("openrouter", joined)
        self.assertIn("old-or-model", joined)
        self.assertIn("content_filter_model", joined)
        self.assertNotIn("falling back", joined.lower())

    def test_ollama_unpulled_model_names_child_check_model(self):
        import urllib.error
        def fake(req, timeout=None):
            raise urllib.error.HTTPError(
                C.OLLAMA_GENERATE_URL,
                404,
                "not found",
                hdrs={},
                fp=io.BytesIO(b'{"error":"model \'missing-guard:8b\' not found, try pulling it first"}'),
            )
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "ollama", "GROQ_MODEL": "llama3", "GROQ_API_KEY": "", "OPENROUTER_API_KEY": ""}, clear=False), \
             _settings(child_check_provider="ollama", child_check_model="missing-guard:8b"), \
             self._capture_gui(), \
             mock.patch("urllib.request.urlopen", side_effect=fake):
            self.assertEqual(C._child_check_sync("hello"), "child-check-error")
        joined = " ".join(m for _c, m in self.logs)
        self.assertIn("ollama", joined)
        self.assertIn("missing-guard:8b", joined)
        self.assertIn("child_check_model", joined)
        self.assertTrue(any(ev == "error" for ev, _ in self.events))


class SettingsAuditTests(unittest.TestCase):
    def test_save_all_audits_old_and_new_checker_fields(self):
        from core import audit as audit_mod
        tmp = tempfile.mkdtemp(prefix="eche-checker-")
        records = []
        def capture(event, **fields):
            records.append((event, dict(fields)))
        payload = {k: "" for k in list(PUBLIC_KEYS) + list(SECRET_KEYS)}
        payload["content_filter_model"] = ""
        with mock.patch("core.secrets._user_root", return_value=tmp), \
             mock.patch("core.audit.safe_activity", side_effect=capture):
            save_all(payload, root=tmp)
            payload["content_filter_model"] = "llama-guard3:8b"
            payload["content_filter_provider"] = "ollama"
            save_all(payload, root=tmp)
        saves = [fields for event, fields in records if event == "settings-save"]
        self.assertGreaterEqual(len(saves), 2)
        changed = saves[-1]
        self.assertEqual(changed.get("content_filter_model_old"), "")
        self.assertEqual(changed.get("content_filter_model_new"), "llama-guard3:8b")
        self.assertEqual(changed.get("content_filter_provider_old"), "")
        self.assertEqual(changed.get("content_filter_provider_new"), "ollama")
        self.assertEqual(changed.get("outcome"), "success")
        self.assertNotIn("content_filter_model", ENV_MAP)


class GuiErrorSurfaceTests(unittest.TestCase):
    def test_config_hints_name_each_field(self):
        title, message, hint = match_config_hint(
            "Safety checker model is missing or retired. Backend: cloud. Model: gone. Update content_filter_model in Settings."
        )
        self.assertIn("content filter", title.lower())
        self.assertIn("content_filter_model", message)
        title, message, hint = match_config_hint(
            "Safety checker model is missing or retired. Backend: ollama. Model: x. Update child_check_model in Settings."
        )
        self.assertIn("child check", title.lower())
        self.assertIn("child_check_model", message)
        title, message, hint = match_config_hint(
            "Safety checker model is missing or retired. Backend: cloud. Model: gone. Update the chat model in Settings."
        )
        self.assertTrue(title)
        self.assertIn("chat model", (title + message + hint).lower())

    def test_error_event_is_presented_in_the_gui(self):
        from gui.main import MainWindow
        message = (
            "Safety checker model is missing or retired. Backend: groq. "
            "Model: retired-model. Update content_filter_model in Settings."
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
        self.assertIn("content_filter_model", h.failures[0])
        self.assertTrue(any("ERROR" in line or message in line for line in h.logs))

        h2 = Harness()
        MainWindow.handle_bot_output(h2, json.dumps({"event": "log", "data": {"message": message, "channel": "error"}}))
        self.assertTrue(h2.failures)
        self.assertIn("content_filter_model", h2.failures[0])

    def test_present_failure_uses_the_config_hint(self):
        titles = []
        def fake_show_error(parent, title, message="", hint="", **kwargs):
            titles.append((title, message, hint))
        with mock.patch("gui.widgets.dialogs.show_error", side_effect=fake_show_error):
            present_failure(
                None,
                "Safety checker model is missing or retired. Backend: ollama. Model: x. Update child_check_model in Settings.",
                log_fn=None,
            )
        self.assertTrue(titles)
        title, message, hint = titles[0]
        self.assertIn("child check", title.lower())
        self.assertIn("child_check_model", message)
