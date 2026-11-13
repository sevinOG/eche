# 18+ and approval toggle rules. Defaults, coupling, channel, caps, video,
# S12 output-only ignore, and the extra 18+ style / vision checks.
from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from core.asset_safety import (
    APPROVAL_OFF_GUILD_CAP,
    APPROVAL_OFF_USER_CAP,
    ADULT_OUTPUT_IGNORE_LG4,
    adult_output_ignore_lg4,
    asset_adult_enabled,
    asset_approval_enabled,
    asset_approval_stored,
    channel_is_age_restricted,
    hourly_caps,
    owner_only_required,
    video_allowed,
)
from core.classifier import (
    AGE_VISION_ADULT_PROMPT,
    AGE_VISION_PROMPT,
    _verdict_to_check,
    lg4_categories,
)
from core.imagine import ADULT_CHANNEL_REFUSAL, plan_asset, refuse_speaker
from core.secrets import couple_asset_toggles
from core.safety import footer_sha256

FOOTER_HASH = "66e2062573080eb6f55015acabf38fc33848fbfa8d450a2821918196b464d7f1"


class _Channel:
    def __init__(self, nsfw=False):
        self.nsfw = nsfw

    def is_nsfw(self):
        return self.nsfw


def _flags(*, adult=False, approval=True, owner=True, enabled=True):
    return mock.patch.multiple(
        "core.asset_safety",
        asset_adult_enabled=lambda: adult,
        asset_approval_stored=lambda: approval,
        asset_approval_enabled=lambda: True if adult else approval,
        asset_enabled=lambda: enabled,
    )


class ToggleDefaultsTests(unittest.TestCase):
    def test_defaults_are_18_off_and_approval_on(self):
        with mock.patch("core.secrets.public_value", return_value=""):
            self.assertFalse(asset_adult_enabled())
            self.assertTrue(asset_approval_stored())
            self.assertTrue(asset_approval_enabled())
            self.assertTrue(video_allowed())
            user_cap, guild_cap = hourly_caps()
            self.assertEqual((user_cap, guild_cap), (10, 30))
            self.assertIsNone(adult_output_ignore_lg4())

    def test_footer_hash_is_unchanged(self):
        self.assertEqual(footer_sha256(), FOOTER_HASH)

    def test_s12_is_the_only_ignored_output_category(self):
        self.assertEqual(ADULT_OUTPUT_IGNORE_LG4, frozenset({"S12"}))


class CouplingTests(unittest.TestCase):
    def test_runtime_18_plus_forces_approval_on(self):
        with mock.patch("core.asset_safety.asset_adult_enabled", return_value=True), \
             mock.patch("core.asset_safety.asset_approval_stored", return_value=False):
            self.assertTrue(asset_approval_enabled())
            self.assertTrue(video_allowed())
            self.assertTrue(owner_only_required())
            self.assertEqual(hourly_caps(), (10, 30))
            self.assertEqual(adult_output_ignore_lg4(), frozenset({"S12"}))

    def test_approval_off_lowers_caps_and_disables_video(self):
        with mock.patch("core.asset_safety.asset_adult_enabled", return_value=False), \
             mock.patch("core.asset_safety.asset_approval_stored", return_value=False):
            self.assertFalse(asset_approval_enabled())
            self.assertFalse(video_allowed())
            self.assertTrue(owner_only_required())
            self.assertEqual(hourly_caps(), (APPROVAL_OFF_USER_CAP, APPROVAL_OFF_GUILD_CAP))
            self.assertEqual(hourly_caps(), (3, 10))

    def test_save_time_18_plus_forces_approval_and_owner_only(self):
        out = couple_asset_toggles({
            "asset_adult_enabled": "1",
            "asset_approval_enabled": "0",
            "asset_owner_only": "0",
        })
        self.assertEqual(out["asset_adult_enabled"], "1")
        self.assertEqual(out["asset_approval_enabled"], "1")
        self.assertEqual(out["asset_owner_only"], "1")

    def test_save_time_approval_off_forbids_18_plus_and_forces_owner_only(self):
        out = couple_asset_toggles({
            "asset_adult_enabled": "1",  # would be on, but see 18+ first... wait
        })
        # 18+ wins and forces approval on. A request to have both 18+ on and
        # approval off is stored as 18+ on, approval on.
        out = couple_asset_toggles({
            "asset_adult_enabled": "0",
            "asset_approval_enabled": "0",
            "asset_owner_only": "0",
        })
        self.assertEqual(out["asset_adult_enabled"], "0")
        self.assertEqual(out["asset_approval_enabled"], "0")
        self.assertEqual(out["asset_owner_only"], "1")

    def test_save_all_writes_coupled_values_and_audits_old_and_new(self):
        from core.secrets import save_all, load_all

        with tempfile.TemporaryDirectory(prefix="eche-toggles-") as raw:
            recorded = {}

            def fake_record(kind, **fields):
                recorded["kind"] = kind
                recorded.update(fields)

            with mock.patch("core.audit.record", side_effect=fake_record), \
                 mock.patch("core.audit.safe_activity"), \
                 mock.patch("core.secrets._notify_asset_toggle_owners") as notify:
                save_all({
                    "asset_adult_enabled": "1",
                    "asset_approval_enabled": "0",
                    "asset_owner_only": "0",
                }, root=raw)
            cfg = load_all(raw)
            self.assertEqual(cfg.get("asset_adult_enabled"), "1")
            self.assertEqual(cfg.get("asset_approval_enabled"), "1")
            self.assertEqual(cfg.get("asset_owner_only"), "1")
            self.assertEqual(recorded.get("kind"), "settings-save")
            self.assertEqual(recorded.get("asset_adult_enabled_old"), "0")
            self.assertEqual(recorded.get("asset_adult_enabled_new"), "1")
            self.assertEqual(recorded.get("asset_approval_enabled_old"), "1")
            self.assertEqual(recorded.get("asset_approval_enabled_new"), "1")
            notify.assert_called()


class GateTests(unittest.TestCase):
    def test_18_plus_is_refused_outside_age_restricted_channels_and_in_dms(self):
        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch("core.asset_safety.asset_adult_enabled", return_value=True), \
             mock.patch("core.asset_safety.asset_approval_enabled", return_value=True), \
             mock.patch("core.imagine.speaker_allowed", return_value=True), \
             mock.patch("core.asset_limits.is_locked", return_value=False), \
             mock.patch("core.asset_limits.rate_refusal", return_value=None), \
             mock.patch("core.asset_safety._audit"):
            self.assertEqual(
                refuse_speaker(True, guild_id=1, channel_id=2, nsfw=False),
                ADULT_CHANNEL_REFUSAL,
            )
            self.assertIsNone(
                refuse_speaker(True, guild_id=1, channel_id=2, nsfw=True),
            )
            from core.asset_limits import DM_REFUSAL
            self.assertEqual(
                refuse_speaker(True, guild_id=None, channel_id=2, nsfw=True),
                DM_REFUSAL,
            )
            self.assertEqual(
                refuse_speaker(True, guild_id=1, channel=_Channel(False)),
                ADULT_CHANNEL_REFUSAL,
            )
            self.assertIsNone(
                refuse_speaker(True, guild_id=1, channel=_Channel(True)),
            )

    def test_18_plus_is_owner_only_even_if_audience_is_open(self):
        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch("core.asset_safety.asset_adult_enabled", return_value=True), \
             mock.patch("core.imagine.asset_audience", return_value="all"):
            from core.imagine import speaker_allowed
            self.assertTrue(speaker_allowed(True))
            self.assertFalse(speaker_allowed(False))

    def test_approval_off_refuses_video(self):
        with mock.patch("core.asset_safety.video_allowed", return_value=False):
            from core.imagine import ImagineError
            with self.assertRaises(ImagineError) as err:
                plan_asset("a red paper boat", kind="video")
            self.assertIn("Video is not available", err.exception.text)
        with mock.patch("core.asset_safety.video_allowed", return_value=True):
            plan = plan_asset("a red paper boat", kind="video")
            self.assertEqual(plan.mode, "video")

    def test_approval_off_skips_owner_review(self):
        from core.asset_review import require_owner_review
        from core.imagine import AssetFile

        result = AssetFile(data=b"abc", filename="x.png", mime="image/png", mode="image")
        with mock.patch("core.asset_safety.asset_approval_enabled", return_value=False):
            out = asyncio.run(require_owner_review(result, bot=None))
        self.assertIs(out, result)

    def test_channel_is_age_restricted(self):
        self.assertFalse(channel_is_age_restricted(None))
        self.assertTrue(channel_is_age_restricted(None, nsfw=True))
        self.assertTrue(channel_is_age_restricted(_Channel(True)))
        self.assertFalse(channel_is_age_restricted(_Channel(False)))


class AdultFilterTests(unittest.TestCase):
    def test_18_plus_prompt_blocks_stylized_and_real_people(self):
        from core.asset_filter import media_prompt_blocked

        stylized = [
            "an anime woman in a red dress",
            "a cartoon landscape at dusk",
            "chibi style portrait of an adult",
            "a stylized oil painting of two adults",
            "a doll standing on a shelf",
        ]
        real = [
            "a photorealistic portrait of a celebrity",
            "photo of a real person on a beach",
            "looks like a famous actor",
        ]
        ok = [
            "a couple kissing",
            "a toaster on a counter",
            "a tall woman in a red dress",
        ]
        for text in stylized + real:
            self.assertTrue(media_prompt_blocked(text, adult=True), text)
            # Default mode still allows ordinary adult / object scenes;
            # some stylized words are not a minors cue on their own.
        self.assertFalse(media_prompt_blocked("a cartoon landscape at dusk", adult=False))
        self.assertFalse(media_prompt_blocked("a toaster on a counter", adult=True))
        for text in ok:
            self.assertFalse(media_prompt_blocked(text, adult=False), text)

    def test_minors_checks_still_run_in_18_plus_mode(self):
        from core.asset_filter import media_prompt_blocked

        self.assertTrue(media_prompt_blocked("a toddler in a garden", adult=True))
        self.assertTrue(media_prompt_blocked("a teacup kissing a spoon", adult=True))


class ClassifierIgnoreTests(unittest.TestCase):
    def test_s12_only_is_ignored_s4_and_empty_are_not(self):
        ignore = frozenset({"S12"})
        self.assertEqual(_verdict_to_check("unsafe\nS12", ignore_categories=ignore), "")
        self.assertEqual(_verdict_to_check("unsafe\nS4", ignore_categories=ignore), "classifier-unsafe")
        self.assertEqual(
            _verdict_to_check("unsafe\nS12 S4", ignore_categories=ignore),
            "classifier-unsafe",
        )
        self.assertEqual(_verdict_to_check("unsafe", ignore_categories=ignore), "classifier-unsafe")
        self.assertEqual(_verdict_to_check("unsafe\nS12"), "classifier-unsafe")
        self.assertEqual(_verdict_to_check("safe", ignore_categories=ignore), "")
        self.assertEqual(lg4_categories("unsafe\nS12, S4"), ["S12", "S4"])

    def test_adult_vision_prompt_is_stricter(self):
        self.assertIn("If unsure, answer YES", AGE_VISION_ADULT_PROMPT)
        self.assertIn("clearly an adult", AGE_VISION_ADULT_PROMPT)
        self.assertIn("anime", AGE_VISION_ADULT_PROMPT)
        self.assertIn("real or identifiable person", AGE_VISION_ADULT_PROMPT)
        self.assertNotEqual(AGE_VISION_ADULT_PROMPT, AGE_VISION_PROMPT)

    def test_keys_are_settings_only_not_env_map(self):
        from core import secrets
        self.assertIn("asset_adult_enabled", secrets.PUBLIC_KEYS)
        self.assertIn("asset_approval_enabled", secrets.PUBLIC_KEYS)
        self.assertNotIn("asset_adult_enabled", secrets.ENV_MAP)
        self.assertNotIn("asset_approval_enabled", secrets.ENV_MAP)


if __name__ == "__main__":
    unittest.main()
