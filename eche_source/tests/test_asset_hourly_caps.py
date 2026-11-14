# Hourly ?asset caps (owner requests 2026-11-13 21:39 and 21:54 ET).
# Per user: 10 images and 5 videos an hour, counted separately (a video
# uses no image slot). Per server: 40 images and 15 videos. The same with
# owner approval on or off. Video works with approval off. The owner is
# exempt. Blocked or failed tries give their slot back. No live calls and
# no media generated; the pipeline after the cap is mocked.

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from core import asset_limits as L  # noqa: E402
from core import asset_source as SRC  # noqa: E402
from core.asset_safety import _mode_from_store, video_allowed, video_caps, hourly_caps  # noqa: E402
from core.imagine import refuse_speaker  # noqa: E402
from core.imagine import AssetPlan, ImagineError, plan_asset, run_asset  # noqa: E402

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _approval_off(adult: str = "0"):
    mode, _invalid = _mode_from_store({
        "asset_enabled": "1",
        "asset_adult_enabled": adult,
        "asset_approval_enabled": "0",
        "asset_owner_only": "0",
    })
    return mode


def _approval_on():
    mode, _invalid = _mode_from_store({"asset_enabled": "1", "asset_approval_enabled": "1", "asset_owner_only": "0"})
    return mode


class CapConfigTests(unittest.TestCase):
    def test_caps_live_in_one_config_spot(self):
        self.assertEqual(L.STANDARD_CAPS, L.HourlyCaps(user_images=10, user_videos=5, guild_images=40, guild_videos=15))

    def test_help_text_matches_the_config(self):
        from gui.widgets.settings_help import FIELD_HELP

        c = L.STANDARD_CAPS
        body = FIELD_HELP["asset_approval_enabled"][1]
        self.assertIn(f"{c.user_images} images and {c.user_videos} videos per user", body)
        self.assertIn(f"{c.guild_images} images and {c.guild_videos} videos", body)

    def test_same_caps_with_approval_on_or_off_and_with_18_plus(self):
        for mode in (_approval_off("0"), _approval_off("1"), _approval_on()):
            self.assertTrue(video_allowed(mode))
            self.assertEqual(hourly_caps(mode), (10, 40))
            self.assertEqual(video_caps(mode), (5, 15))

    def test_plan_accepts_new_video_and_video_edit_with_approval_off(self):
        mode = _approval_off()
        self.assertEqual(plan_asset("a red paper boat", kind="video", asset_mode=mode).mode, "video")
        src = SRC.AssetSource(data=MP4, mime="video/mp4", kind="video")
        self.assertEqual(plan_asset("make it night", source=src, asset_mode=mode).mode, "video_edit")


class CapRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="eche-hourly-cap-")
        self.addCleanup(self._tmp.cleanup)
        self.audits = []
        patches = [
            mock.patch("core.secrets._user_root", return_value=self._tmp.name),
            mock.patch("core.asset_safety._audit", side_effect=lambda *a, **k: self.audits.append((a, k))),
            mock.patch("core.asset_safety.asset_enabled", return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.mode = _approval_off()

    def _run(self, mode_name, *, user=1, guild=2, owner=False, outcome="ok", mode=None):
        async def fake(*_a, **_k):
            if outcome == "blocked":
                raise ImagineError("blocked by a check")
            return "file"

        with mock.patch("core.imagine._run_asset_reserved", side_effect=fake):
            return asyncio.run(run_asset(
                AssetPlan(mode=mode_name, prompt="a red paper boat"), "key",
                user_id=user, guild_id=guild, channel_id=3,
                asset_mode=mode or self.mode, is_owner=owner,
            ))

    def _counts(self, bucket, key):
        data = L._load(self._tmp.name)
        return len((data.get(bucket) or {}).get(str(key)) or [])

    def test_five_videos_per_user_and_videos_use_no_image_slot(self):
        for i in range(5):
            self.assertEqual(self._run("video" if i % 2 else "video_edit"), "file")
        with self.assertRaises(ImagineError) as err:
            self._run("video")
        self.assertTrue(err.exception.text.startswith(L.VIDEO_USER_CAP_REFUSAL), err.exception.text)
        self.assertTrue(any(k.get("category") == "video-rate-limit" for _a, k in self.audits))
        self.assertEqual(self._counts("users", 1), 0)
        for _ in range(10):
            self.assertEqual(self._run("image"), "file")
        with self.assertRaises(ImagineError) as err:
            self._run("image_edit")
        self.assertTrue(err.exception.text.startswith(L.USER_CAP_REFUSAL), err.exception.text)

    def test_full_images_do_not_block_videos(self):
        for _ in range(10):
            self._run("image")
        self.assertEqual(self._run("video"), "file")

    def test_same_limits_with_approval_on(self):
        on = _approval_on()
        for _ in range(5):
            self._run("video", mode=on)
        with self.assertRaises(ImagineError) as err:
            self._run("video", mode=on)
        self.assertTrue(err.exception.text.startswith(L.VIDEO_USER_CAP_REFUSAL), err.exception.text)

    def test_server_caps_40_images_15_videos(self):
        for user in range(100, 104):  # 4 users x 10 images
            for _ in range(10):
                self._run("image", user=user)
        with self.assertRaises(ImagineError) as err:
            self._run("image", user=200)
        self.assertTrue(err.exception.text.startswith(L.GUILD_CAP_REFUSAL), err.exception.text)
        for user, n in ((300, 5), (301, 5), (302, 5)):
            for _ in range(n):
                self._run("video", user=user)
        with self.assertRaises(ImagineError) as err:
            self._run("video", user=303)
        self.assertTrue(err.exception.text.startswith(L.VIDEO_GUILD_CAP_REFUSAL), err.exception.text)

    def test_server_caps_do_not_bind_before_two_users_hit_theirs(self):
        for user in (1, 2):
            for _ in range(10):
                self._run("image", user=user)
            for _ in range(5):
                self._run("video", user=user)
        self.assertEqual(self._run("image", user=3), "file")
        self.assertEqual(self._run("video", user=3), "file")

    def test_blocked_tries_refund_their_slot(self):
        with self.assertRaises(ImagineError):
            self._run("video", outcome="blocked")
        with self.assertRaises(ImagineError):
            self._run("image", outcome="blocked")
        for bucket, key in (("video_users", 1), ("video_guilds", 2), ("users", 1), ("guilds", 2)):
            self.assertEqual(self._counts(bucket, key), 0, bucket)

    def test_owner_is_exempt(self):
        for _ in range(12):
            self.assertEqual(self._run("video", owner=True), "file")
            self.assertEqual(self._run("image", owner=True), "file")
        self.assertEqual(self._counts("video_users", 1), 0)
        self.assertEqual(self._counts("users", 1), 0)

    def test_early_gate_refuses_only_when_no_slot_of_either_kind_is_left(self):
        # Approval on and Owner only off, so a non-owner reaches the cap gate.
        self.mode = _approval_on()
        for _ in range(10):
            self._run("image")
        self.assertIsNone(refuse_speaker(False, user_id=1, guild_id=2, channel_id=3, asset_mode=self.mode))
        for _ in range(5):
            self._run("video")
        refused = refuse_speaker(False, user_id=1, guild_id=2, channel_id=3, asset_mode=self.mode)
        self.assertTrue(refused.startswith(L.USER_CAP_REFUSAL), refused)
        self.assertRegex(refused, r"It resets in \d+ min\.$")


if __name__ == "__main__":
    unittest.main()
