"""2026-11-18 22:06 ET follow-ups: 25-30 wording, no Making line,
text-only chat model note, viewed-media audit hashes. Mock-only."""

import base64
import unittest
from types import SimpleNamespace
from unittest import mock

from core import asset_safety as S
from core import client as C


class WordingTests(unittest.TestCase):
    def test_adult_lines(self):
        self.assertIn("clearly adults aged 25 to 30", S.ALWAYS_ADULT_LINE)
        self.assertIn("scenes with no people are fine", S.ALWAYS_ADULT_LINE)
        hint = S.character_adult_hint("a woman on a bike")
        for needle in ("aged 25 to 30", "unmistakably", "adult jawline", "adult height",
                       "Keep the requested art style."):
            self.assertIn(needle, hint)
        self.assertNotIn("40s", hint + S.ALWAYS_ADULT_LINE)

    def test_footer_still_last(self):
        from core.safety import SAFETY_FOOTER
        sent, _ = S.build_sent_prompt("a red boat")
        self.assertTrue(sent.endswith(SAFETY_FOOTER))
        self.assertIn("aged 25 to 30", sent)

    def test_no_making_constants(self):
        import core.announce as A
        self.assertFalse(hasattr(A, "MAKING_IMAGE"))
        self.assertFalse(hasattr(A, "making_line"))


def _img_msgs():
    return [{"role": "user", "content": [
        {"type": "text", "text": "what is this"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
    ]}]


class TextOnlyModelTests(unittest.TestCase):
    def test_flatten_adds_note(self):
        msgs = _img_msgs()
        self.assertTrue(C._flatten_image_messages(msgs))
        self.assertIn("what is this", msgs[0]["content"])
        self.assertIn(C.IMAGES_DROPPED_NOTE, msgs[0]["content"])
        plain = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        C._flatten_image_messages(plain)
        self.assertNotIn("can't see", plain[0]["content"])

    def test_lacks_vision(self):
        with mock.patch.object(C, "_provider_backend", return_value="ollama"):
            with mock.patch.object(C, "_ollama_capabilities", return_value=frozenset({"completion", "tools"})):
                self.assertTrue(C._ollama_lacks_vision("u", "m"))
            with mock.patch.object(C, "_ollama_capabilities", return_value=frozenset({"completion", "vision"})):
                self.assertFalse(C._ollama_lacks_vision("u", "m"))
            with mock.patch.object(C, "_ollama_capabilities", return_value=None):
                self.assertFalse(C._ollama_lacks_vision("u", "m"))
        with mock.patch.object(C, "_provider_backend", return_value="cloud"):
            self.assertFalse(C._ollama_lacks_vision("u", "m"))


class ViewedShaTests(unittest.TestCase):
    def test_frame_and_own_parts_hashed(self):
        from core import chat_media as M
        from core.message_media import image_part

        frame = image_part(b"frame-bytes", "image/jpeg")
        self.assertEqual(M._part_sha(frame), M.sha256_hex(b"frame-bytes"))
        rows = []
        media = SimpleNamespace(reply_images=[], images=[], video_parts=[frame, frame], kinds=["video"], reply_parts=[])
        msg = SimpleNamespace(author=SimpleNamespace(id=1), guild=SimpleNamespace(id=2), channel=SimpleNamespace(id=3))
        with mock.patch("core.message_media.fit_images", return_value=[]), \
             mock.patch("core.audit.record", side_effect=lambda ev, **kw: rows.append(kw)):
            M.prepare_viewed(media, msg)
        self.assertNotIn("-", rows[0]["sha256"].split(","))
        self.assertEqual(rows[0]["sha256"].split(",")[0], M.sha256_hex(b"frame-bytes")[:16])
        self.assertEqual(len(media.video_parts), 2)
        self.assertEqual(base64.b64decode(frame["image_url"]["url"].split(",")[1]), b"frame-bytes")


if __name__ == "__main__":
    unittest.main()
