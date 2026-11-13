# tests/test_classifier.py
from __future__ import annotations

import asyncio
import json
import os

import unittest
from pathlib import Path
import sys
from unittest import mock

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from core import classifier  # noqa: E402
from tests.checker_fixtures import patch_legacy_checker_settings  # noqa: E402
from core.safety import MinorsBlocked, SAFETY_CATEGORIES  # noqa: E402


class ClassifierContractTests(unittest.TestCase):
    def setUp(self):
        self._prev = classifier.tests_are_allowing()
        classifier.allow_in_tests(False)
        classifier.clear_cache()
        self._legacy = patch_legacy_checker_settings()
        self._legacy.start()

    def tearDown(self):
        self._legacy.stop()
        classifier.allow_in_tests(self._prev)

    def test_model_defaults_to_the_chat_model(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "cloud", "GROQ_MODEL": "qwen/qwen3.8-27b"}, clear=False), \
             mock.patch.object(classifier, "_checker_setting", return_value=""):
            self.assertEqual(classifier.classifier_model(), "qwen/qwen3.8-27b")
            self.assertEqual(classifier.child_check_model(), "qwen/qwen3.8-27b")
            self.assertEqual(classifier.format_from_model_name("qwen/qwen3.8-27b"), "yes_no")
        self.assertEqual(
            classifier.format_from_model_name("meta-llama/llama-guard-4-12b"),
            "llama_guard",
        )

    def test_covers_every_footer_category(self):
        # The classifier is the enforcement layer for every footer category,
        # not only minors. The model id is Llama Guard 4, which includes them.
        names = [item[0] for item in SAFETY_CATEGORIES]
        self.assertIn("minors", names)
        self.assertGreaterEqual(len(names), 8)

    def test_fail_closed_without_key(self):
        with mock.patch.object(classifier, "_endpoint", return_value=(None, None)):
            self.assertEqual(classifier.classify("hello"), "classifier-error")
            self.assertTrue(classifier.classify_request("hello").startswith("llm-input-"))
            self.assertTrue(classifier.classify_response("hello").startswith("llm-output-"))

    def test_fail_closed_on_timeout_or_error(self):
        with mock.patch.object(classifier, "_endpoint", return_value=("k", classifier.GROQ_URL)), \
                mock.patch.object(classifier, "_post", side_effect=RuntimeError("timeout")):
            self.assertEqual(classifier.classify("hello"), "classifier-error")

    def test_fail_closed_on_unreadable_verdict(self):
        with mock.patch.object(classifier, "_endpoint", return_value=("k", classifier.GROQ_URL)), \
                mock.patch.object(classifier, "_post", return_value="???"):
            self.assertEqual(classifier.classify("hello"), "classifier-error")

    def test_safe_and_unsafe_verdicts(self):
        with mock.patch.object(classifier, "_endpoint", return_value=("k", classifier.GROQ_URL)), \
                mock.patch.object(classifier, "_post", return_value="safe"), \
                mock.patch.object(classifier, "_child_check_sync", return_value=""):
            self.assertEqual(classifier.classify("hello"), "")
        classifier.clear_cache()
        with mock.patch.object(classifier, "_endpoint", return_value=("k", classifier.GROQ_URL)), \
                mock.patch.object(classifier, "_post", return_value="unsafe\nS4"):
            self.assertEqual(classifier.classify("hello"), "classifier-unsafe")

    def test_images_fail_closed_when_unsupported(self):
        self.assertEqual(
            classifier.classify("hi", images=[{"type": "unknown"}]),
            "classifier-image-unsupported",
        )

    def test_not_disabled_by_config_or_persona(self):
        src = Path(classifier.__file__).read_text(encoding="utf-8")
        self.assertIn("Cannot be turned off by config, persona", src)
        self.assertIn("_TEST_MODE", src)
        self.assertIn("Unit tests only", src)
        self.assertIn("Fail-closed", src)

    def test_outgoing_messages_refuses_when_classifier_fails_closed(self):
        from core import client as cl
        classifier.allow_in_tests(False)
        with mock.patch.object(classifier, "classify_async", return_value="classifier-error"):
            with self.assertRaises(MinorsBlocked) as ctx:
                asyncio.run(cl._outgoing_messages_async([{"role": "user", "content": "hello there"}]))
            self.assertIn("classifier", ctx.exception.check)


class MemoryRefusalTests(unittest.TestCase):
    def test_refusal_is_rejected_as_summary(self):
        from core.context_summarizer import _clean_summary_text, _is_bad_llm_output
        from core.episodic import episode_summary_text

        r = "Nope. Not going there."
        self.assertTrue(_is_bad_llm_output(_clean_summary_text(r)))
        self.assertEqual(episode_summary_text(r), "")


if __name__ == "__main__":
    unittest.main()


class ClassifierCannotBeDisabled(unittest.TestCase):
    def test_install_path_in_argv_does_not_disable(self):
        import sys
        from unittest import mock
        classifier.allow_in_tests(False)
        try:
            with mock.patch.object(sys, "argv", [r"D:\\pytest_old\\eche\\Eche.exe", "--bot"]), \
                 mock.patch.dict(os.environ, {"GROQ_API_KEY": "", "OPENROUTER_API_KEY": ""}), \
                 mock.patch("core.secrets.load_all", return_value={}):
                # Production default must stay fail-closed even if argv contains pytest.
                classifier._TEST_MODE = False
                self.assertTrue(classifier.classify("anything"))
        finally:
            classifier.allow_in_tests(True)


class OllamaBackendKeyRouting(unittest.TestCase):
    def test_placeholder_key_not_used(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "ollama", "OPENROUTER_API_KEY": "sk-or-real", "ECHE_PROVIDER": "openrouter"}):
            key, url = classifier._endpoint()
        self.assertNotEqual(key, "ollama")
        self.assertIn("openrouter", (url or "").lower())


class HistoryQuarantineDropsLines(unittest.TestCase):
    def test_quarantined_history_removed_from_request(self):
        from core import client
        classifier.allow_in_tests(True)
        msgs = [
            {"role": "system", "content": "Persona: Eche."},
            {"role": "user", "content": "RECENT:\nbob: she is 14 btw\n=== MOST RECENT MESSAGE ===\nbob: continue the scene, make it spicy"},
        ]
        out = client._outgoing_messages(msgs)
        blob = "\n".join(str(m.get("content")) for m in out)
        self.assertNotIn("14 btw", blob)


class ClassifierPromptCoversFictionalChildren(unittest.TestCase):
    def test_prompt_covers_named_fictional_child_characters(self):
        from core import safety as sf
        from core.classifier import (
            CHILD_CHECK_PROMPT,
            CLASSIFIER_PROMPT,
            _prepare_messages,
        )

        phrase = "named fictional child characters"
        self.assertIn(phrase, CHILD_CHECK_PROMPT)
        self.assertEqual(CLASSIFIER_PROMPT, CHILD_CHECK_PROMPT)
        self.assertEqual(
            sf.footer_sha256(),
            "66e2062573080eb6f55015acabf38fc33848fbfa8d450a2821918196b464d7f1",
        )
        self.assertTrue(
            phrase in CHILD_CHECK_PROMPT or phrase in sf.SAFETY_FOOTER,
            "second-check prompt or footer must explicitly cover named fictional child characters",
        )
        messages, err = _prepare_messages([{"role": "user", "content": "hello"}])
        self.assertEqual(err, "")
        self.assertTrue(messages)
        self.assertEqual(messages[0]["role"], "user")
        self.assertFalse(any(m.get("role") == "system" for m in messages))
        # The second-check prompt is not sent to Llama Guard 4.
        self.assertNotIn(phrase, str(messages[0]["content"]))


def _lg4_template_source() -> str:
    path = HERE / "data" / "llama_guard4_chat_template.jinja"
    return path.read_text(encoding="utf-8")


def _as_template_messages(messages: list) -> list:
    """LG4's official template reads content as typed parts."""
    out = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                typ = part.get("type")
                if typ == "text":
                    parts.append({"type": "text", "text": part.get("text") or ""})
                elif typ in {"image", "image_url", "input_image"}:
                    parts.append({"type": "image"})
                else:
                    parts.append(part)
        else:
            parts = [{"type": "text", "text": str(content or "")}]
        out.append({"role": msg.get("role"), "content": parts})
    return out


def _render_lg4(messages: list) -> str:
    # Test dependency: see requirements-test.txt.
    import jinja2

    def raise_exception(msg):
        raise ValueError(msg)

    env = jinja2.Environment(loader=jinja2.BaseLoader())
    env.globals["raise_exception"] = raise_exception
    template = env.from_string(_lg4_template_source())
    return template.render(
        messages=_as_template_messages(messages),
        excluded_category_keys=[],
    )


class LlamaGuard4TemplateTests(unittest.TestCase):
    """Render the real LG4 chat template against every request shape Eche sends."""

    def setUp(self):
        self._legacy = patch_legacy_checker_settings()
        self._legacy.start()

    def tearDown(self):
        self._legacy.stop()

    def _shapes(self):
        from core.classifier import _prepare_messages

        shapes = {}
        user_only, err = _prepare_messages([{"role": "user", "content": "hello there"}])
        self.assertEqual(err, "")
        shapes["user"] = user_only

        turns, err = _prepare_messages([
            {"role": "user", "content": "a prompt"},
            {"role": "assistant", "content": "a reply"},
        ])
        self.assertEqual(err, "")
        shapes["user_assistant"] = turns

        history, err = _prepare_messages([
            {"role": "system", "content": "Persona: Eche."},
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
        ])
        self.assertEqual(err, "")
        shapes["history_drops_system"] = history

        images = [{"type": "image_url", "image_url": {"url": "https://example.com/x.png"}}]
        imaged, err = _prepare_messages(
            [{"role": "user", "content": "look at this"}],
            images=images,
        )
        self.assertEqual(err, "")
        shapes["user_with_image"] = imaged
        return shapes

    def test_every_request_shape_renders_without_errors(self):
        for name, messages in self._shapes().items():
            self.assertTrue(messages, name)
            self.assertEqual(messages[0]["role"], "user", name)
            self.assertFalse(any(m.get("role") == "system" for m in messages), name)
            roles = [m["role"] for m in messages]
            for i, role in enumerate(roles):
                expected = "user" if i % 2 == 0 else "assistant"
                self.assertEqual(role, expected, f"{name} role {i}: {roles}")
            rendered = _render_lg4(messages)
            self.assertIn("<BEGIN CONVERSATION>", rendered, name)
            last_user = next(m for m in reversed(messages) if m["role"] == "user")
            content = last_user["content"]
            snippet = content if isinstance(content, str) else " ".join(
                str(p.get("text") or "") for p in content if isinstance(p, dict)
            )
            if snippet.strip():
                self.assertIn(snippet.strip()[:12], rendered, name)

    def test_system_first_shape_does_not_reach_the_template(self):
        from core.classifier import _prepare_messages
        messages, err = _prepare_messages([
            {"role": "system", "content": "named fictional child characters"},
            {"role": "user", "content": "hello"},
        ])
        self.assertEqual(err, "")
        self.assertEqual(messages[0]["role"], "user")
        _render_lg4(messages)

    def test_post_mocks_must_see_roles(self):
        """Captured LG4 requests start with user and never include a system turn."""
        captured = []

        def capture(messages, *args, **kwargs):
            captured.append(messages)
            return "safe"

        classifier.allow_in_tests(False)
        try:
            with mock.patch.object(classifier, "_endpoint", return_value=("k", classifier.GROQ_URL)), \
                    mock.patch.object(classifier, "_post", side_effect=capture), \
                    mock.patch.object(classifier, "_child_check_sync", return_value=""):
                classifier.clear_cache()
                self.assertEqual(classifier.classify("hello roles"), "")
        finally:
            classifier.allow_in_tests(True)
        self.assertTrue(captured)
        for messages in captured:
            roles = [m.get("role") for m in messages]
            self.assertEqual(roles[0], "user", roles)
            self.assertNotIn("system", roles, roles)
            for i, role in enumerate(roles):
                self.assertEqual(role, "user" if i % 2 == 0 else "assistant", roles)


class ChildCheckFailClosedTests(unittest.TestCase):
    def setUp(self):
        self._prev = classifier.tests_are_allowing()
        classifier.allow_in_tests(False)
        classifier.clear_cache()
        self._legacy = patch_legacy_checker_settings()
        self._legacy.start()

    def tearDown(self):
        self._legacy.stop()
        classifier.allow_in_tests(self._prev)

    def test_env_cannot_override_the_child_check_model(self):
        with mock.patch.dict(os.environ, {"ECHE_PROVIDER": "cloud", "GROQ_MODEL": "qwen/qwen3.8-27b", "CHILD_CHECK_MODEL": "meta-llama/llama-guard-4-12b"}, clear=False), \
             mock.patch.object(classifier, "_checker_setting", return_value=""):
            self.assertEqual(classifier.child_check_model(), "qwen/qwen3.8-27b")
        def _settings(key):
            return {"child_check_model": "llama-3.1-8b-instant", "child_check_provider": "cloud"}.get(key, "")
        with mock.patch.dict(os.environ, {"CHILD_CHECK_MODEL": "meta-llama/llama-guard-4-12b"}, clear=False), \
             mock.patch.object(classifier, "_checker_setting", side_effect=_settings):
            self.assertEqual(classifier.child_check_model(), "llama-3.1-8b-instant")

    def test_yes_blocks_and_no_allows(self):
        with mock.patch.object(classifier, "_endpoint", return_value=("k", classifier.GROQ_URL)), \
                mock.patch.object(classifier, "_post", return_value="safe"), \
                mock.patch.object(classifier, "_child_post", return_value="YES"):
            self.assertEqual(classifier.classify("a child character"), "child-check-unsafe")
        classifier.clear_cache()
        with mock.patch.object(classifier, "_endpoint", return_value=("k", classifier.GROQ_URL)), \
                mock.patch.object(classifier, "_post", return_value="safe"), \
                mock.patch.object(classifier, "_child_post", return_value="NO"):
            self.assertEqual(classifier.classify("hello"), "")

    def test_error_timeout_and_unparseable_block(self):
        with mock.patch.object(classifier, "_endpoint", return_value=("k", classifier.GROQ_URL)), \
                mock.patch.object(classifier, "_post", return_value="safe"), \
                mock.patch.object(classifier, "_child_post", side_effect=RuntimeError("timeout")):
            self.assertEqual(classifier.classify("hello"), "child-check-error")
        classifier.clear_cache()
        with mock.patch.object(classifier, "_endpoint", return_value=("k", classifier.GROQ_URL)), \
                mock.patch.object(classifier, "_post", return_value="safe"), \
                mock.patch.object(classifier, "_child_post", return_value="maybe?"):
            self.assertEqual(classifier.classify("hello"), "child-check-error")


class OllamaFallbackTests(unittest.TestCase):
    def setUp(self):
        self._prev = classifier.tests_are_allowing()
        self._force = classifier._FORCE_BACKEND
        classifier.allow_in_tests(False)
        classifier.force_backend(None)
        classifier.clear_cache()

    def tearDown(self):
        classifier.force_backend(self._force)
        classifier.allow_in_tests(self._prev)
        classifier.clear_cache()

    def _ollama_env(self, **extra):
        env = {
            "GROQ_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "ECHE_PROVIDER": "ollama",
            "GROQ_MODEL": "llama-guard3:8b",
        }
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=False)

    def _settings(self, **values):
        def lookup(key):
            return values.get(key, "")
        return mock.patch.object(classifier, "_checker_setting", side_effect=lookup)

    def test_checker_follows_the_configured_provider(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk-real", "OPENROUTER_API_KEY": "sk-or-real", "ECHE_PROVIDER": "cloud", "GROQ_MODEL": "qwen/qwen3.8-27b"}, clear=False), \
             mock.patch("core.secrets.load_all", return_value={}), \
             self._settings():
            key, url = classifier._endpoint()
            self.assertEqual(key, "gsk-real")
            self.assertEqual(url, classifier.GROQ_URL)
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk-real", "OPENROUTER_API_KEY": "sk-or-real", "ECHE_PROVIDER": "openrouter", "GROQ_MODEL": "openrouter/free"}, clear=False), \
             mock.patch("core.secrets.load_all", return_value={}), \
             self._settings():
            key, url = classifier._endpoint()
            self.assertEqual(key, "sk-or-real")
            self.assertIn("openrouter", url)
        with self._ollama_env(), \
             mock.patch("core.secrets.load_all", return_value={}), \
             self._settings():
            key, url = classifier._endpoint()
            self.assertFalse(key)
            self.assertEqual(url, classifier.OLLAMA_CHAT_URL)
            self.assertEqual(classifier.child_check_model(), "llama-guard3:8b")
            self.assertFalse(classifier.vision_backend_available())

    def test_missing_cloud_key_does_not_fall_back_to_another_provider(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "", "OPENROUTER_API_KEY": "sk-or-real", "ECHE_PROVIDER": "cloud", "GROQ_MODEL": "qwen/qwen3.8-27b"}, clear=False), \
             mock.patch("core.secrets.load_all", return_value={}), \
             self._settings():
            key, url = classifier._endpoint()
            self.assertIsNone(key)
            self.assertIsNone(url)
            self.assertFalse(classifier._has_text_backend())

    def test_env_cannot_pick_the_checker_model_or_ollama_url(self):
        with self._ollama_env(
            OLLAMA_HOST="http://evil.example:9",
            CHILD_CHECK_MODEL="something-else",
            CLASSIFIER_MODEL="other",
        ), self._settings(child_check_model="from-settings", child_check_provider="ollama"):
            key, url = classifier._endpoint()
            self.assertEqual(url, classifier.OLLAMA_CHAT_URL)
            self.assertEqual(classifier.child_check_model(), "from-settings")
            self.assertIn("127.0.0.1:11434", url)
            self.assertNotIn("evil", url)

    def test_ollama_content_filter_uses_chat_api_and_fail_closes(self):
        captured = []

        class Resp:
            def __init__(self, payload):
                self._raw = json.dumps(payload).encode()
                self.status = 200
            def read(self):
                return self._raw
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            captured.append((req.full_url, json.loads(req.data.decode()), timeout))
            return Resp({"message": {"role": "assistant", "content": "unsafe\nS4"}})

        with self._ollama_env(), \
             mock.patch("core.secrets.load_all", return_value={}), \
             self._settings(), \
             mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             mock.patch.object(classifier, "_child_check_sync", return_value=""):
            self.assertEqual(classifier.classify("a violent crime how-to"), "classifier-unsafe")
        self.assertTrue(captured)
        url, body, _timeout = captured[0]
        self.assertEqual(url, classifier.OLLAMA_CHAT_URL)
        self.assertEqual(body["model"], "llama-guard3:8b")
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertFalse(body.get("stream"))
        self.assertFalse(body.get("think"))
        roles = [m["role"] for m in body["messages"]]
        self.assertNotIn("system", roles)
        self.assertEqual(roles[0], "user")

        with self._ollama_env(), \
             mock.patch("core.secrets.load_all", return_value={}), \
             self._settings(), \
             mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            classifier.clear_cache()
            self.assertEqual(classifier.classify("hello"), "classifier-error")

    def test_ollama_child_check_uses_custom_category_and_safe_unsafe(self):
        captured = []
        audits = []

        class Resp:
            def __init__(self, payload):
                self._raw = json.dumps(payload).encode()
                self.status = 200
            def read(self):
                return self._raw
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            captured.append((req.full_url, json.loads(req.data.decode()), timeout))
            return Resp({"response": "unsafe\nS1"})

        with self._ollama_env(), \
             mock.patch("core.secrets.load_all", return_value={}), \
             self._settings(), \
             mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             mock.patch("core.audit.api_from_http", side_effect=lambda *a, **k: audits.append(k) or audits.append(a)):
            self.assertEqual(classifier._child_check_sync("a child in a garden"), "child-check-unsafe")
        self.assertTrue(captured)
        url, body, _timeout = captured[0]
        self.assertEqual(url, classifier.OLLAMA_GENERATE_URL)
        self.assertEqual(body["model"], "llama-guard3:8b")
        self.assertTrue(body.get("raw"))
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertIn(classifier.CHILD_CHECK_OLLAMA_CATEGORY, body["prompt"])
        self.assertIn("Minors or child-like characters", body["prompt"])
        self.assertTrue(audits)

        with self._ollama_env(), \
             mock.patch("core.secrets.load_all", return_value={}), \
             self._settings(), \
             mock.patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
            self.assertEqual(classifier._child_check_sync("hello"), "child-check-error")

    def test_images_are_refused_without_a_vision_backend(self):
        with self._ollama_env(), \
             mock.patch("core.secrets.load_all", return_value={}), \
             self._settings():
            self.assertFalse(classifier.vision_backend_available())
            self.assertEqual(
                classifier.classify("hi", images=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]),
                "classifier-vision-unavailable",
            )
            self.assertEqual(
                classifier._age_vision_sync([{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]),
                "age-vision-error",
            )

    def test_live_script_flag_can_force_ollama(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk-real", "OPENROUTER_API_KEY": "sk-or-real", "ECHE_PROVIDER": "cloud", "GROQ_MODEL": "qwen/qwen3.8-27b"}, clear=False), \
             self._settings():
            classifier.force_backend("ollama")
            key, url = classifier._endpoint()
            self.assertEqual(url, classifier.OLLAMA_CHAT_URL)
            spec = classifier._resolve_checker("child_check")
            self.assertEqual(spec.provider, "ollama")
            self.assertEqual(spec.model, "qwen/qwen3.8-27b")
