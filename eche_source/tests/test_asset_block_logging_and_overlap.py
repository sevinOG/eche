# tests/test_asset_block_logging_and_overlap.py
# Owner request 2026-11-19 (after live 09:54 / 09:55 / 10:00 ET):
# 1. Asset-path refusals log the matched term, the rule and the offending
#    prompt (cut to 1500 chars since #26) in the local audit log and desktop log.
# 2. A short request that follows Eche's own offer ("make the shirt") passes
#    the prompt-overlap gate when the tool prompt overlaps the recent
#    conversation. Unrelated prompts are still declined.
# 3. The chat-tool output check applies the owner-approved girl(s) ->
#    woman/women rewrite before the minors list, like the asset tool itself.
#    Genuine minor terms still block. "kid gloves" (leather) no longer does.
# Mocks only; nothing is generated.
from __future__ import annotations

import asyncio
import datetime as dt
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
from core import tools_asset as TA  # noqa: E402

GENUINE_MINOR_TEXTS = (
    "a child", "two kids", "a kid", "the kid's toy", "a teen", "a teenager", "an underage girl",
    "a minor", "loli", "a schoolgirl", "a young girl", "a little girl", "a 12 year old",
    "aged 15", "a toddler", "an infant", "a preteen", "jailbait", "a girl", "two girls", "a boy",
)


def _asset_call(prompt: str) -> list[dict]:
    return [{"name": "asset", "arguments": json.dumps({"prompt": prompt})}]


def _output_blocked(prompt: str) -> bool:
    token = client._request_images.set(False)
    try:
        return asyncio.run(client._output_blocked("", tool_calls=_asset_call(prompt)))
    finally:
        client._request_images.reset(token)


class BlockLoggingTests(unittest.TestCase):
    def test_block_detail_names_term_rule_and_prompt(self):
        d = B.block_detail("a cheerful teen holding a flag")
        self.assertEqual((d["term"], d["rule"]), ("teen", "always"))
        self.assertEqual(d["prompt"], "a cheerful teen holding a flag")
        self.assertEqual(B.block_detail("a lighthouse at dusk"), {})

    def test_block_detail_truncates_to_1500_chars(self):
        d = B.block_detail("a child " + "x" * 4000)
        self.assertLessEqual(len(d["prompt"]), 1500)

    def test_block_detail_reports_close_variant_without_remembering(self):
        B.clear_recent_blocks()
        try:
            B.remember_block(1, 2, "a toddler riding a red tricycle downhill")
            d = B.block_detail("a person riding a red tricycle downhill", user_id=1, channel_id=2)
            self.assertEqual(d["rule"], "close-variant")
            with B._recent_lock:
                self.assertEqual(len(B._recent[(1, 2)]), 1)
        finally:
            B.clear_recent_blocks()

    def test_llm_output_block_logs_term_rule_and_prompt(self):
        rows = []
        with mock.patch("core.audit.record", side_effect=lambda ev, **f: rows.append((ev, f)) or {}):
            self.assertTrue(_output_blocked("USA teen dominating UK teen"))
        ev, fields = rows[-1]
        self.assertEqual(ev, "block")
        self.assertEqual(fields["category"], "llm-output")
        self.assertEqual((fields["term"], fields["rule"]), ("teen", "always"))
        self.assertIn("USA teen dominating UK teen", fields["prompt"])

    def test_gui_line_shows_term_rule_and_prompt(self):
        import os
        import tempfile

        logged = []
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch.dict(os.environ, {"ECHE_USER_ROOT": raw}, clear=False), \
                mock.patch("core.gui_bridge.log", side_effect=lambda line, channel="": logged.append(line)):
            B.note("llm-output", term="teen", rule="always", prompt="USA teen vs UK teen")
        joined = "\n".join(logged)
        self.assertIn("Blocked (llm-output)", joined)
        self.assertIn("rule=always", joined)
        self.assertIn("term=teen", joined)
        self.assertIn("USA teen vs UK teen", joined)

    def test_prepare_generation_matcher_row_has_detail(self):
        from core import asset_safety as S
        from core.asset_safety import AssetMode
        from core.imagine import ImagineError

        mode = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=10,
                         guild_cap=40, video=True, video_user_cap=5, video_guild_cap=15)
        rows = []
        with mock.patch("core.audit.record", side_effect=lambda ev, **f: rows.append((ev, f)) or {}), \
                mock.patch("core.classifier.asset_prompt_check_async", new=mock.AsyncMock(return_value="")):
            with self.assertRaises(ImagineError):
                asyncio.run(S.prepare_generation("a woman", user_id=None, guild_id=1, channel_id=None,
                                                 mode=mode, model_prompt="a toddler on a swing"))
        row = [f for ev, f in rows if ev == "asset" and f.get("category") == "matcher"][-1]
        self.assertEqual((row["term"], row["rule"], row["reason"]), ("toddler", "always", "in=model"))
        self.assertEqual(row["prompt"], "a toddler on a swing")


SHIRT_OFFER = ("Want me to make you a t-shirt? I could do the squid-beak fermenter, or the "
               "chainsaw brewer with a foaming kettle.")
SHIRT_PROMPT = ("Bold vector print: a squid-beak fermenter mascot holding a chainsaw over a "
                "foaming brew kettle, distressed lettering")


class OverlapGateTests(unittest.TestCase):
    def test_t_shirt_spellings_share_a_word(self):
        self.assertTrue(AI.shares_meaningful_word("T-shirt design, squid mascot", "Generate the t shirt"))
        self.assertEqual(AI.decline_reason("T-shirt design, squid mascot", "Generate the t shirt"), "")

    def test_short_request_passes_on_conversation_overlap(self):
        self.assertEqual(AI.decline_reason(SHIRT_PROMPT, "Make the shirt man"), AI.DECLINE_NO_OVERLAP)
        self.assertEqual(AI.decline_reason(SHIRT_PROMPT, "Make the shirt man", SHIRT_OFFER), "")
        self.assertEqual(AI.decline_reason(SHIRT_PROMPT, "Generate the t shirt", SHIRT_OFFER), "")

    def test_unrelated_prompt_is_still_declined(self):
        self.assertEqual(
            AI.decline_reason("a castle on the moon at night", "Make the shirt man", SHIRT_OFFER),
            AI.DECLINE_NO_OVERLAP,
        )
        # Reactions with no request wording are declined whatever the context.
        self.assertEqual(AI.decline_reason(SHIRT_PROMPT, "what the heck man", SHIRT_OFFER),
                         AI.DECLINE_NO_REQUEST)

    def _message(self, history, *, replied=""):
        now = dt.datetime(2026, 11, 19, 15, 0, tzinfo=dt.timezone.utc)
        ref = SimpleNamespace(resolved=SimpleNamespace(content=replied)) if replied else None

        class Channel:
            def history(self, limit=None, before=None):
                async def gen():
                    for item in history[:limit]:
                        yield item
                return gen()

        return SimpleNamespace(
            content="Make the shirt man", reference=ref, channel=Channel(), created_at=now,
            author=SimpleNamespace(id=5), guild=SimpleNamespace(me=SimpleNamespace(id=99)),
        ), now

    def test_conversation_context_reads_eche_and_author_turns_only(self):
        base = dt.datetime(2026, 11, 19, 15, 0, tzinfo=dt.timezone.utc)

        def item(who, text, minutes):
            return SimpleNamespace(author=SimpleNamespace(id=who), content=text, embeds=[],
                                   created_at=base - dt.timedelta(minutes=minutes))

        history = [item(99, SHIRT_OFFER, 1), item(7, "squid-beak castle moon", 2), item(5, "lol yes", 3),
                   item(99, "ancient offer about volcanoes", 45)]
        message, _ = self._message(history, replied="the replied line")
        text = asyncio.run(TA._conversation_context(message))
        self.assertIn("squid-beak fermenter", text)
        self.assertIn("lol yes", text)
        self.assertIn("the replied line", text)
        self.assertNotIn("castle moon", text)  # another user's turn
        self.assertNotIn("volcanoes", text)  # older than 30 minutes

    def test_make_asset_runs_past_the_gate_with_context(self):
        base = dt.datetime(2026, 11, 19, 15, 0, tzinfo=dt.timezone.utc)
        offer = SimpleNamespace(author=SimpleNamespace(id=99), content=SHIRT_OFFER, embeds=[],
                                created_at=base - dt.timedelta(minutes=1))
        message, _ = self._message([offer])
        ctx = SimpleNamespace(message=message, bot=SimpleNamespace(user=SimpleNamespace(id=99)))
        rows = []
        sentinel = RuntimeError("past the gate")
        with mock.patch("core.audit.record", side_effect=lambda ev, **f: rows.append((ev, f)) or {}), \
                mock.patch.object(TA, "_allowed", side_effect=sentinel):
            with self.assertRaises(RuntimeError):
                asyncio.run(TA.make_asset(ctx, {"prompt": SHIRT_PROMPT}))
        self.assertIn(("asset-request", "context-overlap"),
                      [(ev, f.get("outcome")) for ev, f in rows])

    def test_make_asset_still_declines_unrelated_prompt(self):
        base = dt.datetime(2026, 11, 19, 15, 0, tzinfo=dt.timezone.utc)
        offer = SimpleNamespace(author=SimpleNamespace(id=99), content=SHIRT_OFFER, embeds=[],
                                created_at=base - dt.timedelta(minutes=1))
        message, _ = self._message([offer])
        ctx = SimpleNamespace(message=message, bot=SimpleNamespace(user=SimpleNamespace(id=99)))
        rows = []
        with mock.patch("core.audit.record", side_effect=lambda ev, **f: rows.append((ev, f)) or {}):
            result = asyncio.run(TA.make_asset(ctx, {"prompt": "a castle on the moon at night"}))
        self.assertTrue(result.declined)
        row = [f for ev, f in rows if ev == "asset-request"][-1]
        self.assertEqual((row["outcome"], row["reason"]), ("declined", AI.DECLINE_NO_OVERLAP))


class OutputCheckFalsePositiveTests(unittest.TestCase):
    def test_usa_uk_prompts_pass_the_chat_tool_output_check(self):
        for prompt in (
            "personified USA woman dominating personified UK woman categorically",
            "photorealistic personified USA woman dominating personified UK woman categorically",
            "USA girl dominating UK girl",
            "American girl in stars and stripes dominating a British girl in a Union Jack dress",
            "Lady Liberty dominating Britannia, both personified as women",
            "petite personified USA woman dominating a cute personified UK woman",
            "personified USA woman handling personified UK woman with kid gloves",
        ):
            self.assertFalse(_output_blocked(prompt), prompt)

    def test_genuine_minor_wording_still_blocks_in_the_output_check(self):
        for prompt in (
            "USA teen dominating UK teen",
            "a schoolgirl version of the USA dominating the UK",
            "a young girl personifying the USA",
            "a little girl personifying the UK",
            "USA girl dominating UK girl at school",
            "a 12 year old girl personifying the USA",
            "personified USA kid vs UK kid",
            "a child personifying Britain",
            "an underage girl as the USA",
        ):
            self.assertTrue(_output_blocked(prompt), prompt)

    def test_minors_terms_are_unchanged_in_the_matcher(self):
        for text in GENUINE_MINOR_TEXTS:
            self.assertTrue(B.match(text), text)
        for text in ("kids in kid gloves", "a kid wearing kid gloves", "my kid's kid leather jacket"):
            self.assertTrue(B.match(text), text)

    def test_kid_leather_is_not_a_minor(self):
        for text in ("handled with kid gloves", "a kid-glove approach", "kid leather boots", "kidskin purse"):
            self.assertEqual(B.match(text), "", text)


if __name__ == "__main__":
    unittest.main()
