# tests/test_safety_port.py
# Owner 2026-11-21: port selected Waerhouse safety pieces to Eche.
# Mocks only. No image is generated; frame and file bytes are placeholders.
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import asset_safety as S
from core import audit as A
from core.asset_filter import stylized_sexual_blocked
from core.asset_safety import AssetMode
from core.imagine import ImagineError, request_for, plan_asset
from core.safety import SAFETY_FOOTER

SFW = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=10, guild_cap=40,
                video=True, video_user_cap=5, video_guild_cap=15)


def _prep(prompt: str, **kwargs):
    with mock.patch("core.audit.record"), \
            mock.patch("core.classifier.asset_prompt_check_async", new=mock.AsyncMock(return_value="")):
        return asyncio.run(S.prepare_generation(prompt, mode=SFW, **kwargs))


class DiffusionPromptTests(unittest.TestCase):
    def test_imagine_body_is_user_prompt_plus_25_line_without_footer(self):
        for prompt in ("a red paper boat", "a goth woman on a bike"):
            body = S.build_diffusion_prompt(prompt)
            self.assertIn(prompt, body)
            self.assertIn("aged 25 or older", body)
            self.assertIn("mature adult facial features and adult proportions", body)
            self.assertNotIn(SAFETY_FOOTER, body)
            self.assertNotIn("Safety rules (mandatory", body)
            sent, digest = S.build_sent_prompt(prompt)
            self.assertTrue(S.footer_is_last(sent))
            self.assertTrue(digest)

    def test_request_for_sends_diffusion_not_footer(self):
        prepared = _prep("a red paper boat")
        path, body = request_for(plan_asset("a red paper boat"), prepared)
        self.assertEqual(path, "/images/generations")
        self.assertEqual(body["prompt"], prepared.diffusion_prompt)
        self.assertNotEqual(body["prompt"], prepared.sent_prompt)
        self.assertNotIn(SAFETY_FOOTER, body["prompt"])
        self.assertTrue(S.footer_is_last(prepared.sent_prompt))

    def test_retry_cues_apply_to_diffusion_body(self):
        prepared = _prep("a painterly woman")
        stronger = S.strengthen_for_retry(prepared)
        self.assertIn(S.RETRY_ADULT_LINE, stronger.diffusion_prompt)
        self.assertNotIn(SAFETY_FOOTER, stronger.diffusion_prompt)
        self.assertTrue(S.footer_is_last(stronger.sent_prompt))
        self.assertEqual(stronger.footer_hash, prepared.footer_hash)


class StylizedSexualTests(unittest.TestCase):
    def test_sfw_anime_is_allowed_sexual_stylized_is_blocked(self):
        allowed = (
            "an anime landscape at dusk",
            "a cartoon cat sitting on a fence",
            "an anime woman reading on a train",
            "a sexy vampire in a castle",
            "a woman in lingerie on a couch",
        )
        blocked = (
            "hentai poster",
            "ecchi anime scene",
            "a sexy anime woman",
            "nsfw waifu",
            "a cartoon character in lingerie",
        )
        for text in allowed:
            hit, why = stylized_sexual_blocked(text)
            self.assertFalse(hit, (text, why))
        for text in blocked:
            hit, why = stylized_sexual_blocked(text)
            self.assertTrue(hit, text)
            self.assertTrue(why)

    def test_prepare_generation_blocks_sexual_anime_not_sfw(self):
        with mock.patch("core.audit.record"), \
                mock.patch("core.classifier.asset_prompt_check_async", new=mock.AsyncMock(return_value="")):
            asyncio.run(S.prepare_generation("an anime landscape at dusk", mode=SFW))
        with mock.patch("core.audit.record") as rec, \
                mock.patch("core.classifier.asset_prompt_check_async", new=mock.AsyncMock(return_value="")):
            with self.assertRaises(ImagineError):
                asyncio.run(S.prepare_generation("a sexy anime woman", mode=SFW))
        rows = [call.kwargs for call in rec.call_args_list if call.args and call.args[0] == "asset"]
        row = [kw for kw in rows if kw.get("category") == "stylized-sexual"][-1]
        self.assertEqual(row["rule"], "stylized-sexual")
        self.assertNotIn("prompt", row)
        self.assertTrue(row["safety"])


class AuditPrivacyAndFailClosedTests(unittest.TestCase):
    def test_record_drops_prompt_and_snippet(self):
        with tempfile.TemporaryDirectory(prefix="eche-audit-priv-") as raw:
            with mock.patch.dict(os.environ, {"ECHE_USER_ROOT": raw}, clear=False), \
                    mock.patch("core.gui_bridge.log"):
                rec = A.record(
                    "asset",
                    outcome="refusal",
                    category="matcher",
                    term="teen",
                    rule="always",
                    prompt="a cheerful teen holding a flag",
                    snippet="...teen holding...",
                    safety=True,
                )
                A.flush()
            self.assertNotIn("prompt", rec)
            self.assertNotIn("snippet", rec)
            self.assertEqual((rec.get("term"), rec.get("rule")), ("teen", "always"))
            path = Path(raw) / "logs" / "audit.jsonl"
            blob = path.read_text(encoding="utf-8") if path.is_file() else ""
            self.assertNotIn("cheerful teen", blob)
            self.assertNotIn("snippet", blob)
            self.assertIn('"term":"teen"', blob)

    def test_prepare_pass_row_is_safety_and_fail_closed(self):
        with mock.patch.object(A, "record", side_effect=OSError("disk full")), \
                mock.patch("core.classifier.asset_prompt_check_async", new=mock.AsyncMock(return_value="")):
            with self.assertRaises(ImagineError) as raised:
                asyncio.run(S.prepare_generation("a red paper boat", mode=SFW))
        self.assertEqual(raised.exception.status, "error")
        self.assertIn("safety log", raised.exception.text.lower())

    def test_prepare_pass_row_is_marked_safety(self):
        rows = []
        with mock.patch.object(A, "record", side_effect=lambda ev, **kw: rows.append((ev, kw)) or {}), \
                mock.patch("core.classifier.asset_prompt_check_async", new=mock.AsyncMock(return_value="")):
            prepared = asyncio.run(S.prepare_generation("a red paper boat", mode=SFW))
        self.assertTrue(prepared.diffusion_prompt)
        ev, kw = [r for r in rows if r[0] == "asset" and r[1].get("outcome") == "success"][-1]
        self.assertTrue(kw["safety"])
        self.assertEqual(kw["category"], "pass")
        self.assertTrue(kw.get("footer_hash"))
        self.assertNotIn("prompt", kw)


class BurnTests(unittest.TestCase):
    def test_secure_delete_overwrites_and_removes(self):
        from core import burn

        with tempfile.TemporaryDirectory(prefix="eche-burn-") as raw:
            path = Path(raw) / "clip.bin"
            path.write_bytes(b"rejected-bytes-XXXX")
            self.assertTrue(burn.secure_delete(path, queue=False))
            self.assertFalse(path.exists())

    def test_stuck_path_is_queued_and_retried(self):
        from core import burn

        with tempfile.TemporaryDirectory(prefix="eche-burnq-") as raw:
            stuck = Path(raw) / "stuck.bin"
            stuck.write_bytes(b"still-here")
            pending = Path(raw) / "logs"
            pending.mkdir()
            with mock.patch.object(burn, "_pending_file", return_value=pending / "burn_pending.json"):
                # Make overwrite+unlink fail once so the path is queued.
                with mock.patch.object(burn, "_burn_once", side_effect=OSError("busy")):
                    self.assertFalse(burn.secure_delete(stuck, retries=1, delay=0))
                self.assertTrue(stuck.exists())
                self.assertIn(str(stuck), burn.pending())
                self.assertEqual(burn.retry_pending(), 0)
                self.assertFalse(stuck.exists())
                self.assertEqual(burn.pending(), [])


if __name__ == "__main__":
    unittest.main()
