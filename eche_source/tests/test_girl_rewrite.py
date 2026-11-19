"""Owner-approved 2026-11-19: "girl(s)" -> "woman/women" in asset prompts
unless a youth/school cue is present. Mock-only; nothing is generated."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from core import asset_intent as AI
from core import asset_safety as S
from core.imagine import ImagineError


def _prep(prompt, *, adult=False, user_text="", model_prompt=""):
    rows = []
    with mock.patch.object(S, "_audit"), \
         mock.patch("core.audit.record", side_effect=lambda ev, **kw: rows.append((ev, kw))), \
         mock.patch("core.classifier.asset_prompt_check_async", new=mock.AsyncMock(return_value="")):
        out = asyncio.run(S.prepare_generation(
            prompt, mode=SimpleNamespace(adult=adult), user_text=user_text, model_prompt=model_prompt,
            user_id=1, guild_id=2, channel_id=3,
        ))
    return out, rows


class RewriteTests(unittest.TestCase):
    def test_goth_girls_pass_as_women(self):
        out, rows = _prep("goth girls at a concert", user_text="make goth girls at a concert",
                          model_prompt="goth girls at a concert")
        self.assertEqual(out.user_prompt, "goth women at a concert")
        self.assertNotIn("girl", out.sent_prompt.lower().split("safety rules")[0])
        strip = [kw for ev, kw in rows if ev == "asset-strip"]
        self.assertEqual(strip[0]["rule"], AI.GIRL_REWRITE_RULE)
        self.assertNotIn("goth", repr(strip[0]))  # rule name only, no prompt text

    def test_sexy_girl_in_18_plus(self):
        out, _ = _prep("sexy girl", adult=True)
        self.assertEqual(out.user_prompt, "sexy woman")

    def test_youth_cues_stay_blocked(self):
        for prompt in ("schoolgirl", "teen girl", "girl, 16", "petite girl in lingerie, sexy",
                       "young girl, sexy pose", "sexy petite girl", "young girl in a bikini"):
            self.assertEqual(AI.rewrite_adult_girls(prompt), (prompt, False), prompt)
            with self.assertRaises(ImagineError, msg=prompt):
                _prep(prompt, adult=True)

    def test_user_text_with_cue_still_blocks(self):
        # The model wrote a clean prompt, but the user's words have a youth cue.
        with self.assertRaises(ImagineError):
            _prep("a woman at a concert", adult=True, user_text="make a teen girl at a concert")

    def test_idioms_and_compounds_untouched(self):
        for text in ("girls night out", "my girlfriend", "catgirl", "girl scouts"):
            self.assertFalse(AI.rewrite_adult_girls(text)[1], text)


class ToolArgumentRewriteTests(unittest.TestCase):
    def test_on_message_rewrites_asset_arguments(self):
        import inspect
        from cogs.events import on_message as M

        src = inspect.getsource(M.OnMessage._deliver_tool_calls) if hasattr(M, "OnMessage") else inspect.getsource(M)
        self.assertIn("rewrite_adult_girls", src)
        from core.blocklist import is_blocked
        self.assertFalse(is_blocked(AI.rewrite_adult_girls("goth girls")[0]))
        self.assertTrue(is_blocked(AI.rewrite_adult_girls("teen girl")[0]))


if __name__ == "__main__":
    unittest.main()
