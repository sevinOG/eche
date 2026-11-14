# Video with owner approval off (owner request 2026-11-13): allowed, with an
# extra hourly video cap (1 per user, 3 per server) on top of the shared
# approval-off caps (3/10). The owner is exempt. Blocked tries refund both.
# No live calls, no media generated; the pipeline after the cap is mocked.

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


class VideoModeTests(unittest.TestCase):
    def test_video_allowed_with_approval_off_with_and_without_18_plus(self):
        for adult in ("0", "1"):
            mode = _approval_off(adult)
            self.assertTrue(video_allowed(mode))
            self.assertEqual(video_caps(mode), (1, 3))
            self.assertEqual(hourly_caps(mode), (3, 10))

    def test_approval_on_has_no_extra_video_cap(self):
        mode, _ = _mode_from_store({"asset_enabled": "1", "asset_approval_enabled": "1"})
        self.assertTrue(video_allowed(mode))
        self.assertIsNone(video_caps(mode))

    def test_plan_accepts_new_video_and_video_edit_with_approval_off(self):
        mode = _approval_off()
        self.assertEqual(plan_asset("a red paper boat", kind="video", asset_mode=mode).mode, "video")
        src = SRC.AssetSource(data=MP4, mime="video/mp4", kind="video")
        self.assertEqual(plan_asset("make it night", source=src, asset_mode=mode).mode, "video_edit")


class VideoCapRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="eche-video-cap-")
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

    def _run(self, mode_name, *, user=1, guild=2, owner=False, outcome="ok"):
        async def fake(*_a, **_k):
            if outcome == "blocked":
                raise ImagineError("blocked by a check")
            return "file"

        with mock.patch("core.imagine._run_asset_reserved", side_effect=fake):
            return asyncio.run(run_asset(
                AssetPlan(mode=mode_name, prompt="a red paper boat"), "key",
                user_id=user, guild_id=guild, channel_id=3,
                asset_mode=self.mode, is_owner=owner,
            ))

    def _counts(self, bucket, key):
        data = L._load(self._tmp.name)
        return len((data.get(bucket) or {}).get(str(key)) or [])

    def test_one_video_per_user_per_hour(self):
        self.assertEqual(self._run("video"), "file")
        with self.assertRaises(ImagineError) as err:
            self._run("video_edit")
        self.assertEqual(err.exception.text, L.VIDEO_USER_CAP_REFUSAL)
        self.assertTrue(any(k.get("category") == "video-rate-limit" for _a, k in self.audits))
        # Images still have shared room left (video used 1 of 3).
        self.assertEqual(self._run("image"), "file")
        self.assertEqual(self._run("image"), "file")
        with self.assertRaises(ImagineError) as err:
            self._run("image")
        self.assertEqual(err.exception.text, L.USER_CAP_REFUSAL)

    def test_three_videos_per_server_per_hour(self):
        for user in (1, 2, 3):
            self.assertEqual(self._run("video", user=user), "file")
        with self.assertRaises(ImagineError) as err:
            self._run("video", user=4)
        self.assertEqual(err.exception.text, L.VIDEO_GUILD_CAP_REFUSAL)

    def test_video_needs_shared_room_too(self):
        for _ in range(3):
            self._run("image")
        with self.assertRaises(ImagineError) as err:
            self._run("video")
        self.assertEqual(err.exception.text, L.USER_CAP_REFUSAL)

    def test_blocked_video_refunds_both_slots(self):
        with self.assertRaises(ImagineError):
            self._run("video", outcome="blocked")
        self.assertEqual(self._counts("video_users", 1), 0)
        self.assertEqual(self._counts("video_guilds", 2), 0)
        self.assertEqual(self._run("video"), "file")

    def test_owner_is_exempt(self):
        for _ in range(5):
            self.assertEqual(self._run("video", owner=True), "file")
        self.assertEqual(self._run("video"), "file")


if __name__ == "__main__":
    unittest.main()
