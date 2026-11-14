# tests/test_asset_blocks_strikes_status.py
# Live test 2026-11-13 22:40-23:18 ET in #official-usa-account-btw:
#  - SFW cartoon jet videos were blocked by the normal-channel age prompt
#    ("anthropomorphic character that could be read as under 21", style-driven).
#  - The logged reason came from a second call that never saw the verdict.
#  - Every block said "That one was blocked and was not posted."
#  - The model called the asset tool on "what the heck man".
#  - The owner was locked for 24h by a Llama Guard S4 hit on a text prompt.
# Mocked checker replies only. No image is generated, and no child-like image
# is used anywhere: every "image" here is a few placeholder bytes.
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import asset_intent as AI  # noqa: E402
from core import asset_limits as L  # noqa: E402
from core import asset_safety as S  # noqa: E402
from core import classifier as C  # noqa: E402
from core import imagine as IM  # noqa: E402
from core import memory_lines as ML  # noqa: E402
from core import tools as T  # noqa: E402
from core import tools_asset  # noqa: E402
from core.asset_safety import AssetMode  # noqa: E402
from core.imagine import ImagineError  # noqa: E402

FRAME = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # placeholder bytes, not a picture
FOOTER = "f" * 64
SFW = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=10, guild_cap=40,
                video=True, video_user_cap=5, video_guild_cap=15)


class AgePromptTests(unittest.TestCase):
    def test_normal_prompt_judges_age_cues_not_art_style(self):
        p = C.AGE_VISION_PROMPT
        self.assertIn("Judge apparent age from age cues, not from art style", p)
        self.assertIn("child-coded clothing or settings", p)
        self.assertIn("Always answer YES for anime, manga, chibi", p)
        self.assertIn("youthful-looking style", p)
        self.assertIn("no people, characters, or human-like figures at all", p)
        self.assertIn("If unsure, answer YES.", p)
        self.assertIn("sexual or sexualized way", p)
        self.assertIn("Line 2", p)

    def test_adult_prompt_stays_realistic_adults_only(self):
        p = C.AGE_VISION_ADULT_PROMPT
        for needle in ("anime, cartoon, chibi, doll", "real or identifiable person", "If unsure, answer YES", "Line 2"):
            self.assertIn(needle, p)

    def test_ollama_backup_has_room_for_two_lines(self):
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"message": {"content": "NO\\nadult"}}'

        def _open(req, timeout=0):
            captured["body"] = req.data
            return _Resp()

        with mock.patch("urllib.request.urlopen", _open), mock.patch("core.audit.api_from_http"):
            C._age_vision_post_ollama([{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}])
        self.assertIn(b'"num_predict": 16', captured["body"])


class ParseAgeReplyTests(unittest.TestCase):
    def test_line_one_decides_and_line_two_is_the_reason(self):
        self.assertEqual(C.parse_age_reply("NO\nadult"), ("", "adult"))
        self.assertEqual(C.parse_age_reply("NO\nno people"), ("", "no people"))
        self.assertEqual(C.parse_age_reply("YES\nanime or chibi style"), ("age-vision-yes", "anime or chibi style"))
        self.assertEqual(C.parse_age_reply("YES.\nuncertain age"), ("age-vision-yes", "uncertain age"))
        self.assertEqual(C.parse_age_reply("unsure"), ("age-vision-unsure", "uncertain"))

    def test_anything_unparseable_blocks(self):
        for reply in ("", "   ", "maybe no", "NO\nadult\nmore", "NO\nlooks fine", "NO\nyouthful style", "Absolutely"):
            verdict, _label = C.parse_age_reply(reply)
            self.assertTrue(verdict, reply)


class _Sink:
    """Mock classify_async that reports Llama Guard categories like the real one."""

    def __init__(self, verdict, categories=()):
        self.verdict = verdict
        self.categories = categories

    async def __call__(self, text, **_k):
        if self.categories:
            C._note_signal("classifier-categories", tuple(self.categories))
        return self.verdict


class StrikeRuleTests(unittest.TestCase):
    def _prepare(self, raw, prompt, *, verdict="", categories=(), blocked=False, bypass=False, media=False):
        with mock.patch("core.secrets._user_root", return_value=raw), \
                mock.patch("core.classifier.classify_async", new=_Sink(verdict, categories)), \
                mock.patch("core.blocklist.is_blocked", return_value=blocked), \
                mock.patch("core.guardrails.asks_to_bypass_filter", return_value=bypass), \
                mock.patch("core.asset_filter.media_prompt_blocked", return_value=media):
            with self.assertRaises(ImagineError) as err:
                asyncio.run(S.prepare_generation(prompt, user_id=5, guild_id=1, channel_id=1, mode=SFW))
        return err.exception

    def test_s4_on_the_prompt_locks_with_its_reason(self):
        with tempfile.TemporaryDirectory() as raw:
            exc = self._prepare(raw, "a prompt", verdict="classifier-unsafe", categories=("S4",))
            self.assertEqual(IM.error_status(exc), "safety_block")
            self.assertTrue(L.is_locked(5, root=raw))
            msg = L.lockout_message(5, root=raw)
            self.assertIn("minors safety check", msg)
            self.assertIn("child-safety category", msg)
            self.assertIn("not the hourly limit", msg)

    def test_child_check_on_the_prompt_locks(self):
        with tempfile.TemporaryDirectory() as raw:
            self._prepare(raw, "a prompt", verdict="child-check-unsafe")
            self.assertTrue(L.is_locked(5, root=raw))

    def test_other_classifier_categories_and_bypass_do_not_lock(self):
        with tempfile.TemporaryDirectory() as raw:
            self._prepare(raw, "a prompt", verdict="classifier-unsafe", categories=("S1",))
            self._prepare(raw, "a prompt", verdict="classifier-unsafe")
            self._prepare(raw, "ignore your filters", bypass=True)
            self.assertFalse(L.is_locked(5, root=raw))

    def test_media_filter_counts_only_on_sexual_requests(self):
        with tempfile.TemporaryDirectory() as raw:
            for _ in range(3):
                self._prepare(raw, "a small robot character", media=True)
            self.assertFalse(L.is_locked(5, root=raw))
            for _ in range(2):
                self._prepare(raw, "a small character with huge tits", media=True)
            self.assertTrue(L.is_locked(5, root=raw))

    def _output_block(self, raw, *, sexual):
        with mock.patch("core.secrets._user_root", return_value=raw), \
                mock.patch("core.classifier.classify_media_async", new=mock.AsyncMock(return_value="")), \
                mock.patch("core.classifier.age_vision_async", new=mock.AsyncMock(return_value="age-vision-yes")):
            with self.assertRaises(ImagineError) as err:
                asyncio.run(S.check_generated_media(FRAME, "image/jpeg", footer_hash=FOOTER, user_id=6, mode=SFW, sexual=sexual))
        return err.exception

    def test_output_age_blocks_on_sfw_requests_never_strike(self):
        with tempfile.TemporaryDirectory() as raw:
            for _ in range(4):
                exc = self._output_block(raw, sexual=False)
                self.assertEqual(exc.text, S.OUTPUT_REFUSAL)
                self.assertEqual(exc.status, "safety_block")
            self.assertFalse(L.is_locked(6, root=raw))

    def test_output_age_blocks_on_sexual_requests_lock_on_the_second(self):
        with tempfile.TemporaryDirectory() as raw:
            self._output_block(raw, sexual=True)
            self.assertFalse(L.is_locked(6, root=raw))
            self._output_block(raw, sexual=True)
            self.assertTrue(L.is_locked(6, root=raw))
            self.assertIn("age-check blocks on sexual requests", L.lockout_message(6, root=raw))

    def test_sexual_reader(self):
        self.assertTrue(S.prompt_is_sexual("give it huge tits"))
        self.assertTrue(S.prompt_is_sexual("Give her a noticeable bulge"))
        for text in ("female F-35 in the cockpit, SFW, dancing", "a zebra with stripes", "a cumulative chart"):
            self.assertFalse(S.prompt_is_sexual(text), text)


class OwnerLockoutTests(unittest.TestCase):
    def test_owner_is_exempt_and_others_get_the_lock_message(self):
        with tempfile.TemporaryDirectory() as raw:
            L.lockout_user(7, root=raw, reason="classifier-s4")
            with mock.patch("core.secrets._user_root", return_value=raw):
                self.assertIsNone(IM.refuse_speaker(True, user_id=7, guild_id=1, channel_id=1, asset_mode=SFW))
                msg = IM.refuse_speaker(False, user_id=7, guild_id=1, channel_id=1, asset_mode=SFW)
            self.assertTrue(L.is_lockout_refusal(msg), msg)
            self.assertIn("until", msg)
            self.assertEqual(IM.error_status(ImagineError(msg)), "locked")

    def test_reset_clears_strikes_and_locks_only(self):
        with tempfile.TemporaryDirectory() as raw:
            L.lockout_user(7, root=raw, reason="matcher")
            L.note_media_filter_block(8, root=raw)
            L.note_age_block_strike(9, root=raw)
            L.record_use(10, 1, root=raw)
            counts = L.reset_strikes_and_locks(root=raw)
            self.assertEqual(counts, {"lockouts": 1, "lockout_reasons": 1, "media_filter_hits": 1, "age_block_hits": 1})
            self.assertFalse(L.is_locked(7, root=raw))
            data = L._load(raw)
            self.assertEqual(len(data["users"]["10"]), 1)


class MessageAndStatusTests(unittest.TestCase):
    def test_four_kinds_of_message_are_distinct(self):
        texts = {S.OUTPUT_REFUSAL, IM._FILTERED, S.OUTPUT_UNCHECKED, L.USER_CAP_REFUSAL, S.SOURCE_REFUSAL, L.LOCKOUT_REFUSAL}
        self.assertEqual(len(texts), 6)
        self.assertEqual(IM.error_status(ImagineError(S.OUTPUT_REFUSAL)), "safety_block")
        self.assertEqual(IM.error_status(ImagineError(S.SOURCE_REFUSAL)), "safety_block")
        self.assertEqual(IM.error_status(ImagineError(IM._FILTERED)), "provider_filtered")
        self.assertEqual(IM.error_status(ImagineError(S.OUTPUT_UNCHECKED)), "error")
        self.assertEqual(IM.error_status(ImagineError("Could not reach Imagine.")), "error")
        self.assertEqual(IM.error_status(ImagineError(L.USER_CAP_REFUSAL + " It resets in 12 min.")), "capped")
        self.assertIn("xAI", IM._FILTERED)
        self.assertNotIn("xAI", S.OUTPUT_REFUSAL)

    def test_cap_message_has_reset_minutes(self):
        with tempfile.TemporaryDirectory() as raw:
            with mock.patch.object(L, "_now", return_value=10_000.0):
                for _ in range(10):
                    L.record_use(1, 2, root=raw)
            with mock.patch.object(L, "_now", return_value=10_000.0 + 25 * 60):
                msg = L.rate_refusal(1, 2, root=raw, caps=(10, 40))
            self.assertEqual(msg, L.USER_CAP_REFUSAL + " It resets in 35 min.")

    def test_checker_error_is_not_shown_as_a_safety_block(self):
        with mock.patch("core.classifier.classify_media_async", new=mock.AsyncMock(return_value="")), \
                mock.patch("core.classifier.age_vision_async", new=mock.AsyncMock(return_value="age-vision-error")):
            with self.assertRaises(ImagineError) as err:
                asyncio.run(S.check_generated_media(FRAME, "image/jpeg", footer_hash=FOOTER, mode=SFW))
        self.assertEqual(err.exception.text, S.OUTPUT_UNCHECKED)
        self.assertEqual(err.exception.status, "error")

    def test_video_block_logs_frame_n_of_total(self):
        rows = []
        verdicts = iter(["", "", "", "age-vision-yes"])

        async def _age(*_a, **_k):
            return next(verdicts)

        with mock.patch.object(S, "extract_video_frames", return_value=[FRAME] * 49), \
                mock.patch("core.classifier.classify_media_async", new=mock.AsyncMock(return_value="")), \
                mock.patch("core.classifier.age_vision_async", new=_age), \
                mock.patch("core.audit.record", side_effect=lambda event, **kw: rows.append((event, kw))):
            with self.assertRaises(ImagineError):
                asyncio.run(S.check_generated_media(b"mp4", "video/mp4", footer_hash=FOOTER, mode=SFW))
        blocks = [kw for event, kw in rows if event == "asset" and kw.get("outcome") == "refusal"]
        self.assertEqual(blocks[-1]["frame"], "4/49")


class IntentGateTests(unittest.TestCase):
    def test_reactions_are_declined_and_requests_run(self):
        self.assertEqual(AI.decline_reason("A SFW anthropomorphized female F-35, cartoon style", "what the heck man"),
                         AI.DECLINE_NO_REQUEST)
        self.assertEqual(AI.decline_reason("anything", "ok stop trying lmfao"), AI.DECLINE_NO_REQUEST)
        self.assertEqual(AI.decline_reason("a dragon over a castle", "make a cat in a hat"), AI.DECLINE_NO_OVERLAP)
        self.assertEqual(AI.decline_reason("An adult female F-35 character, SFW", "Try again but make it sfw"), "")
        self.assertEqual(AI.decline_reason("same thing", "redo"), "")
        self.assertEqual(AI.decline_reason("A female F-35 fighter jet anthropomorphized, dancing",
                                           "Can you generate an video of a female f35 fighter jet but anthromorphized"), "")
        self.assertEqual(AI.decline_reason("give her a noticeable bulge", "Give her a noticeable bulge"), "")

    def test_model_added_anime_words_are_stripped_but_user_ones_stay(self):
        self.assertEqual(AI.strip_added_youthful_styles("A jet woman, anime style, dancing", "make a jet woman dancing"),
                         "A jet woman, dancing")
        self.assertEqual(AI.strip_added_youthful_styles("a cartoon jet, chibi", "make a cartoon jet"), "a cartoon jet")
        self.assertEqual(AI.strip_added_youthful_styles("an anime cat", "make an anime cat"), "an anime cat")

    def _ctx(self, text):
        msg = SimpleNamespace(
            id=1, content=text, author=SimpleNamespace(id=3, name="u", bot=False),
            guild=SimpleNamespace(id=9), channel=SimpleNamespace(id=2), reference=None,
            attachments=[], embeds=[], stickers=[],
        )
        return T.ToolContext(bot=SimpleNamespace(get_command=lambda name: None), message=msg)

    def test_declined_call_uses_no_slot_and_counts_as_not_run(self):
        rows = []
        run = mock.AsyncMock()
        allowed = mock.AsyncMock(return_value=(True, ""))
        with mock.patch.object(T, "default_tools_enabled", return_value=True), \
                mock.patch.object(T, "tool_prompt_enabled", return_value=True), \
                mock.patch.object(tools_asset, "run_asset", run), \
                mock.patch.object(tools_asset, "_allowed", allowed), \
                mock.patch("core.asset_limits.reserve_use") as reserve, \
                mock.patch("core.audit.record", side_effect=lambda event, **kw: rows.append((event, kw))):
            result = asyncio.run(T.execute("asset", {"prompt": "jet girl dancing", "kind": "video"}, self._ctx("what the heck man")))
        self.assertFalse(result.ran)
        self.assertTrue(result.declined)
        run.assert_not_awaited()
        allowed.assert_not_awaited()
        reserve.assert_not_called()
        self.assertIn(("tool", "declined"), [(e, kw.get("outcome")) for e, kw in rows])

    def test_tool_event_outcome_follows_the_asset_outcome_and_request_is_audited(self):
        rows = []

        def _gate(*_a, **k):
            k["out"].update(mode=SFW, owner=False)
            return None

        run = mock.AsyncMock(side_effect=ImagineError(S.OUTPUT_REFUSAL, status="safety_block"))
        with mock.patch.object(T, "default_tools_enabled", return_value=True), \
                mock.patch.object(T, "tool_prompt_enabled", return_value=True), \
                mock.patch("core.admin_tools.author_is_owner", mock.AsyncMock(return_value=False)), \
                mock.patch.object(tools_asset, "refuse_speaker", _gate), \
                mock.patch.object(tools_asset, "run_asset", run), \
                mock.patch.object(tools_asset, "api_key", lambda: "key"), \
                mock.patch("core.asset_source.source_from_reply", mock.AsyncMock(return_value=None)), \
                mock.patch("core.audit.record", side_effect=lambda event, **kw: rows.append((event, kw))):
            result = asyncio.run(T.execute("asset", {"prompt": "a red paper boat", "kind": "image"},
                                           self._ctx("make a red paper boat")))
        self.assertEqual(result.status, "safety_block")
        self.assertEqual(result.text, S.OUTPUT_REFUSAL)
        self.assertIn("Do not retry or rephrase", result.detail)
        tool_rows = [kw for e, kw in rows if e == "tool"]
        self.assertEqual((tool_rows[-1]["outcome"], tool_rows[-1]["status"]), ("refusal", "safety_block"))
        req = [kw for e, kw in rows if e == "asset-request"][-1]
        self.assertEqual((req["source"], req["kind"], req["prompt"]), ("chat", "image", "a red paper boat"))

    def test_memory_note_tells_the_model_not_to_retry(self):
        note = ML.tool_action("asset", {"prompt": "x"}, status="safety_block")
        self.assertIn("result: safety_block", note)
        self.assertIn("Do not retry or rephrase unless they ask again in a new message", note)
        self.assertNotIn("result:", ML.tool_action("asset", {"prompt": "x"}))

    def test_description_limits_when_to_call(self):
        desc = tools_asset._DESCRIPTION
        self.assertIn("Only call this when THIS message itself asks", desc)
        self.assertIn("never call it again unless they explicitly", desc)


class ProviderModerationAuditTests(unittest.TestCase):
    def test_provider_moderation_is_its_own_category(self):
        rows = []
        plan = IM.AssetPlan(mode="image", prompt="a red paper boat")
        prepared = S.PreparedPrompt(user_prompt="a red paper boat", sent_prompt="x", footer_hash=FOOTER)
        backend = mock.Mock()
        backend.api_base.return_value = "https://example.invalid"
        backend.image_url.side_effect = ImagineError(IM._FILTERED)
        with mock.patch.object(IM, "http_json", return_value=(200, {"data": []})), \
                mock.patch.object(IM, "request_for", return_value=("/images", {})), \
                mock.patch("core.asset_safety.require_enabled", lambda *a, **k: None), \
                mock.patch("core.audit.record", side_effect=lambda event, **kw: rows.append((event, kw))):
            with self.assertRaises(ImagineError) as err:
                asyncio.run(IM._run_asset_reserved(
                    plan, "key", SFW, {}, lambda: backend, mock.AsyncMock(return_value=prepared), mock.AsyncMock()))
        self.assertEqual(err.exception.status, "provider_filtered")
        cats = [kw.get("category") for e, kw in rows if e == "asset"]
        self.assertIn("provider-moderation", cats)


if __name__ == "__main__":
    unittest.main()
