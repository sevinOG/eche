"""2026-11-18 owner changes: adults line everywhere, 3-frame video checks,
one output retry, Making that!/retry lines, and "Eche used" as its own message.

Mock-only. No image is generated; frame bytes are placeholders.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from core import asset_safety as S
from core import announce as A


def run(coro):
    return asyncio.run(coro)


class AdultsLineTests(unittest.TestCase):
    def test_line_on_prompt_without_people(self):
        sent, _ = S.build_sent_prompt("a burger restaurant at night")
        self.assertIn(S.ALWAYS_ADULT_LINE, sent)
        self.assertIn("scenes with no people are fine", sent)
        self.assertIn("Keep the requested art style.", sent)
        from core.safety import SAFETY_FOOTER
        self.assertTrue(sent.endswith(SAFETY_FOOTER))

    def test_person_prompt_keeps_strong_hint_then_line(self):
        prompt = "a goth woman driving a car"
        sent, _ = S.build_sent_prompt(prompt)
        hint = S.character_adult_hint(prompt)
        self.assertIn(prompt + "\n\n" + hint + "\n\n" + S.ALWAYS_ADULT_LINE, sent)
        self.assertEqual(sent.count("Keep the requested art style."), 1)

    def test_edit_gets_line_and_hint(self):
        sent, _ = S.build_sent_prompt("make it blue", edit=True)
        self.assertIn(S.ALWAYS_ADULT_LINE, sent)
        self.assertIn("mature adult facial features and adult proportions", sent)

    def test_adult_mode_too(self):
        sent, _ = S.build_sent_prompt("a sunset over the sea", adult=True)
        self.assertIn(S.ALWAYS_ADULT_LINE, sent)


class FrameSampleTests(unittest.TestCase):
    def test_output_samples_begin_middle_end(self):
        self.assertEqual(S.check_frame_indexes(49), [1, 25, 49])
        self.assertEqual(S.check_frame_indexes(2), [1, 2])
        self.assertEqual(S.check_frame_indexes(1), [1])
        self.assertEqual(S.check_frame_indexes(0), [])

    def test_source_still_every_frame(self):
        self.assertEqual(S.check_frame_indexes(5, "source"), [1, 2, 3, 4, 5])

    def _run_check(self, verdicts):
        calls = []

        async def fake(data, mime, **kw):
            calls.append(kw["frame"])
            if verdicts.get(kw["frame"]):
                from core.imagine import ImagineError
                raise ImagineError("blocked", status="safety_block")

        frames = [bytes([i]) for i in range(49)]
        with mock.patch.object(S, "extract_video_frames", return_value=frames), \
             mock.patch.object(S, "_classify_image", side_effect=fake):
            try:
                run(S.check_generated_media(b"x", "video/mp4", footer_hash="h", mode=mock.Mock()))
                return calls, None
            except Exception as exc:
                return calls, exc

    def test_three_frames_checked_and_one_yes_blocks(self):
        calls, exc = self._run_check({})
        self.assertEqual(calls, ["1/49", "25/49", "49/49"])
        self.assertIsNone(exc)
        calls, exc = self._run_check({"25/49": True})
        self.assertEqual(getattr(exc, "status", ""), "safety_block")

    def test_no_frames_fails_closed(self):
        with mock.patch.object(S, "extract_video_frames", return_value=[]), \
             mock.patch.object(S, "_audit"):
            with self.assertRaises(Exception):
                run(S.check_generated_media(b"x", "video/mp4", footer_hash="h", mode=mock.Mock()))

    def test_image_single_check(self):
        with mock.patch.object(S, "_classify_image", new=mock.AsyncMock()) as m:
            run(S.check_generated_media(b"\xff\xd8x", "image/jpeg", footer_hash="h", mode=mock.Mock()))
        self.assertEqual(m.await_count, 1)


def ImagineErrorFor(text):
    from core.imagine import ImagineError
    return ImagineError(text, status="safety_block")


class RetryTests(unittest.TestCase):
    REVISED = "a woman in her 30s eating a burger, realistic photo"

    def _run(self, outcomes, *, prompt="a burger", record_fails=False, revised=REVISED, prepare=None):
        from core import imagine as I

        plan = SimpleNamespace(prompt=prompt, user_text=prompt, model_prompt=prompt, mode="image", source=None)
        prepared = S.PreparedPrompt(
            user_prompt=prompt,
            sent_prompt=S.build_sent_prompt(prompt)[0],
            footer_hash="h",
            diffusion_prompt=S.build_diffusion_prompt(prompt),
        )
        self.sent_prompts = []
        self.diffusion_prompts = []
        seq = list(outcomes)

        async def check(*a, **k):
            if seq.pop(0) == "block":
                raise I.ImagineError("blocked", status="safety_block",
                                     category="output-age-vision-unsure", label="uncertain age")

        self.prepare_calls = []

        async def default_prepare(text, **kw):
            self.prepare_calls.append((text, kw))
            return S.PreparedPrompt(
                user_prompt=text,
                sent_prompt=S.build_sent_prompt(text)[0],
                footer_hash="h",
                diffusion_prompt=S.build_diffusion_prompt(text),
            )

        self.rewrite = mock.AsyncMock(return_value=revised)

        backend = mock.Mock()
        backend.api_base.return_value = "https://x"
        backend.image_url.return_value = "https://x/img"
        notes = []

        async def notify(event):
            notes.append(event)

        records = []

        def fake_record(event, **kw):
            if record_fails:
                raise OSError("disk")
            records.append((event, kw))

        posted = mock.Mock()
        posted.__iter__ = lambda self: iter((200, {}))
        def request_for(_plan, prep):
            self.sent_prompts.append(prep.sent_prompt)
            self.diffusion_prompts.append(prep.diffusion_prompt)
            return "/p", {}

        with mock.patch.object(I, "request_for", side_effect=request_for), \
             mock.patch.object(I, "http_json", return_value=(200, {})), \
             mock.patch.object(I, "http_bytes", return_value=(b"\xff\xd8\xffdata", "image/jpeg")), \
             mock.patch("core.asset_safety.require_enabled"), \
             mock.patch("core.asset_safety._audit"), \
             mock.patch("core.audit.record", side_effect=fake_record), \
             mock.patch("core.asset_safety.rewrite_for_retry", new=self.rewrite):
            try:
                out = run(I._run_asset_reserved(
                    plan, "k", mock.Mock(adult=False), {"user_id": 1, "guild_id": 2, "channel_id": 3},
                    lambda: backend, prepare or default_prepare, check, notify=notify,
                ))
                return out, None, notes, records, seq
            except Exception as exc:
                return None, exc, notes, records, seq

    def test_block_then_pass_retries_once_with_the_rewrite(self):
        out, exc, notes, records, _ = self._run(["block", "pass"])
        self.assertIsNone(exc)
        self.assertIsNotNone(out)
        self.assertEqual(notes, ["retrying"])
        # The text model is told only the category and the checker's label.
        args, kwargs = self.rewrite.await_args
        self.assertEqual(args, ("a burger", "output-age-vision-unsure", "uncertain age"))
        # The revision plus the code-added adult age went through prepare_generation.
        forced = self.REVISED + ". " + S.FORCED_ADULT_AGE
        self.assertEqual([t for t, _ in self.prepare_calls], ["a burger", forced])
        rows = [kw for ev, kw in records if kw.get("outcome") == "retry"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["safety"])
        self.assertEqual(rows[0]["category"], "output-block-retry")
        self.assertEqual(rows[0]["reason"], "rewrite")
        self.assertTrue(rows[0]["forced_adult_age"])
        self.assertEqual(rows[0]["block_label"], "uncertain age")
        # Ids and hashes only: no prompt text in the row.
        self.assertEqual(len(rows[0]["rewrite_sha256"]), 16)
        for value in rows[0].values():
            self.assertNotIn("burger", str(value))
        from core.safety import SAFETY_FOOTER
        first, second = self.sent_prompts
        self.assertNotIn("30 years old", first)
        self.assertIn(forced, second)
        self.assertTrue(second.endswith(SAFETY_FOOTER))
        self.assertEqual(second.count(SAFETY_FOOTER), 1)
        d_first, d_second = self.diffusion_prompts
        self.assertIn(S.FORCED_ADULT_AGE, d_second)
        self.assertNotIn(SAFETY_FOOTER, d_second)
        self.assertIn(S.ADULT_WORDING, d_first)
        self.assertIn("aged 25 or older", d_second)  # the adult line was added again

    def test_rewrite_blocked_by_input_checks_falls_back_then_retries(self):
        calls = []

        async def prepare(text, **kw):
            calls.append(text)
            if len(calls) == 2:
                raise ImagineErrorFor("blocked by input checks")
            return S.PreparedPrompt(user_prompt=text, sent_prompt=S.build_sent_prompt(text)[0],
                                    footer_hash="h", diffusion_prompt=S.build_diffusion_prompt(text))

        out, exc, notes, records, seq = self._run(["block", "pass"], prepare=prepare)
        self.assertIsNone(exc)
        self.assertIsNotNone(out)
        self.assertEqual(calls, ["a burger", self.REVISED + ". " + S.FORCED_ADULT_AGE,
                                 "a burger. " + S.FORCED_ADULT_AGE])
        self.assertEqual(notes, ["retrying"])
        reasons = [kw.get("reason") for _, kw in records if kw.get("category") == "output-block-retry"]
        self.assertEqual(reasons, ["rewrite", "rewrite-blocked", "fallback"])

    def test_every_retry_prompt_blocked_fails_closed(self):
        calls = []

        async def prepare(text, **kw):
            calls.append(text)
            if len(calls) > 1:
                raise ImagineErrorFor("blocked by input checks")
            return S.PreparedPrompt(user_prompt=text, sent_prompt=S.build_sent_prompt(text)[0],
                                    footer_hash="h", diffusion_prompt=S.build_diffusion_prompt(text))

        out, exc, notes, records, seq = self._run(["block", "pass"], prepare=prepare)
        self.assertIsNone(out)
        self.assertEqual(str(exc), "blocked")  # the original block and its message
        self.assertEqual(getattr(exc, "status", ""), "safety_block")
        self.assertEqual(len(calls), 3)
        self.assertEqual(notes, [])
        self.assertEqual(seq, ["pass"])  # nothing blocked was generated
        reasons = [kw.get("reason") for _, kw in records if kw.get("category") == "output-block-retry"]
        self.assertEqual(reasons, ["rewrite", "rewrite-blocked", "fallback", "fallback-blocked"])

    def test_rewrite_failure_falls_back_to_original_plus_forced_age(self):
        for bad in ("", None):
            out, exc, notes, records, seq = self._run(["block", "pass"], revised=bad)
            self.assertIsNone(exc)
            self.assertIsNotNone(out)
            self.assertEqual(notes, ["retrying"])
            self.assertEqual([t for t, _ in self.prepare_calls], ["a burger", "a burger. " + S.FORCED_ADULT_AGE])
            reasons = [kw.get("reason") for _, kw in records if kw.get("category") == "output-block-retry"]
            self.assertEqual(reasons, ["rewrite-failed", "fallback"])
            self.assertIn(S.FORCED_ADULT_AGE, self.diffusion_prompts[1])

    def test_rewrite_failure_then_second_block_refuses(self):
        out, exc, notes, _, seq = self._run(["block", "block", "pass"], revised="")
        self.assertEqual(getattr(exc, "status", ""), "safety_block")
        self.assertEqual(seq, ["pass"])
        self.assertEqual(notes, ["retrying"])

    def test_rewrite_call_exception_fails_closed(self):
        from core import imagine as I
        with mock.patch("core.client.call_groq_raw", new=mock.AsyncMock(side_effect=RuntimeError("down"))):
            self.assertEqual(run(S.rewrite_for_retry("a burger", "output-age-vision-unsure", "uncertain age")), "")
        _ = I

    def test_only_one_retry(self):
        out, exc, notes, _, seq = self._run(["block", "block", "pass"])
        self.assertEqual(getattr(exc, "status", ""), "safety_block")
        self.assertEqual(seq, ["pass"])
        self.assertEqual(self.rewrite.await_count, 1)
        self.assertEqual(len(self.prepare_calls), 2)
        self.assertEqual(notes, ["retrying"])

    def test_second_block_refuses(self):
        out, exc, notes, _, seq = self._run(["block", "block", "pass"])
        self.assertEqual(getattr(exc, "status", ""), "safety_block")
        self.assertEqual(seq, ["pass"])  # only two attempts

    def test_no_retry_for_sexual_or_school_cues(self):
        from core.asset_filter import school_sexual_cues
        for word in ("nude", "school"):
            if any(school_sexual_cues(f"a {word} scene")):
                _, exc, notes, records, _ = self._run(["block", "pass"], prompt=f"a {word} scene")
                self.assertIsNotNone(exc)
                self.assertEqual(notes, [])
                self.assertEqual(self.rewrite.await_count, 0)
                skipped = [kw for _, kw in records if kw.get("reason") == "retry-not-allowed"]
                self.assertEqual(len(skipped), 1)
                self.assertNotIn(word, str(skipped[0].get("block_label")))
                self.assertTrue(skipped[0]["school_cues"] or skipped[0]["sexual_cues"])
                for key in ("prompt", "user_text"):
                    self.assertNotIn(key, skipped[0])

    def test_failed_retry_log_keeps_block(self):
        _, exc, notes, _, seq = self._run(["block", "pass"], record_fails=True)
        self.assertEqual(getattr(exc, "status", ""), "safety_block")
        self.assertEqual(notes, [])
        self.assertEqual(seq, ["pass"])

    def test_errors_are_not_retried(self):
        from core import imagine as I
        plan = SimpleNamespace(prompt="a burger", user_text="", model_prompt="")
        self.assertTrue(I.output_retry_allowed(plan))


class FakeChannel:
    def __init__(self, cid=1):
        self.id = cid
        self.sent = []

    async def send(self, text, **kw):
        self.sent.append(text)


class AnnounceTests(unittest.TestCase):
    def setUp(self):
        A._sent.clear()
        A._window.clear()

    def test_tool_line_is_its_own_message_and_not_in_mono_block(self):
        from core.tools import format_tool_messages
        ch = FakeChannel()
        self.assertTrue(run(A.announce_tool(ch, "duckduckgo")))
        self.assertEqual(ch.sent, ["Eche used *search*!"])
        for fence in (True, False):
            chunks = format_tool_messages("duckduckgo", "result text", fence=fence)
            self.assertTrue(chunks)
            for chunk in chunks:
                self.assertNotIn("Eche used", chunk)
        self.assertTrue(format_tool_messages("duckduckgo", "x")[0].startswith("```"))
        self.assertEqual(format_tool_messages("thread", ""), [])

    def test_lines_never_include_arguments(self):
        self.assertEqual(A.tool_line("asset"), "Eche used *asset*!")
        self.assertEqual(A.tool_line("bad <@123> name"), "Eche used *bad123name*!")

    def test_rate_safe(self):
        ch = FakeChannel()
        run(A.announce_tool(ch, "play"))
        run(A.announce_tool(ch, "play"))
        self.assertEqual(len(ch.sent), 1)
        for i in range(20):
            run(A.post_line(ch, f"Eche used *t{i}*!"))
        self.assertLessEqual(len(ch.sent), A.CHANNEL_MAX)

    def test_send_failure_is_swallowed(self):
        ch = mock.Mock(id=2)
        ch.send = mock.AsyncMock(side_effect=RuntimeError("x"))
        self.assertFalse(run(A.announce_tool(ch, "asset")))

    def test_asset_start_and_retry_lines(self):
        from core.tools_asset import announce_start, retry_notifier, asset_text
        ch = FakeChannel()
        run(announce_start(ch, "video"))
        self.assertEqual(ch.sent, ["Eche used *asset*!"])
        note = retry_notifier(ch, "video")
        run(note("started"))
        run(note("retrying"))
        self.assertEqual(ch.sent, ["Eche used *asset*!", "That one didn't pass, trying again..."])
        ch2 = FakeChannel(9)
        run(retry_notifier(ch2, "image")("started"))
        self.assertEqual(ch2.sent, [])  # "Making that!" removed 2026-11-18
        self.assertNotIn("Eche used", asset_text("a caption"))


class MakingAfterCapTests(unittest.TestCase):
    def _run(self, capped, *, owner=False):
        from core import imagine as I

        events = []

        async def notify(event):
            events.append(event)

        snap = mock.Mock(enabled=True, user_cap=1, guild_cap=1)
        plan = SimpleNamespace(mode="image")
        with mock.patch("core.asset_limits.reserve_use", return_value="capped text" if capped else ""), \
             mock.patch("core.asset_limits.release_use"), \
             mock.patch("core.asset_safety._audit"), \
             mock.patch("core.asset_safety.video_caps", return_value=None), \
             mock.patch.object(I, "_run_asset_reserved", new=mock.AsyncMock(return_value="file")):
            try:
                run(I.run_asset(plan, "k", asset_mode=snap, is_owner=owner, notify=notify, user_id=1, guild_id=2))
            except I.ImagineError:
                pass
        return events

    def test_capped_request_gets_no_making_line(self):
        self.assertEqual(self._run(True), [])

    def test_started_after_cap_passes(self):
        self.assertEqual(self._run(False), ["started"])

    def test_owner_started(self):
        self.assertEqual(self._run(True, owner=True), ["started"])


if __name__ == "__main__":
    unittest.main()
