# tests/test_asset_live_regressions.py
# Regressions from the 2026-11-13 live session:
#  - every finished image (even a car) was blocked because the text-only
#    checkers judged a fixed instruction string that names minors;
#  - 18+ on refused every request in ordinary channels;
#  - blocked attempts used up the hourly cap.
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import asset_limits as L  # noqa: E402
from core import asset_safety as S  # noqa: E402
from core import classifier as C  # noqa: E402
from core.asset_safety import AssetMode  # noqa: E402
from core.imagine import ImagineError, refuse_speaker, run_asset  # noqa: E402

CAR = b"\xff\xd8\xff\xe0fake-car-jpeg"
ADULT_ON = AssetMode(enabled=True, adult=True, approval=False, owner_only=False, user_cap=3, guild_cap=10, video=False)


class _Channel:
    def __init__(self, nsfw):
        self._nsfw = nsfw

    def is_nsfw(self):
        return self._nsfw


class FinishedImageTests(unittest.TestCase):
    def setUp(self):
        self._prev = C.tests_are_allowing()
        C.allow_in_tests(False)

    def tearDown(self):
        C.allow_in_tests(self._prev)

    def test_media_check_never_runs_text_checkers(self):
        boom = mock.AsyncMock(side_effect=AssertionError("text checker must not run on a finished image"))
        with mock.patch.object(C, "_classify_images_async", mock.AsyncMock(return_value="")) as pixels, \
                mock.patch.object(C, "_child_check_async", boom), \
                mock.patch.object(C, "_child_post_xai", side_effect=AssertionError("no text check")), \
                mock.patch.object(C, "_child_post_ollama", side_effect=AssertionError("no text check")):
            out = asyncio.run(C.classify_media_async([S._image_part(CAR, "image/jpeg")]))
        self.assertEqual(out, "")
        pixels.assert_awaited_once()

    def _finish(self, mode, *, pixels="", age=""):
        with mock.patch("core.classifier.classify_media_async", mock.AsyncMock(return_value=pixels)), \
                mock.patch("core.classifier.classify_async", mock.AsyncMock(return_value="child-check-unsafe")), \
                mock.patch("core.classifier.age_vision_async", mock.AsyncMock(return_value=age)) as av, \
                mock.patch.object(S, "_audit"):
            asyncio.run(S.check_generated_media(CAR, "image/jpeg", footer_hash="x", mode=mode))
        return av

    def test_car_passes_in_sfw_and_in_18_plus(self):
        sfw = S.mode_for_channel(ADULT_ON, nsfw=False)
        av = self._finish(sfw)
        self.assertFalse(av.await_args.kwargs["adult"])
        av = self._finish(S.mode_for_channel(ADULT_ON, nsfw=True))
        self.assertTrue(av.await_args.kwargs["adult"])

    def test_real_blocks_still_block(self):
        with self.assertRaises(ImagineError):
            self._finish(ADULT_ON, pixels="classifier-unsafe")
        with self.assertRaises(ImagineError):
            self._finish(ADULT_ON, age="age-vision-yes")
        with self.assertRaises(ImagineError):
            self._finish(ADULT_ON, age="age-vision-error")

    def test_age_prompts_pass_images_without_people(self):
        self.assertIn("no people, characters, or human-like figures at all", C.AGE_VISION_ADULT_PROMPT)
        self.assertIn("no people, characters, or human-like figures at all", C.AGE_VISION_PROMPT)


class ChannelModeTests(unittest.TestCase):
    def test_18_plus_on_runs_sfw_in_an_ordinary_channel(self):
        with mock.patch("core.imagine.speaker_allowed", return_value=True), \
                mock.patch("core.asset_limits.rate_refusal", return_value=None), \
                mock.patch("core.asset_safety._audit"):
            out = {}
            self.assertIsNone(refuse_speaker(False, guild_id=1, channel=_Channel(False), asset_mode=ADULT_ON, out=out))
            self.assertFalse(out["mode"].adult)
            self.assertFalse(S.adult_output_mode(out["mode"]))
            out = {}
            self.assertIsNone(refuse_speaker(False, guild_id=1, channel=_Channel(True), asset_mode=ADULT_ON, out=out))
            self.assertTrue(out["mode"].adult)

    def test_delivery_check_accepts_the_channel_view(self):
        sfw = S.mode_for_channel(ADULT_ON, nsfw=False)
        with mock.patch.object(S, "read_asset_mode", return_value=ADULT_ON), mock.patch.object(S, "_audit"):
            self.assertEqual(S.confirm_asset_delivery(sfw, nsfw=False), sfw)
            self.assertEqual(S.confirm_asset_delivery(ADULT_ON, nsfw=True), ADULT_ON)
            with self.assertRaises(ImagineError):
                S.confirm_asset_delivery(ADULT_ON, nsfw=False)


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eche-quota-")
        self._root = mock.patch("core.secrets._user_root", return_value=self.tmp)
        self._root.start()
        self._env = mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._root.stop()

    def _count(self, uid=7):
        data = L._load(self.tmp)
        return len(L._prune(data.get("users", {}).get(str(uid), []), L._now()))

    def _attempt(self, exc):
        class Plan:
            prompt = "car"
            mode = "image"
        with mock.patch("core.asset_safety.prepare_generation", mock.AsyncMock(side_effect=exc)), \
                mock.patch("core.asset_safety._audit"):
            with self.assertRaises(type(exc)):
                asyncio.run(run_asset(Plan(), "key", user_id=7, guild_id=9, asset_mode=ADULT_ON))

    def test_blocked_or_failed_attempts_do_not_use_the_cap(self):
        for _ in range(5):
            self._attempt(ImagineError("blocked"))
            self._attempt(RuntimeError("backend down"))
        self.assertEqual(self._count(), 0)
        self.assertIsNone(L.rate_refusal(7, 9, root=self.tmp, caps=(3, 10)))

    def test_release_gives_back_only_one_slot(self):
        L.record_use(7, 9, root=self.tmp)
        L.record_use(7, 9, root=self.tmp)
        L.release_use(7, 9)
        self.assertEqual(self._count(), 1)
        L.release_use(7, 9)
        L.release_use(7, 9)
        self.assertEqual(self._count(), 0)

    def _fill_cap(self, uid=7):
        # Images and videos have separate slots; fill both so the early gate
        # (which refuses only when no slot of either kind is left) refuses.
        for _ in range(3):
            L.record_use(uid, 9, root=self.tmp)
            L.record_use(uid, 9, root=self.tmp, video=True)
        self.assertIsNotNone(L.rate_refusal(uid, 9, root=self.tmp, caps=(3, 10)))

    def test_owner_has_no_hourly_or_daily_cap(self):
        self._fill_cap(uid=7)
        with mock.patch("core.asset_safety._audit"):
            out = {}
            # A non-owner at the cap is refused; the owner is not.
            self.assertIsNotNone(refuse_speaker(False, user_id=7, guild_id=9, nsfw=True, asset_mode=ADULT_ON, out={}))
            self.assertIsNone(refuse_speaker(True, user_id=7, guild_id=9, nsfw=True, asset_mode=ADULT_ON, out=out))
            self.assertTrue(out["owner"])
        done = object()
        with mock.patch("core.imagine._run_asset_reserved", mock.AsyncMock(return_value=done)), \
                mock.patch("core.asset_safety._audit"):
            for _ in range(5):
                got = asyncio.run(run_asset(object(), "key", user_id=7, guild_id=9, asset_mode=ADULT_ON, is_owner=True))
                self.assertIs(got, done)
            # A non-owner at the cap is still refused by run_asset itself.
            with self.assertRaises(ImagineError):
                asyncio.run(run_asset(object(), "key", user_id=7, guild_id=9, asset_mode=ADULT_ON))
        # Owner runs used no slots.
        self.assertEqual(self._count(), 3)

    def test_owner_still_gets_every_safety_check(self):
        class Plan:
            prompt = "car"
            mode = "image"
        blocked = mock.AsyncMock(side_effect=ImagineError("blocked"))
        with mock.patch("core.asset_safety.prepare_generation", blocked), \
                mock.patch("core.asset_safety._audit"):
            with self.assertRaises(ImagineError):
                asyncio.run(run_asset(Plan(), "key", user_id=7, guild_id=9, asset_mode=ADULT_ON, is_owner=True))
        blocked.assert_awaited()
        self.assertEqual(self._count(), 0)


if __name__ == "__main__":
    unittest.main()
