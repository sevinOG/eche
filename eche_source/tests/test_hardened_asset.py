# tests/test_hardened_asset.py
# Fail-closed safety for the official Grok Imagine / xAI ?asset backend.
from __future__ import annotations

import asyncio
import math
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.asset_filter import media_prompt_blocked
from core.asset_limits import (
    DM_REFUSAL,
    GUILD_HOURLY_CAP,
    LOCKOUT_REFUSAL,
    USER_HOURLY_CAP,
    in_direct_message,
    is_locked,
    lockout_user,
    rate_refusal,
    record_use,
)
from core.asset_review import set_review_hook
from core.asset_safety import (
    ADULT_RULE,
    FRAME_FPS,
    KILL_SWITCH_OFF,
    MISSING_KEY,
    OUTPUT_REFUSAL,
    OUTPUT_UNCHECKED,
    VISION_UNAVAILABLE,
    asset_enabled,
    build_sent_prompt,
    check_generated_media,
    extract_video_frames,
    footer_is_last,
    prepare_generation,
    probe_duration,
    require_enabled,
)
from core.blocklist import REFUSAL, is_blocked
from core.imagine import (
    AssetFile,
    AssetPlan,
    ImagineError,
    api_key,
    asset_audience,
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
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _make_mp4(path: Path, seconds: float = 2.0) -> bytes:
    cmd = [
        shutil.which("ffmpeg"),
        "-hide_banner",
        "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=red:s=32x32:d={seconds}",
        "-r", "30",
        "-pix_fmt", "yuv420p",
        "-y",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0 or not path.is_file():
        raise RuntimeError(result.stderr.decode("utf-8", "replace") or "ffmpeg failed")
    return path.read_bytes()


class HardenedAssetTests(unittest.TestCase):
    def setUp(self):
        set_review_hook(lambda *_a, **_k: True)

    def tearDown(self):
        set_review_hook(None)

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

    def test_request_for_uses_prepared_sent_prompt_and_requires_choke_point(self):
        from core.asset_safety import PreparedPrompt

        prepared = PreparedPrompt(
            user_prompt="a red paper boat",
            sent_prompt=build_sent_prompt("a red paper boat")[0],
            footer_hash=FOOTER_HASH,
        )
        plan = plan_asset("a red paper boat")
        with mock.patch("core.blocklist.is_blocked") as blocked:
            path, body = request_for(plan, prepared)
        blocked.assert_not_called()
        self.assertEqual(path, "/images/generations")
        self.assertTrue(footer_is_last(body["prompt"]))
        self.assertEqual(body["prompt"], prepared.sent_prompt)
        self.assertNotIn("negative_prompt", body)
        self.assertEqual(body["model"], "grok-imagine-image-2.0")
        with self.assertRaises(ImagineError):
            request_for(plan, None)
        with self.assertRaises(ImagineError):
            request_for(plan, PreparedPrompt(user_prompt="x", sent_prompt="", footer_hash=FOOTER_HASH))

    def test_plan_asset_refuses_source_edits(self):
        from core.imagine import SourceMedia

        source = SourceMedia(data=b"\xff\xd8\xff" + b"\x00" * 8, mime="image/jpeg")
        with self.assertRaises(ImagineError) as raised:
            plan_asset("make her dance", source=source)
        self.assertIn("not available", raised.exception.text)

    def test_http_json_does_not_matcher_scan_the_sent_body(self):
        sent, _ = build_sent_prompt("a red paper boat")
        body = {"model": "grok-imagine-image-2.0", "prompt": sent}

        class _Resp:
            status = 200
            headers = {"x-request-id": "job-aaa"}

            def read(self):
                return b'{"data":[{"url":"https://api.x.ai/v1/files/x"}],"request_id":"job-aaa"}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch("urllib.request.urlopen", return_value=_Resp()), \
             mock.patch("core.blocklist.is_blocked") as blocked, \
             mock.patch("core.imagine.check_payload"):
            result = http_json("POST", "https://api.x.ai/v1/images/generations", "sk-test", body)
        blocked.assert_not_called()
        status, payload = result
        self.assertEqual(status, 200)
        self.assertEqual(result.request_id, "job-aaa")

    def test_kill_switch_defaults_off(self):
        with mock.patch("core.secrets.public_value", return_value=""):
            self.assertFalse(asset_enabled())
        with mock.patch("core.secrets.public_value", return_value=None):
            self.assertFalse(asset_enabled())
        with mock.patch("core.secrets.public_value", side_effect=RuntimeError("no settings")):
            self.assertFalse(asset_enabled())
        message = refuse_speaker(True, user_id=11, guild_id=22, channel_id=33)
        self.assertEqual(message, KILL_SWITCH_OFF)

    def test_owner_only_defaults_on(self):
        with mock.patch("core.secrets.public_value", return_value=""):
            self.assertEqual(asset_audience(), "owner")
        with mock.patch("core.secrets.public_value", return_value=None):
            self.assertEqual(asset_audience(), "owner")
        with mock.patch("core.secrets.public_value", return_value="0"):
            self.assertEqual(asset_audience(), "all")

    def test_missing_key_fails_closed_from_config_only(self):
        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"XAI_API_KEY": "env-must-be-ignored"}, clear=False), \
             mock.patch("core.secrets.load_all", return_value={}):
            self.assertEqual(api_key(), "")
        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch("core.secrets.load_all", return_value={"xai_api_key": "cfg-key", "xai_key_epoch": "2026-11-hardened-asset"}):
            # api_key reads load_all() as returned; epoch stripping happens inside load_all itself.
            self.assertEqual(api_key(), "cfg-key")
        with mock.patch("core.asset_safety.asset_enabled", return_value=True):
            with self.assertRaises(ImagineError) as raised:
                asyncio.run(run_asset(AssetPlan(mode="image", prompt="a red paper boat"), ""))
            self.assertEqual(raised.exception.text, MISSING_KEY)

    def test_legacy_xai_key_is_ignored_until_reentered(self):
        from core.secrets import CURRENT_XAI_KEY_EPOCH, load_all, save_all

        with tempfile.TemporaryDirectory(prefix="eche-xai-epoch-") as raw:
            root = Path(raw)
            (root / "config").mkdir()
            (root / "config" / "settings.json").write_text(
                '{"xai_api_key": "legacy-plain-key", "xai_key_epoch": ""}',
                encoding="utf-8",
            )
            cfg = load_all(str(root))
            self.assertEqual(cfg.get("xai_api_key"), "")
            text = (root / "config" / "settings.json").read_text(encoding="utf-8")
            self.assertNotIn("legacy-plain-key", text)

            with mock.patch(
                "core.secrets._load_encrypted_secrets",
                return_value={"xai_api_key": "legacy-dpapi-key"},
            ):
                cfg = load_all(str(root))
                self.assertEqual(cfg.get("xai_api_key"), "")

            stored = {}

            def _capture(_root, secrets):
                stored.update(secrets)

            payload = {k: "" for k in list(cfg)}
            payload["xai_api_key"] = "new-entered-key"
            with mock.patch("core.secrets._save_encrypted_secrets", side_effect=_capture),                  mock.patch("core.secrets._load_encrypted_secrets", return_value={}):
                save_all(payload, str(root))
            self.assertEqual(stored.get("xai_api_key"), "new-entered-key")
            public = (root / "config" / "settings.json").read_text(encoding="utf-8")
            self.assertIn(CURRENT_XAI_KEY_EPOCH, public)
            self.assertNotIn("new-entered-key", public)

            with mock.patch(
                "core.secrets._load_encrypted_secrets",
                return_value={"xai_api_key": "new-entered-key"},
            ):
                again = load_all(str(root))
            self.assertEqual(again.get("xai_api_key"), "new-entered-key")
            self.assertEqual(again.get("xai_key_epoch"), CURRENT_XAI_KEY_EPOCH)

    def test_owner_only_is_an_extra_gate(self):
        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch("core.imagine.asset_audience", return_value="owner"), \
             mock.patch("core.asset_limits.is_locked", return_value=False), \
             mock.patch("core.asset_limits.rate_refusal", return_value=None):
            self.assertIsNone(refuse_speaker(True, user_id=1, guild_id=2, channel_id=3))
            self.assertIsNotNone(refuse_speaker(False, user_id=1, guild_id=2, channel_id=3))

    def test_dms_are_refused(self):
        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
             mock.patch("core.imagine.asset_audience", return_value="all"):
            self.assertTrue(in_direct_message(None))
            self.assertEqual(
                refuse_speaker(True, user_id=1, guild_id=None, channel_id=9),
                DM_REFUSAL,
            )

    def test_lockout_and_hourly_caps(self):
        with tempfile.TemporaryDirectory(prefix="eche-limits-") as raw:
            lockout_user(77, root=raw)
            self.assertTrue(is_locked(77, root=raw))
            self.assertFalse(is_locked(78, root=raw))
            for _ in range(USER_HOURLY_CAP):
                record_use(88, 99, root=raw)
            self.assertEqual(rate_refusal(88, 99, root=raw), "The hourly asset limit for this user has been reached.")
            for _ in range(GUILD_HOURLY_CAP):
                record_use(201, 99, root=raw)
            # user 201 may still be under the user cap; guild 99 is over after the first loop plus these
            self.assertIsNotNone(rate_refusal(202, 99, root=raw))

    def test_prepare_generation_fails_closed_on_classifier_error(self):
        with mock.patch("core.blocklist.is_blocked", return_value=False), \
             mock.patch("core.guardrails.asks_to_bypass_filter", return_value=False), \
             mock.patch("core.asset_filter.media_prompt_blocked", return_value=False), \
             mock.patch("core.classifier.classify_async", new=mock.AsyncMock(side_effect=TimeoutError("deadline"))):
            with self.assertRaises(ImagineError) as raised:
                asyncio.run(prepare_generation("a red paper boat"))
            self.assertEqual(raised.exception.text, REFUSAL)

    def test_media_filter_blocks_age_ambiguous_and_non_english(self):
        blocked = [
            "a tiny character sitting on a match",
            "twig character kisses match character and catches fire",
            "her son in the bath",
            "a newborn in a crib",
            "a little sister in a nightgown",
            "at six, bathtime",
            "pigtails, lollipop, knee socks, innocent",
            "petite fille dans un jardin",
            "少女 制服",
            "小女孩在花园里",
            "어린이 교복",
            "küçük kız",
            "एक बच्ची",
        ]
        for text in blocked:
            self.assertTrue(media_prompt_blocked(text), text)
            with mock.patch("core.blocklist.is_blocked", return_value=False), \
                 mock.patch("core.guardrails.asks_to_bypass_filter", return_value=False):
                with self.assertRaises(ImagineError):
                    asyncio.run(prepare_generation(text))
        self.assertFalse(media_prompt_blocked("a small red paper boat"))
        self.assertFalse(media_prompt_blocked("a tiny house on a hill"))

    def test_frame_check_blocks_and_fails_closed_when_unreadable(self):
        frame = b"\xff\xd8\xff" + b"\x00" * 16
        with mock.patch("core.asset_safety.extract_video_frames", return_value=[frame]), \
             mock.patch("core.classifier.classify_async", new=mock.AsyncMock(return_value="S2")), \
             mock.patch("core.classifier.age_vision_async", new=mock.AsyncMock(return_value="")):
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
        ), mock.patch("core.classifier.age_vision_async", new=mock.AsyncMock(return_value="")):
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
        with mock.patch("core.classifier.classify_async", new=mock.AsyncMock(return_value="")), \
             mock.patch("core.classifier.age_vision_async", new=mock.AsyncMock(return_value="age-vision-unsure")):
            with self.assertRaises(ImagineError) as raised:
                asyncio.run(check_generated_media(frame, "image/jpeg", footer_hash=FOOTER_HASH))
            self.assertEqual(raised.exception.text, OUTPUT_REFUSAL)

    @unittest.skipUnless(HAS_FFMPEG, "real ffmpeg is required")
    def test_extract_video_frames_uses_real_ffmpeg_at_four_fps(self):
        with tempfile.TemporaryDirectory(prefix="eche-ff-") as raw:
            path = Path(raw) / "clip.mp4"
            data = _make_mp4(path, seconds=2.0)
            duration = probe_duration(str(path))
            self.assertIsNotNone(duration)
            self.assertGreater(duration, 1.5)
            frames = extract_video_frames(data, "video/mp4")
            needed = math.ceil(duration * FRAME_FPS)
            self.assertGreaterEqual(len(frames), needed)
            for frame in frames:
                self.assertTrue(frame.startswith(b"\xff\xd8\xff"))
            self.assertEqual(extract_video_frames(data, "video/mp4") and True, True)
        # Unknown duration fails closed, even when the bytes are a real video.
        with mock.patch("core.asset_safety.probe_duration", return_value=None):
            self.assertEqual(extract_video_frames(data, "video/mp4"), [])

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
             mock.patch("core.asset_filter.media_prompt_blocked", return_value=False), \
             mock.patch("core.classifier.classify_async", new=mock.AsyncMock(return_value="")), \
             mock.patch("core.imagine.http_json", side_effect=_http), \
             mock.patch("core.asset_safety.check_generated_media", new=mock.AsyncMock()), \
             mock.patch("core.asset_limits.record_use"):
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

    def test_kill_switch_is_rechecked_before_posting(self):
        with mock.patch("core.asset_safety.asset_enabled", return_value=False):
            with self.assertRaises(ImagineError) as raised:
                require_enabled()
            self.assertEqual(raised.exception.text, KILL_SWITCH_OFF)

    def test_no_other_generator_is_reachable_from_run_asset(self):
        src = Path(__file__).resolve().parents[1] / "core" / "imagine.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("https://api.x.ai/v1", text)
        self.assertNotIn("generativelanguage.googleapis.com", text)
        self.assertNotIn("api.stability.ai", text)
        self.assertNotIn("pollinations", text)
        self.assertNotIn("openai.com/v1/images", text)
        self.assertNotIn("os.getenv", text)
        self.assertNotIn("os.environ", text)


class OfficialImagineHashTests(unittest.TestCase):
    def test_leaf_name_is_not_enough_and_hashes_are_pinned(self):
        from core.media_scan import (
            file_sha256,
            is_official_imagine_module,
            load_official_imagine_hashes,
            module_is_blocked,
        )

        root = Path(__file__).resolve().parents[1]
        self.assertTrue(is_official_imagine_module("core.imagine"))
        self.assertFalse(is_official_imagine_module("cogs.zz_probe.imagine"))
        self.assertFalse(is_official_imagine_module("imagine"))
        hashes = load_official_imagine_hashes(root)
        self.assertIn("core/imagine.py", hashes)
        self.assertEqual(hashes["core/imagine.py"], file_sha256(root / "core" / "imagine.py"))
        self.assertEqual(module_is_blocked("core.imagine", source_root=root), "")
        with tempfile.TemporaryDirectory(prefix="eche-hash-") as raw:
            probe = Path(raw)
            (probe / "cogs" / "zz_probe").mkdir(parents=True)
            sneak = probe / "cogs" / "zz_probe" / "imagine.py"
            sneak.write_text(
                "import requests\nrequests.post('https://api.x.ai/v1/images/generations')\n",
                encoding="utf-8",
            )
            self.assertFalse(is_official_imagine_module("cogs.zz_probe.imagine"))
            reason = module_is_blocked("cogs.zz_probe.imagine", source_root=probe)
            self.assertIn("generation marker", reason)


if __name__ == "__main__":
    unittest.main()
