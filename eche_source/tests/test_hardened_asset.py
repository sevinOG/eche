# tests/test_hardened_asset.py
# Fail-closed safety for the official Grok Imagine / xAI ?asset backend.
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.asset_safety import (
    ADULT_RULE,
    KILL_SWITCH_OFF,
    MISSING_KEY,
    NEGATIVE_PROMPT,
    OUTPUT_REFUSAL,
    OUTPUT_UNCHECKED,
    VISION_UNAVAILABLE,
    asset_enabled,
    build_sent_prompt,
    check_generated_media,
    footer_is_last,
    prepare_generation,
    video_sample_times,
)
from core.blocklist import REFUSAL, is_blocked
from core.imagine import (
    AssetFile,
    AssetPlan,
    ImagineError,
    api_key,
    http_json,
    plan_asset,
    refuse_speaker,
    request_for,
    run_asset,
)
from core.safety import SAFETY_FOOTER, footer_sha256

INCIDENT = (
    "animation twig boy kisses match girl and catches fire and starts screaming "
    "horribly as match girl watches in horror at her boyfriend burning"
)
INCIDENT_COMMAND = "?asset " + INCIDENT
FOOTER_HASH = "66e2062573080eb6f55015acabf38fc33848fbfa8d450a2821918196b464d7f1"


class HardenedAssetTests(unittest.TestCase):
    def test_footer_hash_is_unchanged(self):
        self.assertEqual(footer_sha256(), FOOTER_HASH)

    def test_incident_prompt_is_blocked(self):
        self.assertTrue(is_blocked(INCIDENT))
        self.assertTrue(is_blocked(INCIDENT_COMMAND))

    def test_prepare_generation_blocks_incident_and_does_not_log_prompt(self):
        with tempfile.TemporaryDirectory(prefix="eche-asset-audit-") as raw:
            audit = Path(raw) / "logs"
            audit.mkdir()
            with mock.patch.dict(os.environ, {"ECHE_USER_ROOT": raw}, clear=False):
                with self.assertRaises(ImagineError) as raised:
                    asyncio.run(prepare_generation(INCIDENT, user_id=11, guild_id=22, channel_id=33))
                self.assertEqual(raised.exception.text, REFUSAL)
                lines = (audit / "audit.jsonl").read_text(encoding="utf-8") if (audit / "audit.jsonl").is_file() else ""
                self.assertNotIn("twig boy", lines)
                self.assertNotIn(INCIDENT, lines)
                self.assertIn("matcher", lines)

    def test_sent_prompt_has_footer_last(self):
        sent, digest = build_sent_prompt("a red paper boat")
        self.assertTrue(footer_is_last(sent))
        self.assertTrue(sent.endswith(SAFETY_FOOTER))
        self.assertEqual(sent.count(SAFETY_FOOTER), 1)
        self.assertTrue(sent.startswith(ADULT_RULE))
        self.assertIn("a red paper boat", sent)
        self.assertLess(sent.index("a red paper boat"), sent.index(SAFETY_FOOTER))
        self.assertEqual(digest, FOOTER_HASH)

    def test_max_prompt_applies_to_user_text_only(self):
        body = "x" * 2500
        sent, _digest = build_sent_prompt(body)
        self.assertTrue(footer_is_last(sent))
        self.assertTrue(sent.endswith(SAFETY_FOOTER))
        self.assertIn(ADULT_RULE, sent)
        self.assertLessEqual(len(sent) - len(ADULT_RULE) - len(SAFETY_FOOTER) - 4, 2000)

    def test_request_for_uses_prepared_sent_prompt_and_skips_matcher(self):
        from core.asset_safety import PreparedPrompt

        prepared = PreparedPrompt(
            user_prompt="a red paper boat",
            sent_prompt=build_sent_prompt("a red paper boat")[0],
            negative_prompt=NEGATIVE_PROMPT,
            footer_hash=FOOTER_HASH,
        )
        plan = plan_asset("a red paper boat")
        with mock.patch("core.blocklist.is_blocked") as blocked:
            path, body = request_for(plan, prepared)
        blocked.assert_not_called()
        self.assertEqual(path, "/images/generations")
        self.assertTrue(footer_is_last(body["prompt"]))
        self.assertEqual(body["prompt"], prepared.sent_prompt)
        self.assertEqual(body["negative_prompt"], NEGATIVE_PROMPT)
        self.assertEqual(body["model"], "grok-imagine-image-2.0")

    def test_http_json_does_not_matcher_scan_the_sent_body(self):
        sent, _ = build_sent_prompt("a red paper boat")
        body = {"model": "grok-imagine-image-2.0", "prompt": sent}

        class _Resp:
            status = 200
            headers = {}

            def read(self):
                return b'{"data":[{"url":"https://api.x.ai/v1/files/x"}]}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch("urllib.request.urlopen", return_value=_Resp()), \
             mock.patch("core.blocklist.is_blocked") as blocked, \
             mock.patch("core.imagine.check_payload"):
            http_json("POST", "https://api.x.ai/v1/images/generations", "sk-test", body)
        blocked.assert_not_called()

    def test_kill_switch_defaults_off(self):
        with mock.patch("core.secrets.public_value", return_value=""):
            self.assertFalse(asset_enabled())
        with mock.patch("core.secrets.public_value", return_value=None):
            self.assertFalse(asset_enabled())
        with mock.patch("core.secrets.public_value", side_effect=RuntimeError("no settings")):
            self.assertFalse(asset_enabled())
        message = refuse_speaker(True, user_id=11, guild_id=22, channel_id=33)
        self.assertEqual(message, KILL_SWITCH_OFF)

    def test_missing_key_fails_closed_from_config_only(self):
        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"XAI_API_KEY": "env-must-be-ignored"}, clear=False), \
             mock.patch("core.secrets.load_all", return_value={}):
            self.assertEqual(api_key(), "")
        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch("core.secrets.load_all", return_value={"xai_api_key": "cfg-key"}):
            self.assertEqual(api_key(), "cfg-key")
        with mock.patch("core.asset_safety.asset_enabled", return_value=True):
            with self.assertRaises(ImagineError) as raised:
                asyncio.run(run_asset(AssetPlan(mode="image", prompt="a red paper boat"), ""))
            self.assertEqual(raised.exception.text, MISSING_KEY)

    def test_owner_only_is_an_extra_gate(self):
        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch("core.imagine.asset_audience", return_value="owner"):
            self.assertIsNone(refuse_speaker(True, user_id=1, guild_id=2, channel_id=3))
            self.assertIsNotNone(refuse_speaker(False, user_id=1, guild_id=2, channel_id=3))

    def test_prepare_generation_fails_closed_on_classifier_error(self):
        with mock.patch("core.blocklist.is_blocked", return_value=False), \
             mock.patch("core.guardrails.asks_to_bypass_filter", return_value=False), \
             mock.patch("core.classifier.classify_async", new=mock.AsyncMock(side_effect=TimeoutError("deadline"))):
            with self.assertRaises(ImagineError) as raised:
                asyncio.run(prepare_generation("a red paper boat"))
            self.assertEqual(raised.exception.text, REFUSAL)

    def test_video_sample_times_include_first_last_and_each_second(self):
        times = video_sample_times(3.0)
        self.assertEqual(times[0], 0.0)
        self.assertEqual(times[-1], 3.0)
        self.assertIn(1.0, times)
        self.assertIn(2.0, times)
        self.assertGreaterEqual(len(times), 4)

    def test_frame_check_blocks_and_fails_closed_when_unreadable(self):
        frame = b"\xff\xd8\xff" + b"\x00" * 16
        with mock.patch("core.asset_safety.extract_video_frames", return_value=[frame]), \
             mock.patch("core.classifier.classify_async", new=mock.AsyncMock(return_value="S2")):
            with self.assertRaises(ImagineError) as raised:
                asyncio.run(check_generated_media(b"mp4-bytes", "video/mp4", footer_hash=FOOTER_HASH))
            self.assertEqual(raised.exception.text, OUTPUT_REFUSAL)
        with mock.patch("core.asset_safety.extract_video_frames", return_value=[]):
            with self.assertRaises(ImagineError) as raised:
                asyncio.run(check_generated_media(b"mp4-bytes", "video/mp4", footer_hash=FOOTER_HASH))
            self.assertEqual(raised.exception.text, OUTPUT_UNCHECKED)
        with mock.patch(
            "core.classifier.classify_async",
            new=mock.AsyncMock(return_value="classifier-image-unsupported"),
        ):
            with self.assertRaises(ImagineError) as raised:
                asyncio.run(check_generated_media(frame, "image/jpeg", footer_hash=FOOTER_HASH))
            self.assertEqual(raised.exception.text, VISION_UNAVAILABLE)
        with mock.patch(
            "core.classifier.classify_async",
            new=mock.AsyncMock(side_effect=RuntimeError("vision down")),
        ):
            with self.assertRaises(ImagineError) as raised:
                asyncio.run(check_generated_media(frame, "image/jpeg", footer_hash=FOOTER_HASH))
            self.assertEqual(raised.exception.text, OUTPUT_REFUSAL)

    def test_run_asset_returns_file_only_and_does_not_retry_a_block(self):
        import base64

        jpeg = b"\xff\xd8\xff" + b"\x00" * 32
        payload = {"data": [{"url": "b64:" + base64.b64encode(jpeg).decode("ascii")}]}
        calls = {"http": 0}

        def _http(*_a, **_k):
            calls["http"] += 1
            return 200, payload

        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch("core.blocklist.is_blocked", return_value=False), \
             mock.patch("core.guardrails.asks_to_bypass_filter", return_value=False), \
             mock.patch("core.classifier.classify_async", new=mock.AsyncMock(return_value="")), \
             mock.patch("core.imagine.http_json", side_effect=_http), \
             mock.patch("core.asset_safety.check_generated_media", new=mock.AsyncMock()):
            result = asyncio.run(
                run_asset(
                    AssetPlan(mode="image", prompt="a red paper boat"),
                    "cfg-key",
                    user_id=11,
                    guild_id=22,
                    channel_id=33,
                )
            )
        self.assertIsInstance(result, AssetFile)
        self.assertEqual(result.data, jpeg)
        self.assertEqual(calls["http"], 1)

        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch("core.classifier.classify_async", new=mock.AsyncMock(return_value="")), \
             mock.patch("core.imagine.http_json", side_effect=_http):
            with self.assertRaises(ImagineError):
                asyncio.run(run_asset(AssetPlan(mode="image", prompt=INCIDENT), "cfg-key"))
        self.assertEqual(calls["http"], 1, "a blocked prompt must not be retried or sent")

    def test_no_other_generator_is_reachable_from_run_asset(self):
        src = Path(__file__).resolve().parents[1] / "core" / "imagine.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("https://api.x.ai/v1", text)
        self.assertNotIn("generativelanguage.googleapis.com", text)
        self.assertNotIn("api.stability.ai", text)
        self.assertNotIn("pollinations", text)
        self.assertNotIn("openai.com/v1/images", text)
        # Key comes from load_all only.
        self.assertNotIn("os.getenv", text)
        self.assertNotIn("os.environ", text)


if __name__ == "__main__":
    unittest.main()
