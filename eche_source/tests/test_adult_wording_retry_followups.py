# tests/test_adult_wording_retry_followups.py
# Owner request 2026-11-19 (live 10:35-10:42 ET: stylized USA/UK prompts passed
# every prompt check, the output age-vision check flagged "apparent minor" /
# "uncertain age" twice; "Try a safer prompt" and "Yes it is" were declined as
# no-request-wording):
# 1. One adult wording on every image/video prompt and edit, free of youth
#    words and body-focused wording.
# 2. The one output-block retry adds stronger adult cues to the prompt.
# 3. A short go-ahead after Eche's own offer passes the request-wording gate.
# The output age-vision check is unchanged: yes and uncertain still block.
# Mocks only; nothing is generated.
from __future__ import annotations

import asyncio
import datetime as dt
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
from core import blocklist as B  # noqa: E402
from core import tools_asset as TA  # noqa: E402
from core.asset_safety import AssetMode  # noqa: E402
from core.imagine import ImagineError  # noqa: E402
from core.safety import SAFETY_FOOTER  # noqa: E402

SFW = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=10, guild_cap=40,
                video=True, video_user_cap=5, video_guild_cap=15)
ADULT = AssetMode(enabled=True, adult=True, approval=False, owner_only=False, user_cap=10, guild_cap=40,
                  video=True, video_user_cap=5, video_guild_cap=15)
FINAL_WORDING = ("All characters are adults aged 25 or older, with mature adult facial features and "
                 "adult proportions.")
WORDING_TEXTS = (S.ADULT_RULE, S.ADULT_RULE_STRICT, S._ADULT_HINT, S.ALWAYS_ADULT_LINE, S.RETRY_ADULT_LINE)
YOUTH_WORDS = ("child", "kid", "minor", "teen", "young", "youth", "underage", "baby", "infant", "toddler",
               "girl", "boy", "school", "under 18", "under 21", "loli")
BODY_WORDS = ("fully developed", "developed", "breast", "bust", "curv", "hips", "chest", "figure",
              "body proportions", "jawline", "stature")


class AdultWordingTests(unittest.TestCase):
    def test_final_wording(self):
        self.assertEqual(S.ADULT_WORDING, FINAL_WORDING)
        self.assertEqual(S.ADULT_RULE, FINAL_WORDING)
        self.assertTrue(S.ADULT_RULE_STRICT.startswith(FINAL_WORDING))

    def test_wording_has_no_youth_or_body_words_and_never_trips_the_minors_list(self):
        for text in WORDING_TEXTS:
            low = text.casefold()
            for word in YOUTH_WORDS + BODY_WORDS:
                self.assertNotIn(word, low, (word, text))
            self.assertEqual(B.match_info(text), ("", ""), text)
            self.assertEqual(B.match_info(text, force_context=True), ("", ""), text)
            self.assertEqual(B.match_info("persona: sexy " + text), ("", ""), text)

    def test_every_prompt_path_gets_the_wording_and_footer_last(self):
        for kwargs in ({}, {"edit": True}, {"adult": True}, {"adult": True, "edit": True}):
            for prompt in ("a red paper boat", "a goth woman on a bike", "make it blue"):
                sent, _ = S.build_sent_prompt(prompt, **kwargs)
                self.assertTrue(sent.startswith(FINAL_WORDING), (kwargs, prompt))
                self.assertIn(S.ALWAYS_ADULT_LINE, sent)
                self.assertTrue(S.footer_is_last(sent))

    def test_prepare_generation_sent_prompt_has_the_wording(self):
        # ?asset, the chat tool and edits all go through prepare_generation.
        for mode, edit in ((SFW, False), (SFW, True), (ADULT, False)):
            with mock.patch("core.audit.record"), \
                    mock.patch("core.classifier.asset_prompt_check_async", new=mock.AsyncMock(return_value="")):
                prepared = asyncio.run(S.prepare_generation(
                    "a confident woman personifying the USA", user_id=None, guild_id=1, channel_id=None,
                    mode=mode, edit=edit))
            self.assertTrue(prepared.sent_prompt.startswith(FINAL_WORDING))
            self.assertIn("mature adult woman aged 25 or older", prepared.sent_prompt)
            self.assertTrue(S.footer_is_last(prepared.sent_prompt))


class RetryCueTests(unittest.TestCase):
    def _prepared(self, prompt="a surreal painterly woman personifying the UK"):
        sent, digest = S.build_sent_prompt(prompt)
        return S.PreparedPrompt(user_prompt=prompt, sent_prompt=sent, footer_hash=digest)

    def test_retry_adds_cues_before_the_footer(self):
        prepared = self._prepared()
        stronger = S.strengthen_for_retry(prepared)
        self.assertIn(S.RETRY_ADULT_LINE, stronger.sent_prompt)
        self.assertIn("mid-30s", stronger.sent_prompt)
        self.assertIn("mature adult faces", stronger.sent_prompt)
        self.assertTrue(S.footer_is_last(stronger.sent_prompt))
        self.assertEqual(stronger.footer_hash, prepared.footer_hash)
        self.assertTrue(stronger.sent_prompt.startswith(prepared.sent_prompt[: -len(SAFETY_FOOTER)].rstrip()))
        self.assertNotIn(S.RETRY_ADULT_LINE, prepared.sent_prompt)

    def test_retry_cues_are_added_once(self):
        stronger = S.strengthen_for_retry(self._prepared())
        self.assertEqual(S.strengthen_for_retry(stronger).sent_prompt.count(S.RETRY_ADULT_LINE), 1)

    def test_no_footer_means_no_retry_prompt(self):
        broken = S.PreparedPrompt(user_prompt="x", sent_prompt="x without footer", footer_hash="h")
        with self.assertRaises(ValueError):
            S.strengthen_for_retry(broken)

    def test_retry_without_a_strengthenable_prompt_does_not_retry(self):
        from core import imagine as I

        plan = SimpleNamespace(prompt="a burger", user_text="a burger", model_prompt="a burger",
                               mode="image", source=None)
        prepared = SimpleNamespace(footer_hash="h", sent_prompt="no footer here")
        calls = []

        async def check(*a, **k):
            calls.append(1)
            raise ImagineError("blocked", status="safety_block")

        backend = mock.Mock()
        backend.api_base.return_value = "https://x"
        backend.image_url.return_value = "https://x/img"
        with mock.patch.object(I, "request_for", return_value=("/p", {})), \
                mock.patch.object(I, "http_json", return_value=(200, {})), \
                mock.patch.object(I, "http_bytes", return_value=(b"\xff\xd8\xffdata", "image/jpeg")), \
                mock.patch("core.asset_safety.require_enabled"), \
                mock.patch("core.asset_safety._audit"), \
                mock.patch("core.audit.record"):
            with self.assertRaises(ImagineError):
                asyncio.run(I._run_asset_reserved(
                    plan, "k", mock.Mock(), {"user_id": 1, "guild_id": 2, "channel_id": 3},
                    lambda: backend, mock.AsyncMock(return_value=prepared), check))
        self.assertEqual(len(calls), 1)


class AgeVisionUnchangedTests(unittest.TestCase):
    FRAME = b"\xff\xd8\xff\xe0" + b"\x00" * 16

    def test_yes_and_uncertain_still_block(self):
        for verdict in ("age-vision-yes", "age-vision-unsure"):
            with mock.patch("core.audit.record"), \
                    mock.patch("core.classifier.classify_media_async", new=mock.AsyncMock(return_value="")), \
                    mock.patch("core.classifier.age_vision_async", new=mock.AsyncMock(return_value=verdict)):
                with self.assertRaises(ImagineError) as raised:
                    asyncio.run(S.check_generated_media(self.FRAME, "image/jpeg", footer_hash="f" * 64,
                                                        user_id=6, mode=SFW))
            self.assertEqual(raised.exception.status, "safety_block", verdict)

    def test_pass_still_passes(self):
        with mock.patch("core.audit.record"), \
                mock.patch("core.classifier.classify_media_async", new=mock.AsyncMock(return_value="")), \
                mock.patch("core.classifier.age_vision_async", new=mock.AsyncMock(return_value="")):
            asyncio.run(S.check_generated_media(self.FRAME, "image/jpeg", footer_hash="f" * 64,
                                                user_id=6, mode=SFW))


OFFER = ("I can make that: a tall adult woman personifying the USA in a star-spangled jacket pinning a "
         "woman personifying the UK in a Union Jack coat, graphic novel style. Want it?")
PROMPT = ("Graphic novel style: a tall adult woman personifying the USA in a star-spangled jacket pinning "
          "an adult woman personifying the UK in a Union Jack coat")
BASE = dt.datetime(2026, 11, 19, 15, 40, tzinfo=dt.timezone.utc)


def _item(who, text, minutes):
    return SimpleNamespace(author=SimpleNamespace(id=who), content=text, embeds=[],
                           created_at=BASE - dt.timedelta(minutes=minutes))


def _ctx(text, history, *, replied_to=None):
    class Channel:
        id = 3

        def history(self, limit=None, before=None):
            async def gen():
                for item in history[:limit]:
                    yield item
            return gen()

    ref = None
    if replied_to is not None:
        ref = SimpleNamespace(resolved=replied_to)
    message = SimpleNamespace(content=text, reference=ref, channel=Channel(), created_at=BASE,
                              author=SimpleNamespace(id=5), guild=SimpleNamespace(id=2, me=SimpleNamespace(id=99)))
    return SimpleNamespace(message=message, bot=SimpleNamespace(user=SimpleNamespace(id=99)))


class FollowUpTests(unittest.TestCase):
    def test_short_followups(self):
        for text in ("yes", "Yes it is", "yes it is", "do it", "go", "try again", "Try a safer prompt",
                     "make it", "sure", "yeah go for it", "ok do it then", "<@99> yes please",
                     "try a different one", "send it", "the second one"):
            self.assertTrue(AI.is_short_followup(text), text)
        for text in ("lol", "what the heck man", "haha nice", "no", "nah", "stop", "why", "who are you",
                     "yes it is a nice day out there today", "that is cool", "",
                     "yes but also tell me about the weather in london right now"):
            self.assertFalse(AI.is_short_followup(text), text)

    def _run(self, ctx, prompt=PROMPT):
        rows = []
        with mock.patch("core.audit.record", side_effect=lambda ev, **f: rows.append((ev, f)) or {}), \
                mock.patch.object(TA, "_allowed", side_effect=RuntimeError("past the gate")):
            try:
                result = asyncio.run(TA.make_asset(ctx, {"prompt": prompt}))
            except RuntimeError:
                return "passed", rows
        return ("declined" if result.declined else "other"), rows

    def test_followups_after_eche_offer_pass(self):
        for text in ("Yes it is", "Try a safer prompt", "do it", "sure", "go"):
            outcome, rows = self._run(_ctx(text, [_item(99, OFFER, 1)]))
            self.assertEqual(outcome, "passed", text)
            if AI.decline_reason(PROMPT, text) != AI.DECLINE_NO_REQUEST:
                continue  # "do it" already reads as a request on its own
            self.assertIn(("followup", "follow-up-to-eche"),
                          [(f.get("outcome"), f.get("reason")) for ev, f in rows if ev == "asset-request"])

    def test_followup_as_reply_to_eche_passes(self):
        offer = _item(99, OFFER, 1)
        outcome, _ = self._run(_ctx("yes", [], replied_to=offer))
        self.assertEqual(outcome, "passed")

    def test_followup_after_refused_try_passes(self):
        history = [_item(99, B.REFUSAL, 1),
                   _item(5, "generate an image of personified USA woman dominating personified UK woman", 2)]
        outcome, _ = self._run(_ctx("Try a safer prompt", history),
                               prompt="painterly adult woman personifying the USA beside a personified UK woman")
        self.assertEqual(outcome, "passed")

    def test_unrelated_chatter_is_still_declined(self):
        for text in ("lol", "what the heck man", "haha nice one"):
            outcome, rows = self._run(_ctx(text, [_item(99, OFFER, 1)]))
            self.assertEqual(outcome, "declined", text)
            row = [f for ev, f in rows if ev == "asset-request"][-1]
            self.assertEqual(row["reason"], AI.DECLINE_NO_REQUEST)

    def test_followup_without_eche_in_the_conversation_is_declined(self):
        outcome, _ = self._run(_ctx("yes", [_item(7, OFFER, 1)]))  # another user's line
        self.assertEqual(outcome, "declined")
        outcome, _ = self._run(_ctx("yes", [_item(99, OFFER, 45)]))  # older than 30 minutes
        self.assertEqual(outcome, "declined")

    def test_followup_with_unrelated_prompt_is_declined(self):
        outcome, _ = self._run(_ctx("yes", [_item(99, OFFER, 1)]), prompt="a castle on the moon at night")
        self.assertEqual(outcome, "declined")


if __name__ == "__main__":
    unittest.main()
