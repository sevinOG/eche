# tests/test_hint_gate_live_0959.py
# Live 2026-11-14 ET on 366b2ad:
# - 09:59 "?asset generate what you think I look like eche" got no adult hint
#   (no person word matched) and the output was blocked as an apparent minor.
# - 09:31 "anthromorphic heavyset femlale c17" (misspelled) got no adult hint.
# - 10:01 "just do it based off my context" was declined by the intent gate
#   (prompt-not-from-message), and the declined row did not keep the tool
#   prompt, so the decline could not be reproduced.
# The hint is strengthen-only and every check still runs. Mocks only.
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import asset_intent as AI  # noqa: E402
from core import asset_safety as S  # noqa: E402
from core import tools as T  # noqa: E402
from core import tools_asset  # noqa: E402


class SelfPortraitHintTests(unittest.TestCase):
    def test_self_portrait_and_misspelled_prompts_get_the_hint(self):
        for prompt in ("generate what you think I look like eche", "draw yourself", "make a pic of me",
                       "what does she look like", "anthromorphic heavyset femlale c17"):
            self.assertTrue(S.character_adult_hint(prompt), prompt)
        self.assertIn("mature adult woman", S.character_adult_hint("anthromorphic heavyset femlale c17"))

    def test_object_only_prompts_still_get_no_hint(self):
        for prompt in ("a sunset over mountains", "a pink cockpit", "a cat sleeping", "a red sports car"):
            self.assertEqual(S.character_adult_hint(prompt), "", prompt)

    def test_sent_prompt_keeps_footer_last(self):
        sent, _ = S.build_sent_prompt("generate what you think I look like eche")
        self.assertIn("30s, 40s, or older", sent)
        self.assertTrue(S.footer_is_last(sent))


class GoAheadGateTests(unittest.TestCase):
    def test_go_ahead_messages_run(self):
        for text in ("just do it based off my context", "go ahead and draw it", "do it", "you decide, make one",
                     "make one based on our chat"):
            self.assertEqual(AI.decline_reason("a portrait from the conversation", text), "", text)

    def test_reactions_and_unrelated_prompts_are_still_declined(self):
        self.assertEqual(AI.decline_reason("a dragon", "what the heck man"), AI.DECLINE_NO_REQUEST)
        self.assertEqual(AI.decline_reason("a dragon over a castle", "make a cat in a hat"), AI.DECLINE_NO_OVERLAP)
        self.assertEqual(AI.decline_reason("a dragon", "ok stop trying lmfao"), AI.DECLINE_NO_REQUEST)

    def test_declined_row_keeps_the_tool_prompt(self):
        rows = []
        msg = SimpleNamespace(id=1, content="what the heck man", author=SimpleNamespace(id=3, name="u", bot=False),
                              guild=SimpleNamespace(id=9), channel=SimpleNamespace(id=2), reference=None,
                              attachments=[], embeds=[], stickers=[])
        ctx = T.ToolContext(bot=SimpleNamespace(get_command=lambda name: None), message=msg)
        with mock.patch.object(T, "default_tools_enabled", return_value=True), \
                mock.patch.object(T, "tool_prompt_enabled", return_value=True), \
                mock.patch.object(tools_asset, "run_asset", mock.AsyncMock(side_effect=AssertionError("no run"))), \
                mock.patch("core.audit.record", side_effect=lambda event, **kw: rows.append((event, kw))):
            result = asyncio.run(T.execute("asset", {"prompt": "jet girl dancing", "kind": "image"}, ctx))
        self.assertTrue(result.declined)
        row = [kw for e, kw in rows if e == "asset-request"][-1]
        self.assertEqual((row["outcome"], row["prompt"], row["message"]),
                         ("declined", "jet girl dancing", "what the heck man"))


if __name__ == "__main__":
    unittest.main()
