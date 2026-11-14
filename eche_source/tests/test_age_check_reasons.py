# tests/test_age_check_reasons.py
# Every age/child check records checker, verdict and a short reason. No
# image data, prompts or keys go into the log. Verdicts are unchanged.
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import blocklist as bl  # noqa: E402
from core import classifier as C  # noqa: E402

PART = {"data": b"\xff\xd8\xff\xe0" + b"\x00" * 16, "mime": "image/jpeg"}


class AgeReasonTests(unittest.TestCase):
    def setUp(self):
        self._prev = C.tests_are_allowing()
        C.allow_in_tests(False)
        self.rows = []
        self._audit = mock.patch("core.audit.api", side_effect=lambda service, **kw: self.rows.append((service, kw)))
        self._audit.start()

    def tearDown(self):
        self._audit.stop()
        C.allow_in_tests(self._prev)

    def _verdict_rows(self):
        return [kw for service, kw in self.rows if service == "age-check"]

    def _run(self, *, grok=None, grok_exc=None, local=None, local_exc=None, adult=False):
        with mock.patch.object(C, "age_vision_backend", return_value="xai"), \
                mock.patch.object(C, "_age_vision_post", side_effect=grok_exc, return_value=grok), \
                mock.patch.object(C, "_age_vision_post_ollama", side_effect=local_exc, return_value=local), \
                mock.patch.object(C, "_start_age_reason_lookup") as lookup, \
                mock.patch.object(C, "_report_age_vision_problem"), \
                mock.patch.object(C, "_log_vision_fallback"):
            verdict = C._age_vision_sync([PART], adult=adult)
        return verdict, self._verdict_rows()[-1], lookup

    def test_grok_pass_and_yes(self):
        verdict, row, lookup = self._run(grok="NO")
        self.assertEqual((verdict, row["checker"], row["verdict"], row["reason"]), ("", "grok-vision", "pass", "clear"))
        lookup.assert_not_called()
        verdict, row, lookup = self._run(grok="YES", adult=True)
        self.assertEqual((verdict, row["verdict"], row["mode"]), ("age-vision-yes", "yes", "18+"))
        self.assertIn("reason lookup follows", row["reason"])
        lookup.assert_called_once()

    def test_uncertain_is_its_own_reason_and_still_blocks(self):
        verdict, row, _ = self._run(grok="unsure")
        self.assertEqual((verdict, row["verdict"], row["reason"]), ("age-vision-unsure", "unsure", "uncertain"))

    def test_checker_errors_are_named_and_still_block(self):
        verdict, row, _ = self._run(grok_exc=TimeoutError("x"), local_exc=ConnectionError("y"))
        self.assertEqual(verdict, "age-vision-error")
        self.assertEqual((row["checker"], row["verdict"]), ("local-vision", "error"))
        self.assertIn("checker error", row["reason"])
        self.assertIn("ConnectionError", row["reason"])
        self.assertIn("TimeoutError", row["reason"])

    def test_local_backup_records_its_own_verdict(self):
        verdict, row, _ = self._run(grok_exc=TimeoutError("x"), local="NO")
        self.assertEqual((verdict, row["checker"], row["reason"]), ("", "local-vision", "clear"))

    def test_reason_lookup_logs_only_a_fixed_label(self):
        with mock.patch.object(C, "_xai_vision_post", return_value="Cartoon style.\nThe figure is drawn"):
            self.assertEqual(C._age_reason_lookup([PART], adult=True), "cartoon style")
        with mock.patch.object(C, "_xai_vision_post", return_value="something long and free-form"):
            self.assertEqual(C._age_reason_lookup([PART], adult=True), "unparsed")
        with mock.patch.object(C, "_xai_vision_post", side_effect=RuntimeError("down")):
            self.assertEqual(C._age_reason_lookup([PART], adult=False), "lookup failed")
        reasons = [kw["reason"] for service, kw in self.rows if service == "age-check-reason"]
        self.assertEqual(reasons, ["cartoon style", "unparsed", "lookup failed"])
        for _service, kw in self.rows:
            self.assertNotIn("data", kw)
            for value in kw.values():
                self.assertNotIn("base64", str(value))

    def test_label_parser(self):
        self.assertEqual(C.age_reason_label("apparent minor"), "apparent minor")
        self.assertEqual(C.age_reason_label("Uncertain age."), "uncertain age")
        self.assertEqual(C.age_reason_label(""), "unparsed")


class KeywordReasonTests(unittest.TestCase):
    def test_keyword_blocks_record_the_rule_not_the_text(self):
        self.assertEqual(bl.note_reason("llm-input-age"), "keyword: under-age number in text")
        self.assertEqual(bl.note_reason("asset-input-always"), "keyword: child term in text")
        self.assertEqual(bl.note_reason("asset-output-age-vision-unsure"), "vision: uncertain")
        with mock.patch("core.audit.block") as block:
            bl.note("llm-input-age")
        self.assertEqual(block.call_args.kwargs["reason"], "keyword: under-age number in text")

    def test_signed_discord_url_hash_no_longer_trips_the_age_rule(self):
        # The 20:47 false block: hm=...8f... read as "8 female". Discord's
        # machine-made URL parts are no longer scanned (approved 21:22 ET).
        url = "url: https://cdn.discordapp.com/attachments/1/2/asset.jpg?ex=1&is=2&hm=8f3e2a1b"
        self.assertEqual(bl.match_info(url), ("", ""))
        self.assertEqual(bl.match_info("an 8 f")[1], "age")

if __name__ == "__main__":
    unittest.main()
