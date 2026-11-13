# Settings-only text_safety_checks toggle: default ON, chat text only.
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import classifier as C
from core.secrets import ENV_MAP, PUBLIC_KEYS, save_all, settings_path


class ParseAndDefaultTests(unittest.TestCase):
    def test_default_is_on(self):
        self.assertTrue(C.parse_default_on_flag("")[0])
        self.assertTrue(C.parse_default_on_flag(None)[0])
        self.assertFalse(C.parse_default_on_flag("")[1])

    def test_only_explicit_off_tokens_disable_it(self):
        for raw in ("0", "false", "NO", "Off"):
            value, invalid = C.parse_default_on_flag(raw)
            self.assertFalse(value, raw)
            self.assertFalse(invalid, raw)
        for raw in ("1", "true", "yes", "on"):
            value, invalid = C.parse_default_on_flag(raw)
            self.assertTrue(value, raw)
            self.assertFalse(invalid, raw)
        for raw in ("maybe", "2", "enabled", "sure"):
            value, invalid = C.parse_default_on_flag(raw)
            self.assertTrue(value, raw)
            self.assertTrue(invalid, raw)

    def test_unreadable_settings_stay_on(self):
        with tempfile.TemporaryDirectory(prefix="eche-text-checks-") as raw:
            path = Path(raw) / "config"
            path.mkdir()
            (path / "settings.json").write_text("{not json", encoding="utf-8")
            with mock.patch("core.secrets._user_root", return_value=raw):
                self.assertTrue(C.read_text_safety_checks())

    def test_settings_only_not_in_env_map(self):
        self.assertIn("text_safety_checks", PUBLIC_KEYS)
        self.assertNotIn("text_safety_checks", ENV_MAP)


class AuditAndLogTests(unittest.TestCase):
    def test_save_all_audits_old_and_new(self):
        recorded = {}

        def fake_activity(event, **fields):
            recorded["event"] = event
            recorded.update(fields)

        with tempfile.TemporaryDirectory(prefix="eche-text-save-") as raw:
            with mock.patch("core.audit.safe_activity", side_effect=fake_activity), \
                 mock.patch("core.classifier.mark_checker_settings_saved"):
                save_all({"text_safety_checks": "0"}, root=raw)
        self.assertEqual(recorded.get("text_safety_checks_old"), "")
        self.assertEqual(recorded.get("text_safety_checks_new"), "0")

    def test_hand_edit_is_observed(self):
        recorded = []

        def fake_record(event, **fields):
            recorded.append((event, fields))
            return fields

        with tempfile.TemporaryDirectory(prefix="eche-text-hand-") as raw:
            save_all({"text_safety_checks": "1"}, root=raw)
            snap = {key: "" for key in C._CHECKER_WATCH_KEYS}
            snap["text_safety_checks"] = "1"
            C.reset_observed_checker_settings(snap)
            path = Path(settings_path(raw))
            data = json.loads(path.read_text(encoding="utf-8"))
            data["text_safety_checks"] = "0"
            path.write_text(json.dumps(data), encoding="utf-8")
            prev = C.tests_are_allowing()
            C.allow_in_tests(False)
            try:
                with mock.patch("core.audit.record", side_effect=fake_record), \
                     mock.patch("core.secrets._user_root", return_value=raw):
                    C.observe_checker_settings(raw)
            finally:
                C.allow_in_tests(prev)
        events = [item for item in recorded if item[0] == "settings-changed"]
        self.assertTrue(events)
        self.assertEqual(events[0][1].get("text_safety_checks_old"), "1")
        self.assertEqual(events[0][1].get("text_safety_checks_new"), "0")

    def test_startup_and_message_logs_name_skipped_checks(self):
        lines = []

        def fake_log(message, channel=""):
            lines.append((channel, message))

        with mock.patch.object(C, "read_text_safety_checks", return_value=False), \
             mock.patch("core.audit.safe_activity"), \
             mock.patch("core.gui_bridge.log", side_effect=fake_log):
            C.log_text_safety_checks(startup=True, enabled=False)
            C.log_text_safety_checks(enabled=False, skipped=["content_filter", "child_check"])
        blob = " ".join(msg for _ch, msg in lines)
        self.assertIn("content_filter", blob)
        self.assertIn("child_check", blob)
        self.assertIn("off", blob.lower())


class PathSeparationTests(unittest.TestCase):
    def setUp(self):
        self._prev = C.tests_are_allowing()
        C.allow_in_tests(False)
        C.clear_cache()
        C.reset_request_text_checks()

    def tearDown(self):
        C.allow_in_tests(self._prev)
        C.clear_cache()
        C.reset_request_text_checks()

    def test_direct_classify_still_runs_when_the_toggle_is_off(self):
        """Asset/image callers go through classify_async, which ignores the toggle."""
        captured = []

        def fake_post(messages, *a, **k):
            captured.append(messages)
            return "unsafe\nS4"

        with mock.patch.object(C, "read_text_safety_checks", return_value=False), \
             mock.patch.object(C, "_endpoint", return_value=("k", C.GROQ_URL)), \
             mock.patch.object(C, "_post", side_effect=fake_post), \
             mock.patch.object(C, "_child_check_sync", return_value=""), \
             mock.patch.object(C, "_resolve_checker", side_effect=lambda kind="content_filter": C.CheckerSpec(
                 kind, provider="cloud", model="meta-llama/llama-guard-4-12b", fmt="llama_guard",
                 field="content_filter_model",
             )):
            self.assertEqual(C.classify("a violent crime how-to"), "classifier-unsafe")
        self.assertTrue(captured)

    def test_chat_text_skips_model_checks_when_off(self):
        from core import client as cl

        prepared = [
            {"role": "user", "content": "hello there"},
            {"role": "system", "content": "footer"},
        ]
        called = []

        async def fake_classify(*_a, **_k):
            called.append("classify")
            return "classifier-unsafe"

        with mock.patch.object(C, "begin_request_text_checks", return_value=False), \
             mock.patch.object(C, "log_text_safety_checks"), \
             mock.patch.object(C, "classify_async", side_effect=fake_classify), \
             mock.patch("core.classifier.extract_images", return_value=[]), \
             mock.patch("core.safety.prepare_llm_messages", side_effect=lambda msgs: msgs), \
             mock.patch("core.safety.is_footer_message", return_value=False), \
             mock.patch("core.safety.is_user_guard", return_value=False):
            out = asyncio.run(cl._classify_outgoing(prepared))
        self.assertFalse(called)
        self.assertEqual(len(out), 2)

    def test_chat_images_still_run_model_checks_when_off(self):
        from core import client as cl

        prepared = [{"role": "user", "content": "look at this"}]
        called = []

        async def fake_classify(*_a, **_k):
            called.append(_k.get("images") or _a)
            return ""

        images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]
        with mock.patch.object(C, "begin_request_text_checks", return_value=False), \
             mock.patch.object(C, "log_text_safety_checks"), \
             mock.patch.object(C, "classify_async", side_effect=fake_classify), \
             mock.patch("core.classifier.extract_images", return_value=images), \
             mock.patch("core.safety.prepare_llm_messages", side_effect=lambda msgs: msgs), \
             mock.patch("core.safety.is_footer_message", return_value=False), \
             mock.patch("core.safety.is_user_guard", return_value=False):
            asyncio.run(cl._classify_outgoing(prepared))
        self.assertTrue(called)

    def test_output_path_skips_when_off(self):
        from core import client as cl

        called = []

        async def fake_classify(*_a, **_k):
            called.append("out")
            return "llm-output-classifier-unsafe"

        with mock.patch.object(C, "request_text_checks", return_value=False), \
             mock.patch.object(C, "log_text_safety_checks"), \
             mock.patch.object(C, "classify_response_async", side_effect=fake_classify), \
             mock.patch("core.blocklist.is_blocked", return_value=False), \
             mock.patch("core.blocklist.match_info", return_value=(None, None)):
            blocked = asyncio.run(cl._output_blocked("a perfectly ordinary reply"))
        self.assertFalse(blocked)
        self.assertFalse(called)

    def test_gui_label_names_disabled_checks(self):
        from gui.widgets import settings_help
        body = settings_help.FIELD_HELP["text_safety_checks"][1]
        self.assertIn("content filter", body.lower())
        self.assertIn("child check", body.lower())
        self.assertIn("footer", body.lower())
        src = Path(__file__).resolve().parents[1] / "gui" / "widgets" / "settingswindow.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("text_safety_checks_box", text)
        self.assertIn("Image and asset checks", text)


if __name__ == "__main__":
    unittest.main()
