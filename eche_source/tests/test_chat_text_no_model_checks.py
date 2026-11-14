# Text chat does not run model-based child checks (no Llama Guard at all). The footer and
# the minors keyword/blocklist stay. Image/asset checks stay always on.
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest import mock

from core import classifier as C
from core import client as client_mod
from core.secrets import ENV_MAP, PUBLIC_KEYS
from gui.widgets import settings_help


class ChatTextHasNoModelChecksTests(unittest.TestCase):
    def test_toggle_and_env_are_gone(self):
        self.assertNotIn("text_safety_checks", PUBLIC_KEYS)
        self.assertNotIn("text_safety_checks", ENV_MAP)
        self.assertFalse(hasattr(C, "read_text_safety_checks"))
        self.assertFalse(hasattr(C, "begin_request_text_checks"))
        self.assertFalse(hasattr(C, "log_text_safety_checks"))
        self.assertNotIn("text_safety_checks", settings_help.FIELD_HELP)
        ui = (Path(settings_help.__file__).resolve().parents[0] / "settingswindow.py").read_text(encoding="utf-8")
        self.assertNotIn("text_safety_checks_box", ui)
        self.assertNotIn("text_safety_checks", ui)

    def test_text_only_outgoing_does_not_call_classify_async(self):
        calls = []

        async def fake_classify(*_a, **_k):
            calls.append(1)
            return "classifier-unsafe"

        messages = [{"role": "user", "content": "Hey"}]
        with mock.patch.object(C, "classify_async", side_effect=fake_classify), \
             mock.patch.object(C, "extract_images", return_value=[]):
            out = asyncio.run(client_mod._classify_outgoing(messages))
        self.assertEqual(calls, [])
        self.assertTrue(out)

    def test_image_outgoing_still_calls_classify_async(self):
        calls = []

        async def fake_classify(*_a, **kwargs):
            calls.append(kwargs.get("images") or True)
            return ""

        images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]
        messages = [{"role": "user", "content": "look"}]
        with mock.patch.object(C, "classify_async", side_effect=fake_classify), \
             mock.patch.object(C, "extract_images", return_value=images):
            asyncio.run(client_mod._classify_outgoing(messages))
        self.assertTrue(calls)

    def test_image_outgoing_fail_closed(self):
        async def fake_classify(*_a, **_k):
            return "classifier-error"

        images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]
        with mock.patch.object(C, "classify_async", side_effect=fake_classify), \
             mock.patch.object(C, "extract_images", return_value=images):
            from core.safety import MinorsBlocked
            with self.assertRaises(MinorsBlocked):
                asyncio.run(client_mod._classify_outgoing([{"role": "user", "content": "look"}]))

    def test_output_block_uses_keywords_not_the_model(self):
        calls = []

        async def fake_classify(*_a, **_k):
            calls.append(1)
            return "classifier-unsafe"

        # classify_response_async (Llama Guard) no longer exists at all.
        self.assertFalse(hasattr(C, "classify_response_async"))
        with mock.patch.object(C, "_child_check_async", side_effect=fake_classify), \
             mock.patch.object(C, "classify_async", side_effect=fake_classify):
            blocked = asyncio.run(client_mod._output_blocked("Hey there"))
            self.assertFalse(blocked)
            self.assertEqual(calls, [])

    def test_footer_still_applied_to_text_chat(self):
        from core.safety import SAFETY_FOOTER, footer_sha256, prepare_llm_messages
        self.assertEqual(
            footer_sha256(),
            "dbb7d6ac3c29129fde736329e121f46d64827bf842f544c8c8fed7f60c669ec5",
        )
        messages = prepare_llm_messages([{"role": "user", "content": "Hey"}])
        blob = "\n".join(
            (m.get("content") if isinstance(m.get("content"), str) else "")
            for m in messages
        )
        self.assertIn(SAFETY_FOOTER, blob)

    def test_gui_copy_says_image_path_cannot_be_turned_off(self):
        ui = Path(settings_help.__file__).resolve().parents[0].joinpath("settingswindow.py").read_text(encoding="utf-8")
        self.assertIn("cannot be", ui.lower())
        self.assertIn("image and asset", ui.lower())
