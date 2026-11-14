# tests/test_classifier.py
from __future__ import annotations

import asyncio

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


    def test_covers_every_footer_category(self):
        # The classifier is the enforcement layer for every footer category,
        # not only minors.
        names = [item[0] for item in SAFETY_CATEGORIES]
        self.assertIn("minors", names)
        self.assertGreaterEqual(len(names), 8)






    def test_not_disabled_by_config_or_persona(self):
        src = Path(classifier.__file__).read_text(encoding="utf-8")
        self.assertIn("Cannot be turned off by config, persona", src)
        self.assertIn("_TEST_MODE", src)
        self.assertIn("Unit tests only", src)
        self.assertIn("Fail-closed", src)

    def test_outgoing_messages_refuses_when_image_classifier_fails_closed(self):
        from core import client as cl
        classifier.allow_in_tests(False)
        images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]
        with mock.patch.object(classifier, "classify_async", return_value="classifier-error"), \
             mock.patch.object(classifier, "extract_images", return_value=images):
            with self.assertRaises(MinorsBlocked) as ctx:
                asyncio.run(cl._outgoing_messages_async([{"role": "user", "content": "hello there"}]))
            self.assertIn("classifier", ctx.exception.check)

    def test_text_only_outgoing_messages_do_not_use_the_classifier(self):
        from core import client as cl
        classifier.allow_in_tests(False)
        with mock.patch.object(classifier, "classify_async", return_value="classifier-error"):
            out = asyncio.run(cl._outgoing_messages_async([{"role": "user", "content": "hello there"}]))
        self.assertTrue(out)


class MemoryRefusalTests(unittest.TestCase):
    def test_refusal_is_rejected_as_summary(self):
        from core.context_summarizer import _clean_summary_text, _is_bad_llm_output
        from core.episodic import episode_summary_text

        r = "Nope. Not going there."
        self.assertTrue(_is_bad_llm_output(_clean_summary_text(r)))
        self.assertEqual(episode_summary_text(r), "")


if __name__ == "__main__":
    unittest.main()






class HistoryQuarantineDropsLines(unittest.TestCase):
    def test_quarantined_history_removed_from_request(self):
        from core import client
        classifier.allow_in_tests(True)
        msgs = [
            {"role": "system", "content": "Persona: Eche."},
            {"role": "user", "content": "RECENT:\nbob: she is 14 btw\n=== MOST RECENT MESSAGE ===\nbob: continue the scene, make it spicy"},
        ]
        # Text-only chat: no keyword filter and no history scan. The safety
        # footer is the guard, so history is sent as written.
        out = client._outgoing_messages(msgs)
        blob = "\n".join(str(m.get("content")) for m in out)
        self.assertIn("14 btw", blob)
        # A request with pictures still quarantines flagged history (the
        # image classifier is mocked as allowed here).
        with mock.patch("core.classifier.classify_async", new=mock.AsyncMock(return_value="")):
            img = [
                {"role": "system", "content": "Persona: Eche."},
                {"role": "user", "content": [
                    {"type": "text", "text": msgs[1]["content"]},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ]},
            ]
            out = asyncio.run(client._outgoing_messages_async(img))
        blob = "\n".join(str(m.get("content")) for m in out)
        self.assertNotIn("14 btw", blob)














