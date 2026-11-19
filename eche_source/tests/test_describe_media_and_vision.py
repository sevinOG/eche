"""Live 2026-11-18 23:03-23:05 ET: a text-only chat model got video frames,
searched the web, then "Describe the video" made a new video. Mock-only."""

import unittest
from unittest import mock

from core import asset_intent as AI
from core import client as C


class DescribeIsNotMakeTests(unittest.TestCase):
    def test_describe_declined(self):
        for text in ("Describe the video", "what's happening in this video?", "explain this gif",
                     "can you see the picture", "what is in the image"):
            self.assertEqual(AI.decline_reason(text, text), AI.DECLINE_ASKS_ABOUT, text)

    def test_make_requests_still_run(self):
        for text, prompt in (("make a video of a burger car", "a burger car"),
                             ("describe a cat then make a picture of it", "a cat"),
                             ("add more women", "add more women"),
                             ("turn this video into anime", "this video as anime")):
            self.assertEqual(AI.decline_reason(prompt, text), "", text)


def _frames_msgs(n=6):
    content = [{"type": "text", "text": "what is in this video"}]
    content += [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}] * n
    return [{"role": "user", "content": content}]


class FramesReachModelTests(unittest.TestCase):
    def test_vision_model_keeps_frames(self):
        msgs = _frames_msgs()
        with mock.patch.object(C, "_provider_backend", return_value="ollama"), \
             mock.patch.object(C, "_ollama_capabilities", return_value=frozenset({"completion", "vision"})):
            self.assertFalse(C._ollama_lacks_vision("u", "m"))
        self.assertTrue(C._messages_have_images(msgs))
        self.assertEqual(sum(p.get("type") == "image_url" for p in msgs[0]["content"]), 6)

    def test_text_model_gets_note_not_frames(self):
        msgs = _frames_msgs()
        with mock.patch.object(C, "_provider_backend", return_value="ollama"), \
             mock.patch.object(C, "_ollama_capabilities", return_value=frozenset({"completion", "tools", "thinking"})):
            self.assertTrue(C._ollama_lacks_vision("u", "m"))
        C._flatten_image_messages(msgs)
        self.assertFalse(C._messages_have_images(msgs))
        self.assertIn("don't search the web", msgs[0]["content"])
        self.assertIn("don't make a new picture or video", msgs[0]["content"])


if __name__ == "__main__":
    unittest.main()
