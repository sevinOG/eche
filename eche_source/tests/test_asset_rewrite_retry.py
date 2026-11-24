"""Owner 2026-11-23: output-block retry rewrite, the clearer age refusal, and
the ?asset command audit outcome. Mocks only; no image is generated."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import asset_safety as S  # noqa: E402
from core import audit as A  # noqa: E402
from core import blocklist as B  # noqa: E402
from core.asset_safety import AssetMode  # noqa: E402
from core.classifier import AGE_REASON_LABELS  # noqa: E402
from core.imagine import ImagineError, error_status  # noqa: E402

SFW = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=10, guild_cap=40,
                video=True, video_user_cap=5, video_guild_cap=15)
FRAME = b"\xff\xd8\xff\xe0" + b"\x00" * 16


class AgeRefusal(unittest.TestCase):
    def _block(self, label, stage="output"):
        async def age(*a, **k):
            from core.classifier import _note_signal
            _note_signal("age-label", label)
            return "age-vision-unsure"

        with mock.patch("core.audit.record"), \
                mock.patch("core.classifier.classify_media_async", new=mock.AsyncMock(return_value="")), \
                mock.patch("core.classifier.age_vision_async", new=age):
            with self.assertRaises(ImagineError) as raised:
                if stage == "output":
                    asyncio.run(S.check_generated_media(FRAME, "image/jpeg", footer_hash="f" * 64, mode=SFW))
                else:
                    asyncio.run(S.check_source_media(FRAME, "image/jpeg", footer_hash="f" * 64, mode=SFW))
        return raised.exception

    def test_output_age_block_carries_category_label_and_clear_message(self):
        exc = self._block("uncertain age")
        self.assertEqual(exc.status, "safety_block")
        self.assertEqual(exc.text, S.AGE_CHECK_REFUSAL)
        self.assertEqual(exc.category, "age-vision-unsure")
        self.assertEqual(exc.label, "uncertain age")
        self.assertEqual(error_status(SimpleNamespace(text=S.AGE_CHECK_REFUSAL)), "safety_block")
        self.assertIn("adult", S.AGE_CHECK_REFUSAL)
        # Eche retries itself; the user is never told how to reword it.
        for word in ("Try", "describ", "30", "resubmit", "realistic"):
            self.assertNotIn(word, S.AGE_CHECK_REFUSAL)

    def test_source_age_block_message_is_unchanged(self):
        exc = self._block("uncertain age", stage="source")
        self.assertEqual(exc.text, S.SOURCE_REFUSAL)

    def test_clear_message_never_trips_the_minors_list(self):
        self.assertEqual(B.match_info(S.AGE_CHECK_REFUSAL), ("", ""))


class ForcedAdultAge(unittest.TestCase):
    def test_line_is_added_once_at_the_end(self):
        out = S.force_adult_age("  a woman   in a red coat. ")
        self.assertEqual(out, "a woman in a red coat. " + S.FORCED_ADULT_AGE)
        self.assertEqual(S.force_adult_age(out), out)
        self.assertEqual(S.force_adult_age(""), "")
        self.assertIn("30 years old", S.FORCED_ADULT_AGE)

    def test_line_passes_every_input_list(self):
        from core.asset_filter import school_sexual_cues
        text = S.force_adult_age("a knight in armor")
        self.assertEqual(B.match_info(text), ("", ""))
        self.assertEqual(B.match_info(text, force_context=True), ("", ""))
        self.assertEqual(school_sexual_cues(text), ([], []))


class RewritePrompt(unittest.TestCase):
    def test_every_checker_label_passes_the_text_model_input_checks(self):
        for label in AGE_REASON_LABELS + ("",):
            text = S.build_rewrite_prompt("a woman in a red coat", "age-vision-unsure", label)
            self.assertEqual(B.match_info(text), ("", ""), label)
            self.assertEqual(B.match_info(text, force_context=True), ("", ""), label)

    def test_rewrite_uses_the_summarizer_model_low_temperature(self):
        call = mock.AsyncMock(return_value="a woman in her 30s in a red coat, realistic photo")
        with mock.patch("core.client.call_groq_raw", new=call), \
                mock.patch("core.summarizer_prompt.get_summarizer_model", return_value="sum-model"):
            out = asyncio.run(S.rewrite_for_retry("a woman in a red coat", "age-vision-unsure", "uncertain age"))
        self.assertEqual(out, "a woman in her 30s in a red coat, realistic photo")
        kw = call.await_args.kwargs
        self.assertEqual(kw["model"], "sum-model")
        self.assertLessEqual(kw["temperature"], 0.3)
        self.assertLessEqual(kw["max_completion_tokens"], 300)

    def test_refusal_or_error_reply_fails_closed(self):
        from core.blocklist import REFUSAL
        for reply in (REFUSAL, "ERROR: 500", ""):
            with mock.patch("core.client.call_groq_raw", new=mock.AsyncMock(return_value=reply)):
                self.assertEqual(asyncio.run(S.rewrite_for_retry("a cat", "age-vision-unsure", "")), "")


class CommandAudit(unittest.TestCase):
    def setUp(self):
        A.consume_refusal()
        A.consume_command_error()

    def _logged(self):
        rows = []
        with mock.patch.object(A, "record", side_effect=lambda ev, **kw: rows.append(kw) or kw):
            A.command("asset", "success")
        return rows[-1]["outcome"] if rows else None

    def test_refused_asset_is_not_logged_as_success(self):
        from cogs.asset import _mark_outcome
        _mark_outcome(ImagineError(S.AGE_CHECK_REFUSAL, status="safety_block"))
        self.assertEqual(self._logged(), "refusal")
        _mark_outcome(ImagineError("blocked by xAI", status="provider_filtered"))
        self.assertEqual(self._logged(), "refusal")

    def test_failed_asset_logs_error(self):
        from cogs.asset import _mark_outcome
        _mark_outcome(ImagineError("I couldn't make that.", status="error"))
        self.assertEqual(self._logged(), "error")
        _mark_outcome(None)
        self.assertEqual(self._logged(), "error")

    def test_plain_success_stays_success(self):
        self.assertEqual(self._logged(), "success")


if __name__ == "__main__":
    unittest.main()
