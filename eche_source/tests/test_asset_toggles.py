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
    ADULT_OUTPUT_IGNORE_LG4,
    AssetMode,
    adult_output_ignore_lg4,
    asset_adult_enabled,
    asset_approval_enabled,
    asset_approval_stored,
    channel_is_age_restricted,
    confirm_asset_delivery,
    hourly_caps,
    owner_only_required,
    parse_safety_flag,
    read_asset_mode,
    reset_observed_asset_mode,
    video_allowed,
    video_caps,
    _mode_from_store,
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

FOOTER_HASH = "dbb7d6ac3c29129fde736329e121f46d64827bf842f544c8c8fed7f60c669ec5"


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
            self.assertEqual((user_cap, guild_cap), (10, 40))
            self.assertIsNone(adult_output_ignore_lg4())

    def test_footer_hash_is_unchanged(self):
        self.assertEqual(footer_sha256(), FOOTER_HASH)

    def test_s12_is_the_only_ignored_output_category(self):
        self.assertEqual(ADULT_OUTPUT_IGNORE_LG4, frozenset({"S12"}))


class CouplingTests(unittest.TestCase):
    def test_runtime_18_plus_with_approval_on(self):
        mode, _invalid = _mode_from_store({
            "asset_adult_enabled": "1",
            "asset_approval_enabled": "1",
            "asset_owner_only": "0",
        })
        self.assertTrue(asset_approval_enabled(mode))
        self.assertTrue(video_allowed(mode))
        self.assertFalse(owner_only_required(mode))
        self.assertEqual(hourly_caps(mode), (10, 40))
        self.assertEqual(adult_output_ignore_lg4(mode), frozenset({"S12"}))

    def test_runtime_18_plus_can_run_with_approval_off(self):
        # The owner may run 18+ without reviewing outputs. Approval-off keeps
        # the same caps as approval on (10/40 images, 5/15 videos); 18+ stays on and is not
        # forced owner-only. Every minors check still runs (see GateTests).
        mode, _invalid = _mode_from_store({
            "asset_adult_enabled": "1",
            "asset_approval_enabled": "0",
            "asset_owner_only": "0",
        })
        self.assertTrue(mode.adult)
        self.assertFalse(asset_approval_enabled(mode))
        self.assertFalse(owner_only_required(mode))
        self.assertTrue(video_allowed(mode))
        self.assertEqual(video_caps(mode), (5, 15))
        self.assertEqual(hourly_caps(mode), (10, 40))
        self.assertEqual(adult_output_ignore_lg4(mode), frozenset({"S12"}))

    def test_caps_are_the_same_with_approval_on_or_off(self):
        from core.asset_limits import STANDARD_CAPS

        off, _ = _mode_from_store({"asset_adult_enabled": "0", "asset_approval_enabled": "0"})
        on, _ = _mode_from_store({"asset_adult_enabled": "0", "asset_approval_enabled": "1"})
        self.assertFalse(asset_approval_enabled(off))
        self.assertTrue(owner_only_required(off))
        for mode in (off, on):
            self.assertTrue(video_allowed(mode))
            self.assertEqual(hourly_caps(mode), STANDARD_CAPS.images)
            self.assertEqual(video_caps(mode), STANDARD_CAPS.videos)
            self.assertEqual(hourly_caps(mode), (10, 40))
            self.assertEqual(video_caps(mode), (5, 15))

    def test_save_time_18_plus_keeps_approval_off_and_audience(self):
        out = couple_asset_toggles({
            "asset_adult_enabled": "1",
            "asset_approval_enabled": "0",
            "asset_owner_only": "0",
        })
        self.assertEqual(out["asset_adult_enabled"], "1")
        self.assertEqual(out["asset_approval_enabled"], "0")
        self.assertEqual(out["asset_owner_only"], "0")

    def test_save_time_approval_off_without_18_plus_forces_owner_only(self):
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
            self.assertEqual(cfg.get("asset_approval_enabled"), "0")
            self.assertEqual(cfg.get("asset_owner_only"), "0")
            self.assertEqual(recorded.get("kind"), "settings-save")
            self.assertEqual(recorded.get("asset_adult_enabled_old"), "0")
            self.assertEqual(recorded.get("asset_adult_enabled_new"), "1")
            self.assertEqual(recorded.get("asset_approval_enabled_old"), "1")
            self.assertEqual(recorded.get("asset_approval_enabled_new"), "0")
            notify.assert_called()


class GateTests(unittest.TestCase):
    def test_18_plus_runs_sfw_outside_age_restricted_channels_and_refuses_dms(self):
        mode = AssetMode(enabled=True, adult=True, approval=True, owner_only=True, user_cap=10, guild_cap=30, video=True)
        with mock.patch("core.imagine.speaker_allowed", return_value=True), \
             mock.patch("core.asset_limits.rate_refusal", return_value=None), \
             mock.patch("core.asset_safety._audit"):
            for kwargs in ({"nsfw": False}, {"channel": _Channel(False)}):
                out = {}
                self.assertIsNone(refuse_speaker(True, guild_id=1, channel_id=2, asset_mode=mode, out=out, **kwargs))
                self.assertFalse(out["mode"].adult, "an ordinary channel runs under SFW rules")
                self.assertEqual(out["mode"].owner_only, mode.owner_only)
                self.assertEqual((out["mode"].user_cap, out["mode"].guild_cap), (10, 30))
            for kwargs in ({"nsfw": True}, {"channel": _Channel(True)}):
                out = {}
                self.assertIsNone(refuse_speaker(True, guild_id=1, channel_id=2, asset_mode=mode, out=out, **kwargs))
                self.assertTrue(out["mode"].adult)
            from core.asset_limits import DM_REFUSAL
            self.assertEqual(
                refuse_speaker(True, guild_id=None, channel_id=2, nsfw=True, asset_mode=mode),
                DM_REFUSAL,
            )
        self.assertTrue(ADULT_CHANNEL_REFUSAL)

    def test_18_plus_follows_the_owner_only_toggle(self):
        from core.imagine import speaker_allowed
        from core.asset_limits import DM_REFUSAL
        open_mode = AssetMode(enabled=True, adult=True, approval=True, owner_only=False, user_cap=10, guild_cap=30, video=True)
        self.assertTrue(speaker_allowed(True, open_mode))
        self.assertTrue(speaker_allowed(False, open_mode))
        locked = AssetMode(enabled=True, adult=True, approval=True, owner_only=True, user_cap=10, guild_cap=30, video=True)
        self.assertTrue(speaker_allowed(True, locked))
        self.assertFalse(speaker_allowed(False, locked))
        with mock.patch("core.asset_limits.rate_refusal", return_value=None), \
             mock.patch("core.asset_safety._audit"):
            self.assertIsNone(
                refuse_speaker(False, guild_id=1, channel_id=2, nsfw=True, asset_mode=open_mode),
            )
            # An ordinary channel is not refused; it runs under SFW rules.
            self.assertIsNone(
                refuse_speaker(False, guild_id=1, channel_id=2, nsfw=False, asset_mode=open_mode),
            )
            self.assertEqual(
                refuse_speaker(False, guild_id=None, channel_id=2, nsfw=True, asset_mode=open_mode),
                DM_REFUSAL,
            )

    def test_video_refused_only_when_video_is_switched_off(self):
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
            "a kawaii cafe mascot holding a teacup",
            "portrait of a waifu at sunset",
            "hentai ink study of two adults",
            "2D side-view of a market street",
            "a 3D render of a mountain cabin",
            "pixar-style kitchen interior",
            "ghibli hills under a blue sky",
            "an illustrated botanical poster",
            "a charcoal drawing of a bridge",
            "manhwa panel of a rainy street",
            "a vtuber model sheet",
            "claymation table scene",
            "an animated title card",
            "a mannequin in a shop window",
            "PVC figure on a desk",
            "cel shading on a motorcycle",
            "a shortstack adventurer in armor",
            "in the style of a coastal studio",
            "a アニメ street at night",
            "一幅動漫 poster on the wall",
            "Zeichentrick backdrop of a town",
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
            "a coastal lifestyle magazine cover",
            "a new hairstyle in a salon chair",
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
        with mock.patch("core.audit.record"), mock.patch(
            "core.asset_safety.read_asset_mode", return_value=_adult_mode()
        ):
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


def _adult_mode(**overrides):
    fields = dict(
        enabled=True,
        adult=True,
        approval=True,
        owner_only=True,
        user_cap=10,
        guild_cap=30,
        video=True,
    )
    fields.update(overrides)
    return AssetMode(**fields)


class SafetyFlagParseTests(unittest.TestCase):
    def test_only_explicit_tokens_disable_default_on_flags(self):
        for raw in ("0", "false", "NO", "Off"):
            value, invalid = parse_safety_flag(raw, default=True)
            self.assertFalse(value, raw)
            self.assertFalse(invalid, raw)
        for raw in ("1", "true", "yes", "on"):
            value, invalid = parse_safety_flag(raw, default=True)
            self.assertTrue(value, raw)
            self.assertFalse(invalid, raw)
        for raw in ("", None):
            value, invalid = parse_safety_flag(raw, default=True)
            self.assertTrue(value)
            self.assertFalse(invalid)
        for raw in ("enabled", "2", "maybe", "sure"):
            value, invalid = parse_safety_flag(raw, default=True)
            self.assertTrue(value, raw)
            self.assertTrue(invalid, raw)


class FileBackedToggleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="eche-toggles-")
        self.root = self._tmp.name
        reset_observed_asset_mode()

    def tearDown(self):
        reset_observed_asset_mode()
        self._tmp.cleanup()

    def _save(self, **flags):
        from core.secrets import save_all
        with mock.patch("core.audit.safe_activity"):
            save_all(flags, root=self.root)
        reset_observed_asset_mode()

    def test_invalid_approval_and_owner_only_keep_the_safe_value_and_are_audited(self):
        from core.secrets import load_all, save_all

        recorded = []

        def fake_record(kind, **fields):
            recorded.append((kind, fields))

        with mock.patch("core.audit.record", side_effect=fake_record), \
             mock.patch("core.audit.safe_activity"), \
             mock.patch("core.secrets._notify_asset_toggle_owners"):
            save_all({
                "asset_approval_enabled": "enabled",
                "asset_owner_only": "maybe",
            }, root=self.root)
        cfg = load_all(self.root)
        self.assertEqual(cfg.get("asset_approval_enabled"), "1")
        self.assertEqual(cfg.get("asset_owner_only"), "1")
        keys = {fields.get("key") for kind, fields in recorded if kind == "settings-invalid"}
        self.assertIn("asset_approval_enabled", keys)
        self.assertIn("asset_owner_only", keys)

    def test_failed_audit_reverts_the_save(self):
        from core.secrets import load_all, save_all

        with mock.patch("core.audit.safe_activity"), \
             mock.patch("core.audit.record"), \
             mock.patch("core.secrets._notify_asset_toggle_owners"):
            save_all({"asset_adult_enabled": "0", "asset_approval_enabled": "1"}, root=self.root)
        self.assertEqual(load_all(self.root).get("asset_adult_enabled"), "0")

        def boom(*_a, **_k):
            raise OSError("disk full")

        with mock.patch("core.audit.record", side_effect=boom), \
             mock.patch("core.audit.safe_activity", side_effect=boom), \
             mock.patch("core.secrets._notify_asset_toggle_owners"):
            with self.assertRaises(OSError):
                save_all({"asset_adult_enabled": "1"}, root=self.root)
        self.assertEqual(load_all(self.root).get("asset_adult_enabled"), "0")

    def test_save_log_uses_normalised_values(self):
        from core.secrets import save_all

        recorded = {}

        def fake_record(kind, **fields):
            if kind == "settings-save":
                recorded.update(fields)

        with mock.patch("core.audit.record", side_effect=fake_record), \
             mock.patch("core.audit.safe_activity"), \
             mock.patch("core.secrets._notify_asset_toggle_owners"):
            save_all({"asset_enabled": "yes", "asset_owner_only": "true"}, root=self.root)
        self.assertEqual(recorded.get("asset_enabled_old"), "0")
        self.assertEqual(recorded.get("asset_enabled_new"), "1")
        self.assertEqual(recorded.get("asset_owner_only_old"), "1")
        self.assertEqual(recorded.get("asset_owner_only_new"), "1")

    def test_hand_edit_is_observed_and_dms_from_the_bot_process(self):
        from core.secrets import _save_public

        self._save(asset_adult_enabled="0", asset_approval_enabled="1")
        first = read_asset_mode(self.root)
        self.assertFalse(first.adult)
        # A hand edit writes the file without going through save_all.
        from pathlib import Path
        path = Path(self.root) / "config" / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["asset_adult_enabled"] = "1"
        path.write_text(json.dumps(data), encoding="utf-8")
        sent = []

        def fake_notify(lines):
            sent.append("\n".join(lines))

        with mock.patch("core.secrets._notify_asset_toggle_owners", side_effect=fake_notify), \
             mock.patch("core.audit.record"):
            later = read_asset_mode(self.root)
        self.assertTrue(later.adult)
        self.assertTrue(any("18+" in text for text in sent))

    def test_mode_change_between_output_check_and_review_aborts(self):
        from core.imagine import ImagineError
        from core.asset_safety import MODE_CHANGED_REFUSAL

        self._save(
            asset_enabled="1",
            asset_adult_enabled="1",
            asset_approval_enabled="1",
        )
        start = read_asset_mode(self.root)
        self.assertTrue(start.adult)
        # Flip 18+ off after the output check, before review.
        self._save(
            asset_enabled="1",
            asset_adult_enabled="0",
            asset_approval_enabled="1",
        )
        reset_observed_asset_mode(start)
        with self.assertRaises(ImagineError) as err:
            confirm_asset_delivery(start, _Channel(True), guild_id=1, channel_id=2)
        self.assertEqual(err.exception.text, MODE_CHANGED_REFUSAL)

    def test_nsfw_channel_flip_aborts_before_review(self):
        from core.imagine import ImagineError
        from core.asset_safety import CHANNEL_CHANGED_REFUSAL

        mode = _adult_mode()
        with mock.patch("core.asset_safety.read_asset_mode", return_value=mode), \
             mock.patch("core.asset_safety._audit"):
            with self.assertRaises(ImagineError) as err:
                confirm_asset_delivery(mode, _Channel(False), guild_id=1, channel_id=2)
        self.assertEqual(err.exception.text, CHANNEL_CHANGED_REFUSAL)

    def test_review_message_includes_age_restricted(self):
        from core.asset_review import _review_message

        text = _review_message(user_id=1, guild_id=2, channel_id=3, age_restricted=True)
        self.assertIn("age-restricted: yes", text)
        text = _review_message(age_restricted=False)
        self.assertIn("age-restricted: no", text)


class S12IgnoredAuditTests(unittest.TestCase):
    def test_s12_only_pass_writes_an_audit_line(self):
        recorded = []

        def fake_record(kind, **fields):
            recorded.append((kind, fields))

        mode = _adult_mode()
        with mock.patch("core.audit.record", side_effect=fake_record), \
             mock.patch("core.asset_safety.read_asset_mode", return_value=mode):
            self.assertEqual(_verdict_to_check("unsafe\nS12", ignore_categories=frozenset({"S12"})), "")
        matches = [fields for kind, fields in recorded if fields.get("category") == "s12-ignored"]
        self.assertTrue(matches)
        self.assertEqual(matches[0].get("adult"), "1")
        self.assertEqual(matches[0].get("approval"), "1")

    def test_failed_s12_audit_fails_closed(self):
        def boom(*_a, **_k):
            raise OSError("log failed")

        with mock.patch("core.audit.record", side_effect=boom), \
             mock.patch("core.classifier.tests_are_allowing", return_value=False), \
             mock.patch("core.asset_safety.read_asset_mode", return_value=_adult_mode()):
            self.assertEqual(
                _verdict_to_check("unsafe\nS12", ignore_categories=frozenset({"S12"})),
                "classifier-error",
            )


class LimitsIsolationTests(unittest.TestCase):
    def test_source_tree_is_not_left_with_asset_limits(self):
        from pathlib import Path

        stray = Path(__file__).resolve().parent.parent / "config" / "asset_limits.json"
        if stray.is_file():
            stray.unlink()
        self.assertFalse(stray.is_file())


class AdultRuleTests(unittest.TestCase):
    def test_18_plus_sent_prompt_uses_the_stricter_preamble(self):
        from core.asset_safety import ADULT_RULE, ADULT_RULE_STRICT, build_sent_prompt

        default_sent, _digest = build_sent_prompt("a red paper boat", adult=False)
        adult_sent, _digest = build_sent_prompt("a red paper boat", adult=True)
        self.assertIn(ADULT_RULE, default_sent)
        self.assertNotIn(ADULT_RULE_STRICT, default_sent)
        self.assertIn(ADULT_RULE_STRICT, adult_sent)
        self.assertIn("25 years or older", adult_sent)
        from core.safety import SAFETY_FOOTER
        self.assertTrue(adult_sent.endswith(SAFETY_FOOTER))
        self.assertEqual(footer_sha256(), FOOTER_HASH)


class HelpCopyTests(unittest.TestCase):
    def test_18_plus_help_mentions_non_nsfw_refusal(self):
        from gui.widgets.settings_help import FIELD_HELP

        body = FIELD_HELP["asset_adult_enabled"][1] + FIELD_HELP["asset_enabled"][1]
        self.assertIn("non-NSFW", body)
