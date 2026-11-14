# tests/test_pass_row_fail_closed.py
# Approved 2026-11-14 09:22 ET: the prompt child check's pass row was an
# ordinary audit record, and audit.record only raises on a failed write for
# safety records, so the child check's "unlogged pass blocks" never fired.
# The age check also allowed when its pass row could not be written. Both
# pass rows are now safety records; a failed write blocks. Mocks only: the
# audit file writer is replaced, no network, no media.
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import asset_safety as S  # noqa: E402
from core import audit as A  # noqa: E402
from core import classifier as C  # noqa: E402
from core.asset_safety import AssetMode  # noqa: E402
from core.imagine import ImagineError  # noqa: E402

IMG = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUFBQQ=="}}]
FRAME = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # placeholder bytes, not a picture
SFW = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=10, guild_cap=40,
                video=True, video_user_cap=5, video_guild_cap=15)


class _Writer:
    """Stands in for the audit file writer. fail(rec) decides which lines fail."""

    def __init__(self, fail=lambda rec: False):
        self.lines: list[dict] = []
        self.fail = fail

    def __call__(self, path, line, *, safety, rotate=True, retries=3):
        rec = json.loads(line)
        if self.fail(rec):
            raise OSError("disk full")
        self.lines.append(rec)

    def rows(self, service):
        return [r for r in self.lines if r.get("event") == "api" and r.get("service") == service]


def _pass_row(service):
    return lambda rec: rec.get("service") == service and rec.get("outcome") == "pass"


class _Base(unittest.TestCase):
    def setUp(self):
        prev = C.tests_are_allowing()
        C.allow_in_tests(False)
        self.addCleanup(C.allow_in_tests, prev)
        for p in (
            mock.patch.object(A, "_alert_write_failure", lambda *a, **k: None),
            mock.patch("core.gui_bridge.log", lambda *a, **k: None),
            mock.patch.object(C, "_log_vision_fallback", lambda *a, **k: None),
            mock.patch.object(C, "_xai_vision_models_fetch", side_effect=AssertionError("no network")),
        ):
            p.start()
            self.addCleanup(p.stop)

    def write_with(self, writer):
        return mock.patch.object(A, "_append_line", writer)


class ChildCheckPassRowTests(_Base):
    def _run(self, reply, writer):
        with self.write_with(writer), \
                mock.patch.object(C, "child_check_backend", return_value="xai"), \
                mock.patch.object(C, "_child_post_xai", return_value=reply), \
                mock.patch.object(C, "_child_post_ollama", side_effect=AssertionError("no fallback")):
            return C._child_check_one("a red car", "")

    def test_normal_pass_still_passes_and_is_a_safety_row(self):
        w = _Writer()
        self.assertEqual(self._run("NO", w), "")
        row = w.rows("child-check")[-1]
        self.assertEqual((row["outcome"], row["verdict"]), ("pass", "no"))
        self.assertTrue(row["safety"])

    def test_failed_pass_write_blocks(self):
        self.assertEqual(self._run("NO", _Writer(_pass_row("child-check"))), "child-check-error")

    def test_block_rows_are_still_written(self):
        w = _Writer()
        self.assertEqual(self._run("YES", w), "child-check-unsafe")
        self.assertEqual(w.rows("child-check")[-1]["outcome"], "block")


class AgeCheckPassRowTests(_Base):
    def _patches(self, writer, reply="NO\nadult"):
        return [
            self.write_with(writer),
            mock.patch.object(C, "age_vision_backend", return_value="xai"),
            mock.patch.object(C, "_age_vision_post", return_value=reply),
            mock.patch.object(C, "_age_vision_post_ollama", return_value=reply),
        ]

    def _sync(self, writer, reply="NO\nadult"):
        ps = self._patches(writer, reply)
        for p in ps:
            p.start()
        try:
            return C._age_vision_sync(IMG)
        finally:
            for p in reversed(ps):
                p.stop()

    def test_normal_pass_still_passes_and_is_a_safety_row(self):
        w = _Writer()
        self.assertEqual(self._sync(w), "")
        row = w.rows("age-check")[-1]
        self.assertEqual((row["outcome"], row["reason"]), ("pass", "adult"))
        self.assertTrue(row["safety"])

    def test_failed_pass_write_blocks_on_grok(self):
        self.assertEqual(self._sync(_Writer(_pass_row("age-check"))), "age-vision-error")

    def test_failed_pass_write_blocks_on_local_backup(self):
        w = _Writer(_pass_row("age-check"))
        with mock.patch.object(C, "age_vision_backend", return_value="ollama"), \
                mock.patch.object(C, "_age_vision_post", side_effect=AssertionError("grok skipped")), \
                mock.patch.object(C, "_age_vision_post_ollama", return_value="NO\nadult"), \
                self.write_with(w):
            self.assertEqual(C._age_vision_sync(IMG), "age-vision-error")

    def test_block_rows_are_still_written(self):
        w = _Writer()
        self.assertEqual(self._sync(w, "YES\napparent minor"), "age-vision-yes")
        row = w.rows("age-check")[-1]
        self.assertEqual((row["outcome"], row["reason"]), ("block", "apparent minor"))

    def test_async_path_used_by_chat_reply_images_blocks_too(self):
        # Chat reply images, sources, and outputs all call age_vision_async.
        ps = self._patches(_Writer(_pass_row("age-check")))
        for p in ps:
            p.start()
        try:
            self.assertEqual(asyncio.run(C.age_vision_async(IMG)), "age-vision-error")
        finally:
            for p in reversed(ps):
                p.stop()

    def test_every_output_frame_fails_closed_when_its_age_pass_is_not_logged(self):
        spec = C.CheckerSpec("image_check", provider="ollama", model="qwen3-vl:4b-instruct")
        w = _Writer(lambda rec: rec.get("service") == "age-check" and rec.get("outcome") == "pass"
                    and rec.get("frame") == "2/3")
        with self.write_with(w), \
                mock.patch.object(S, "extract_video_frames", return_value=[FRAME, FRAME, FRAME]), \
                mock.patch.object(S, "_image_part", return_value=IMG[0]), \
                mock.patch.object(C, "_resolve_checker", return_value=spec), \
                mock.patch.object(C, "_has_backend", return_value=True), \
                mock.patch.object(C, "_xai_key", return_value="k"), \
                mock.patch.object(C, "_xai_vision_post", return_value="NO"), \
                mock.patch.object(C, "_log_check_block", lambda *a, **k: None), \
                mock.patch.object(C, "age_vision_backend", return_value="xai"), \
                mock.patch.object(C, "_age_vision_post", return_value="NO\nadult"):
            with self.assertRaises(ImagineError):
                asyncio.run(S.check_generated_media(b"mp4", "video/mp4", footer_hash="f" * 64, mode=SFW))
        frames = [r["frame"] for r in w.rows("age-check") if r["outcome"] == "pass"]
        self.assertEqual(frames, ["1/3"])  # frame 2 was not logged, so it blocked; 3 never ran


if __name__ == "__main__":
    unittest.main()
