"""Pictures are seen and edited again (live 2026-11-25 17:42 ET, "it cant see images now").

The reply image reached the model (Ollama decoded it in both calls), but the first call was
cut at the channel cap (61 tokens) before the asset call finished, and the repair call has no
tools, so "Make them badder" on Eche's picture came back as "I can't see an image to work
with here." Mocks only: no real picture, model or generation is used.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

import unittest  # noqa: E402

from tests.test_refinement import ModelTurn  # noqa: E402
from tests.test_watch_auto import ChatTurn, eche_post, message, picture  # noqa: E402
from core import builder  # noqa: E402
from core import watch_auto as A  # noqa: E402

PIC = b"\x89PNG-placeholder"  # placeholder bytes, not a picture


def media_with_picture():
    return SimpleNamespace(text="", images=[PIC], summary="", memory="", reply_images=[PIC])


class CutEditOnAPicture(ChatTurn):
    watch_tools = {"watch_video", "web_search", "asset"}

    def setUp(self):
        super().setUp()
        self.delivered = []

        async def fake_deliver(_self, _message, calls, *args, **kwargs):
            self.delivered.append([c["function"]["name"] for c in calls])
            return None

        d = mock.patch.object(self.om.OnMessage, "_deliver_tool_calls", fake_deliver)
        d.start()
        self.patches.append(d)
        self.settle = mock.AsyncMock(return_value=ModelTurn(reply="I can't see an image to work with here."))
        p = mock.patch.object(self.om, "settle_cut_reply", self.settle)
        p.start()
        self.patches.append(p)

    def turns(self, *turns):
        self.first.side_effect = list(turns)

    def test_cut_first_call_retries_with_tools_and_pictures(self):
        call = {"id": "c1", "type": "function",
                "function": {"name": "asset", "arguments": '{"prompt": "make them badder"}'}}
        self.turns(
            ModelTurn(reply='<tool_call>{"name": "asset", "arguments": {"prompt": "make them', cut=True),
            ModelTurn(reply="", tool_calls=[call]),
        )
        self.answer(message("Make them badder", replied=eche_post("", [picture()])), media_with_picture())
        self.assertGreaterEqual(self.first.call_count, 2)
        retry = self.first.call_args_list[1]
        self.assertTrue(retry.kwargs.get("tools"))
        self.assertTrue(retry.kwargs.get("images"))
        self.assertGreaterEqual(retry.kwargs["max_completion_tokens"], self.om.TOOL_RETRY_TOKENS)
        self.settle.assert_not_called()  # the tool-less repair is not what answers
        self.assertEqual(self.delivered, [["asset"]])
        self.assertNotIn("can't see an image", " ".join(self.sent))

    def test_retry_with_a_plain_reply_is_used(self):
        self.turns(
            ModelTurn(reply="Those four look", cut=True),
            ModelTurn(reply="Four adults in black, glaring at the camera."),
        )
        self.answer(message("<@1> what do you think of this", [picture()]), media_with_picture())
        self.settle.assert_not_called()
        self.assertEqual(self.sent, ["Four adults in black, glaring at the camera."])

    def test_plain_cut_text_turn_still_uses_the_repair(self):
        self.settle.return_value = ModelTurn(reply="Fine, short version.")
        self.turns(ModelTurn(reply="Well, the thing about that is", cut=True))
        self.answer(message("<@1> tell me about rome"))
        self.assertEqual(self.first.call_count, 1)
        self.settle.assert_called_once()
        self.assertEqual(self.sent, ["Fine, short version."])


class PictureRepliesAreNotRewritten(ChatTurn):
    def test_looking_at_a_picture_is_not_replaced(self):
        self.first.return_value = ModelTurn(reply="I'm looking at this pic and those masks have no eyes, creepy.")
        self.answer(message("<@1> rate this", [picture()]), media_with_picture())
        self.assertEqual(self.sent, ["I'm looking at this pic and those masks have no eyes, creepy."])

    def test_typed_watch_marker_is_still_stripped_on_a_picture_turn(self):
        self.first.return_value = ModelTurn(reply="[Watched the video from this message] Nice pic.")
        self.answer(message("<@1> rate this", [picture()]), media_with_picture())
        self.assertEqual(self.sent, ["Nice pic."])

    def test_text_only_denial_still_replaced(self):
        self.first.return_value = ModelTurn(reply="I don't have eyes, boss.")
        self.answer(message("<@1> can you watch stuff"))
        self.assertEqual(self.sent, [A.CAN_WATCH])


class PromptAndMemory(unittest.TestCase):
    def test_clean_reply_with_pictures_keeps_the_text(self):
        self.assertEqual(A.clean_reply("I'm looking at it, no eyes on that mask", watch_on=True, watched=False,
                                       pictures=True), "I'm looking at it, no eyes on that mask")

    def test_looking_at_is_not_pretend_watching(self):
        self.assertFalse(A.pretends_watching("I'm looking at this picture"))
        self.assertTrue(A.pretends_watching("I'm watching it now"))

    def test_memory_drops_old_picture_denials(self):
        out = A.scrub_rows([
            "You want me to make them badder? I can't see an image to work with here.",
            "I said I couldn't see the GIF because the link errored. Nice hat though.",
        ])
        joined = " ".join(out)
        self.assertNotIn("see an image", joined)
        self.assertNotIn("see the GIF", joined)
        self.assertIn("You want me to make them badder?", joined)
        self.assertIn("Nice hat though.", joined)
        self.assertEqual(A.scrub_text("I couldn't see the picture. lol"), "lol")

    def test_picture_note_says_they_can_be_seen(self):
        src = open(builder.__file__, encoding="utf-8").read()
        self.assertIn("You can see them directly; no tool is needed to look at a picture.", src)


if __name__ == "__main__":
    unittest.main()
