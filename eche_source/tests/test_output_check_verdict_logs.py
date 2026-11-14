# tests/test_output_check_verdict_logs.py
# Live 2026-11-14 08:42 ET: on a 49-frame video, the per-frame image content
# check only wrote "api image-check outcome=success" (the HTTP call), never
# its verdict, while the age check wrote pass/block. Now every image-check
# decision writes a verdict row (checker, model, verdict, frame N/total,
# stage), as a safety record; a pass whose row cannot be written blocks, like
# the child check. Age-check rows also carry the model and frame.
# Mocks only: no media is generated, no network, placeholder bytes only.
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import asset_safety as S  # noqa: E402
from core import classifier as C  # noqa: E402
from core.asset_safety import AssetMode  # noqa: E402
from core.imagine import ImagineError  # noqa: E402

IMG = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUFBQQ=="}}]
FRAME = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # placeholder bytes, not a picture
FOOTER = "f" * 64
SFW = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=10, guild_cap=40,
                video=True, video_user_cap=5, video_guild_cap=15)
GROK = "grok-test-vision"
LOCAL = "qwen3-vl:4b-instruct"


class _Base(unittest.TestCase):
    def setUp(self):
        self.prev = C.tests_are_allowing()
        C.allow_in_tests(False)
        self.addCleanup(C.allow_in_tests, self.prev)
        self.audits: list[tuple[str, dict]] = []
        spec = C.CheckerSpec("image_check", provider="ollama", model=LOCAL)
        patches = [
            mock.patch.object(C, "_resolve_checker", return_value=spec),
            mock.patch.object(C, "_has_backend", return_value=True),
            mock.patch.object(C, "_xai_key", return_value="k"),
            mock.patch.object(C, "_xai_model_cache", (1.0, GROK)),
            mock.patch.object(C, "_xai_vision_models_fetch", side_effect=AssertionError("no network")),
            mock.patch.object(C, "_image_check_yesno_messages", return_value=([{"role": "user"}], "")),
            mock.patch.object(C, "_log_check_block", lambda *a, **k: None),
            mock.patch.object(C, "_log_vision_fallback", lambda *a, **k: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _api(self, service, **fields):
        self.audits.append((service, fields))
        return {}

    def rows(self, service):
        return [f for s, f in self.audits if s == service]


class ImageCheckVerdictRowTests(_Base):
    def test_grok_pass_writes_a_safety_verdict_row(self):
        with mock.patch.object(C, "_xai_vision_post", return_value="NO"), \
                mock.patch.object(C, "_post_with_images", side_effect=AssertionError("no fallback")), \
                mock.patch("core.audit.api", side_effect=self._api):
            tok = C.CHECK_FRAME.set("3/49")
            try:
                self.assertEqual(C._classify_images_sync("", IMG), "")
            finally:
                C.CHECK_FRAME.reset(tok)
        row = self.rows("image-check")[-1]
        self.assertEqual((row["outcome"], row["verdict"], row["checker"], row["model"], row["frame"]),
                         ("pass", "no", "grok-vision", GROK, "3/49"))
        self.assertTrue(row["safety"])
        self.assertNotIn("QUFBQQ", repr(self.audits))  # never the image data

    def test_grok_yes_writes_a_block_row(self):
        with mock.patch.object(C, "_xai_vision_post", return_value="YES"), \
                mock.patch("core.audit.api", side_effect=self._api):
            self.assertEqual(C._classify_images_sync("", IMG), "classifier-unsafe")
        row = self.rows("image-check")[-1]
        self.assertEqual((row["outcome"], row["verdict"], row["category"]), ("block", "yes", "classifier-unsafe"))

    def test_local_backup_pass_names_the_local_model(self):
        with mock.patch.object(C, "_xai_vision_post", side_effect=TimeoutError()), \
                mock.patch.object(C, "_post_with_images", return_value="NO"), \
                mock.patch("core.audit.api", side_effect=self._api):
            self.assertEqual(C._classify_images_sync("", IMG), "")
        row = self.rows("image-check")[-1]
        self.assertEqual((row["outcome"], row["checker"], row["model"]), ("pass", "local-vision", LOCAL))

    def test_local_error_is_an_error_row(self):
        with mock.patch.object(C, "_xai_vision_post", side_effect=TimeoutError()), \
                mock.patch.object(C, "_post_with_images", side_effect=OSError("down")), \
                mock.patch("core.audit.api", side_effect=self._api):
            self.assertEqual(C._classify_images_sync("", IMG), "classifier-error")
        row = self.rows("image-check")[-1]
        self.assertEqual((row["outcome"], row["verdict"]), ("block", "error"))
        self.assertIn("grok TimeoutError", row["reason"])

    def test_unlogged_pass_blocks(self):
        def _fail(service, **fields):
            if service == "image-check" and fields.get("outcome") == "pass":
                raise OSError("disk full")
            return {}

        with mock.patch.object(C, "_xai_vision_post", return_value="NO"), \
                mock.patch("core.gui_bridge.log", lambda *a, **k: None), \
                mock.patch("core.audit.api", side_effect=_fail):
            self.assertEqual(C._classify_images_sync("", IMG), "classifier-error")

    def test_pass_row_is_a_safety_record_so_a_failed_write_raises(self):
        # audit.record only raises on a failed write for safety records.
        with mock.patch("core.audit.record", side_effect=OSError("disk full")) as rec:
            self.assertFalse(C._log_image_verdict("grok-vision", GROK, "", adult=False))
        self.assertTrue(rec.call_args.kwargs.get("safety"))


class FrameRowsThroughOutputCheckTests(_Base):
    def test_every_frame_gets_image_and_age_rows_with_frame_and_model(self):
        ages = iter(["NO\nadult", "NO\nadult", "YES\napparent minor"])
        with mock.patch.object(S, "extract_video_frames", return_value=[FRAME, FRAME, FRAME]), \
                mock.patch.object(S, "_image_part", return_value=IMG[0]), \
                mock.patch.object(C, "_xai_vision_post", return_value="NO"), \
                mock.patch.object(C, "age_vision_backend", return_value="xai"), \
                mock.patch.object(C, "_age_vision_post", side_effect=lambda *a, **k: next(ages)), \
                mock.patch.object(C, "_age_vision_post_ollama", side_effect=AssertionError("no fallback")), \
                mock.patch("core.audit.api", side_effect=self._api), \
                mock.patch("core.audit.record", return_value={}):
            with self.assertRaises(ImagineError):
                asyncio.run(S.check_generated_media(b"mp4", "video/mp4", footer_hash=FOOTER, mode=SFW))
        image_rows = self.rows("image-check")
        self.assertEqual([(r["frame"], r["outcome"], r["stage"]) for r in image_rows],
                         [("1/3", "pass", "output"), ("2/3", "pass", "output"), ("3/3", "pass", "output")])
        age_rows = self.rows("age-check")
        self.assertEqual([(r["frame"], r["outcome"], r["model"]) for r in age_rows],
                         [("1/3", "pass", GROK), ("2/3", "pass", GROK), ("3/3", "block", GROK)])
        self.assertEqual(C.CHECK_FRAME.get(), "")  # reset after the check

    def test_single_image_is_frame_1_of_1(self):
        with mock.patch.object(S, "_image_part", return_value=IMG[0]), \
                mock.patch.object(C, "_xai_vision_post", return_value="NO"), \
                mock.patch.object(C, "age_vision_backend", return_value="xai"), \
                mock.patch.object(C, "_age_vision_post", return_value="NO\nadult"), \
                mock.patch("core.audit.api", side_effect=self._api), \
                mock.patch("core.audit.record", return_value={}):
            asyncio.run(S.check_generated_media(FRAME, "image/jpeg", footer_hash=FOOTER, mode=SFW))
        self.assertEqual([r["frame"] for r in self.rows("image-check")], ["1/1"])
        self.assertEqual([r["frame"] for r in self.rows("age-check")], ["1/1"])


if __name__ == "__main__":
    unittest.main()
