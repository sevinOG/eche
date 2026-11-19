# tests/test_negated_minor_exclusions.py
# Live 2026-11-19 10:24 ET: the local chat model wrote a long, clearly adult
# asset prompt and appended safety boilerplate ("no children"), which the
# minors list refused as llm-output (term=children). Owner-approved fix:
# 1. The model is told never to mention minors or ages in an asset prompt,
#    not even to exclude them. That text carries no minors-list word itself.
# 2. Clearly negated exclusion clauses are stripped from the MODEL-WRITTEN
#    prompt before the checks and before generation. Anything ambiguous, or
#    any non-negated youth term anywhere, keeps the prompt as written and
#    blocked.
# 3. Logged prompts are cut at 1500 chars and carry a snippet around the hit.
# Mocks only; nothing is generated.
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import asset_intent as AI  # noqa: E402
from core import blocklist as B  # noqa: E402
from core import client  # noqa: E402
from core import prompt_text  # noqa: E402
from core import tools_asset as TA  # noqa: E402

LIVE_1024 = (
    "Surreal competitive illustration: a tall, confident adult woman personifying the USA (wearing a "
    "star-spangled athletic jacket, red-white-and-blue color scheme, arms crossed, smirking) standing over "
    "a shorter adult woman personifying the UK (Union Jack patterned coat, slightly disheveled, sitting on "
    "the ground looking defeated) in a dramatic sports-arena setting. The USA woman is pinning the UK woman "
    "down in a wrestling-style hold, decisive and categorical. Bold, exaggerated, slightly satirical "
    "cartoon style"
)
EXCLUSION_TAILS = (
    ", no children",
    ", no children.",
    ". No children.",
    ". All characters are adults, no children.",
    ", all adults, no minors",
    " (no children)",
    " (no minors, no kids)",
    ", no kids, teens, or minors",
    ". No minors depicted.",
    ". Strictly no minors.",
    ". Children not present.",
    ". Kids are not shown in the scene.",
    ". Not for children.",
    ". Not suitable for kids.",
    ". No one under 18.",
    ". Nobody under the age of 18.",
    ", without any children in the scene",
    ", no children or minors anywhere",
    ". Absolutely no underage characters.",
    "; zero kids",
    " - no children",
    "\nNo children.",
)
STILL_BLOCKED = (
    LIVE_1024 + ". No adults, only children.",
    LIVE_1024 + ", children with no parents",
    LIVE_1024 + ", no clothes children",
    LIVE_1024 + ", no children's toys",
    LIVE_1024 + ", no children were harmed",
    LIVE_1024 + ", no children\nchildren everywhere",
    LIVE_1024 + ", no children, a toddler in the corner",
    "a child, no children",
    "no children, just teens",
    "not children but kids",
    "a little girl, no minors",
    "a young girl, all adults, no kids",
    "kids playing, no adults",
    "she is not a child",
    "no children - teens only",
    "no child left behind",
    "no minors, a 12 year old",
    "no kid gloves, a kid",
    "a schoolgirl, no children",
    "a teen, no kids",
    "no children (except the teenager)",
    "two kids, no children",
    "no minors; minors only",
    "no children unless they are kids",
)


def _asset_call(prompt: str) -> list[dict]:
    return [{"name": "asset", "arguments": json.dumps({"prompt": prompt})}]


def _output_blocked(prompt: str) -> bool:
    token = client._request_images.set(False)
    try:
        with mock.patch("core.audit.record"):
            return asyncio.run(client._output_blocked("", tool_calls=_asset_call(prompt)))
    finally:
        client._request_images.reset(token)


class StripTests(unittest.TestCase):
    def test_live_prompt_and_exclusion_variants_strip_to_a_clean_prompt(self):
        self.assertEqual(B.match(LIVE_1024), "")
        for tail in EXCLUSION_TAILS:
            text = LIVE_1024 + tail
            # Most tails trip the minors list before the strip ("No one under
            # 18" does not, but is still removed so no age reaches the model).
            if "under" not in tail.casefold():
                self.assertTrue(B.match(text), tail)
            out, removed = AI.strip_negated_minor_exclusions(text)
            self.assertTrue(removed, tail)
            self.assertEqual(B.match(out), "", (tail, out))
            self.assertIn("pinning the UK woman down", out)
            for word in ("child", "kid", "minor", "teen", "underage", "under 18", "under the age"):
                self.assertNotIn(word, out.casefold(), (tail, out))

    def test_short_prompts(self):
        self.assertEqual(AI.strip_negated_minor_exclusions("a lighthouse, no children")[0], "a lighthouse")
        self.assertEqual(AI.strip_negated_minor_exclusions("two adult women boxing; no kids")[0],
                         "two adult women boxing")
        self.assertEqual(AI.strip_negated_minor_exclusions("a lighthouse")[0], "a lighthouse")

    def test_ambiguous_or_mixed_prompts_are_left_unchanged_and_block(self):
        for text in STILL_BLOCKED:
            out, removed = AI.strip_negated_minor_exclusions(text)
            self.assertEqual((out, removed), (text, []), text)
            self.assertTrue(B.match(out), text)

    def test_only_exclusion_returns_original(self):
        self.assertEqual(AI.strip_negated_minor_exclusions("no children"), ("no children", []))

    def test_strip_is_idempotent(self):
        out, _ = AI.strip_negated_minor_exclusions(LIVE_1024 + ", no children")
        self.assertEqual(AI.strip_negated_minor_exclusions(out), (out, []))

    def test_strip_is_logged(self):
        rows = []
        with mock.patch("core.audit.record", side_effect=lambda ev, **f: rows.append((ev, f)) or {}):
            AI.strip_model_exclusions_logged(LIVE_1024 + ", no children", where="llm-output")
        ev, f = rows[-1]
        self.assertEqual((ev, f["rule"], f["source"], f["removed"]),
                         ("asset-strip", AI.NEGATED_EXCLUSION_RULE, "llm-output", "no children"))


class OutputCheckTests(unittest.TestCase):
    def test_live_prompt_with_exclusions_passes_the_llm_output_check(self):
        self.assertFalse(_output_blocked(LIVE_1024))
        for tail in EXCLUSION_TAILS:
            self.assertFalse(_output_blocked(LIVE_1024 + tail), tail)

    def test_real_minor_phrasing_still_blocks_the_llm_output_check(self):
        for text in STILL_BLOCKED:
            self.assertTrue(_output_blocked(text), text)
        for text in ("USA teen dominating UK teen", "a young girl personifying the USA",
                     "a 12 year old girl personifying the USA", "personified USA kid vs UK kid"):
            self.assertTrue(_output_blocked(text), text)


class GenerationGetsStrippedPromptTests(unittest.TestCase):
    def test_make_asset_hands_the_stripped_prompt_on(self):
        seen = {}

        def plan(prompt, **_k):
            seen["prompt"] = prompt
            raise RuntimeError("stop after planning")

        message = SimpleNamespace(content="@eche generate an image of personified USA woman dominating "
                                          "personified UK woman categorically",
                                  reference=None, channel=None, author=SimpleNamespace(id=5), guild=None)
        ctx = SimpleNamespace(message=message, bot=None)
        with mock.patch("core.audit.record"), \
                mock.patch.object(TA, "_allowed", new=mock.AsyncMock(return_value=(True, ""))), \
                mock.patch("core.asset_source.source_from_reply", new=mock.AsyncMock(return_value=None)), \
                mock.patch.object(TA, "plan_asset", side_effect=plan), \
                mock.patch.object(TA, "announce_start", new=mock.AsyncMock()):
            asyncio.run(TA.make_asset(ctx, {"prompt": LIVE_1024 + ". All characters are adults, no children."}))
        self.assertIn("pinning the UK woman down", seen["prompt"])
        self.assertNotIn("children", seen["prompt"])


class ModelInstructionTests(unittest.TestCase):
    def test_model_is_told_not_to_mention_minors_or_ages(self):
        for text in (prompt_text._PICTURES_ON, prompt_text.TOOL_LINES["asset"], TA._DESCRIPTION):
            low = text.casefold()
            self.assertIn("adult", low)
            self.assertIn("not even to exclude", low)
            self.assertIn("under 18", low)

    def test_instruction_text_itself_never_trips_the_minors_list(self):
        # The system prompt is scanned on the image path; it must stay clean.
        for text in (prompt_text._PICTURES_ON, prompt_text.TOOL_LINES["asset"], TA._DESCRIPTION):
            self.assertEqual(B.match_info(text), ("", ""))
            self.assertEqual(B.match_info(text, force_context=True), ("", ""))
            self.assertEqual(B.match_info("persona: " + text), ("", ""))


class LoggingTests(unittest.TestCase):
    def test_snippet_shows_a_hit_deep_in_a_long_prompt(self):
        text = LIVE_1024 + " " + ("filler words " * 120) + "and a child in the corner"
        d = B.block_detail(text)
        self.assertEqual(d["term"], "child")
        self.assertIn("a child in the corner", d["snippet"])
        self.assertLessEqual(len(d["snippet"]), 2 * B.LOG_SNIPPET_RADIUS + len("child") + 2)
        self.assertLessEqual(len(d["prompt"]), 1500)

    def test_llm_output_row_has_snippet_and_long_prompt(self):
        rows = []
        prompt = LIVE_1024 + ", no children" + ", and two kids watching"
        token = client._request_images.set(False)
        try:
            with mock.patch("core.audit.record", side_effect=lambda ev, **f: rows.append((ev, f)) or {}):
                self.assertTrue(asyncio.run(client._output_blocked("", tool_calls=_asset_call(prompt))))
        finally:
            client._request_images.reset(token)
        ev, f = [r for r in rows if r[0] == "block"][-1]
        self.assertEqual(f["category"], "llm-output")
        self.assertIn(f["term"], ("children", "kids"))
        self.assertIn(f["term"], f["snippet"])
        self.assertGreater(len(f["prompt"]), 500)

    def test_gui_line_has_snippet(self):
        import os
        import tempfile

        logged = []
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch.dict(os.environ, {"ECHE_USER_ROOT": raw}, clear=False), \
                mock.patch("core.gui_bridge.log", side_effect=lambda line, channel="": logged.append(line)):
            B.note("llm-output", term="children", rule="always", prompt=LIVE_1024 + ", no children")
        joined = "\n".join(logged)
        self.assertIn("snippet=", joined)
        self.assertIn("no children", joined)


if __name__ == "__main__":
    unittest.main()
