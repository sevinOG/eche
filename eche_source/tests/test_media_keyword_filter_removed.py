# tests/test_media_keyword_filter_removed.py
# The keyword media filter (core.asset_filter.media_prompt_blocked) no longer
# gates asset prompts. Owner request 2026-11-19: it was almost all false
# positives next to the minors matcher, the retry catch, the school+sexual
# rule and the model child check, which all stay. Mocks only; nothing is
# generated.
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import asset_safety as S  # noqa: E402
from core import imagine as IM  # noqa: E402
from core.asset_safety import AssetMode  # noqa: E402
from core.blocklist import REFUSAL, is_blocked  # noqa: E402
from core.imagine import ImagineError  # noqa: E402

SFW = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=10, guild_cap=40,
                video=True, video_user_cap=5, video_guild_cap=15)
ADULT = AssetMode(enabled=True, adult=True, approval=False, owner_only=False, user_cap=10, guild_cap=40,
                  video=True, video_user_cap=5, video_guild_cap=15)

USER_TEXTS = (
    "@eche generate an image of personified USA woman dominating personified UK woman categorically",
    "@eche generate an image of photorealistic personified USA woman dominating personified UK woman categorically",
)
MODEL_PROMPTS = (
    "personified USA woman dominating personified UK woman categorically",
    "photorealistic personified USA woman dominating personified UK woman categorically",
)


class MediaKeywordFilterRemovedTests(unittest.TestCase):
    def _prepare(self, prompt, *, mode, user_text="", model_prompt="", child=""):
        seen = []

        async def child_check(text, **_k):
            seen.append(text)
            return child

        with tempfile.TemporaryDirectory() as raw, \
                mock.patch("core.secrets._user_root", return_value=raw), \
                mock.patch("core.audit.record"), \
                mock.patch("core.classifier.asset_prompt_check_async", new=child_check):
            prepared = asyncio.run(S.prepare_generation(
                prompt, user_id=None, guild_id=1, channel_id=None, mode=mode,
                user_text=user_text, model_prompt=model_prompt))
        return prepared, seen

    def test_prepare_generation_does_not_refuse_the_reported_prompts(self):
        for mode in (SFW, ADULT):
            for user_text, model_prompt in zip(USER_TEXTS, MODEL_PROMPTS):
                for prompt in (model_prompt, user_text):
                    with self.subTest(mode=mode.adult, prompt=prompt):
                        prepared, seen = self._prepare(
                            prompt, mode=mode, user_text=user_text, model_prompt=model_prompt)
                        self.assertIsNotNone(prepared)
                        self.assertEqual(len(seen), 1)  # reached the model child check

    def test_model_child_check_still_refuses(self):
        with self.assertRaises(ImagineError) as raised:
            self._prepare(MODEL_PROMPTS[1], mode=ADULT, user_text=USER_TEXTS[1], child="child-check-unsafe")
        self.assertEqual(raised.exception.text, REFUSAL)

    def test_other_keyword_gates_pass_the_reported_prompts(self):
        for text in USER_TEXTS + MODEL_PROMPTS:
            self.assertFalse(is_blocked(text), text)
            IM.check_prompt(text)  # raises on a refusal
            IM.check_payload({"revised_prompt": text, "caption": text})

    def test_asset_tool_call_arguments_pass_output_check(self):
        from core import client

        for text in MODEL_PROMPTS:
            calls = [{"name": "asset", "arguments": json.dumps({"prompt": text})}]
            self.assertFalse(asyncio.run(client._output_blocked("", tool_calls=calls)), text)

    def test_media_filter_is_not_called_on_the_asset_path(self):
        with mock.patch("core.asset_filter.media_prompt_blocked", side_effect=AssertionError("called")):
            prepared, _ = self._prepare(MODEL_PROMPTS[1], mode=ADULT, user_text=USER_TEXTS[1])
        self.assertIsNotNone(prepared)

    def test_minors_matcher_still_refuses(self):
        self.assertTrue(is_blocked("a 12 year old girl in a bikini"))
        with self.assertRaises(ImagineError):
            self._prepare("a 12 year old girl in a bikini", mode=SFW)


if __name__ == "__main__":
    unittest.main()
