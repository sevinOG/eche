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


class RetryTests(unittest.TestCase):
    def _run(self, outcomes, *, prompt="a burger", record_fails=False):
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
                raise I.ImagineError("blocked", status="safety_block")

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
             mock.patch("core.audit.record", side_effect=fake_record):
            try:
                out = run(I._run_asset_reserved(
                    plan, "k", mock.Mock(), {"user_id": 1, "guild_id": 2, "channel_id": 3},
                    lambda: backend, mock.AsyncMock(return_value=prepared), check, notify=notify,
                ))
                return out, None, notes, records, seq
            except Exception as exc:
                return None, exc, notes, records, seq

    def test_block_then_pass_retries_once(self):
        out, exc, notes, records, _ = self._run(["block", "pass"])
        self.assertIsNone(exc)
        self.assertIsNotNone(out)
        self.assertEqual(notes, ["retrying"])
        rows = [kw for ev, kw in records if kw.get("outcome") == "retry"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["safety"])
        self.assertEqual(rows[0]["category"], "output-block-retry")
        # The retry carries stronger adult cues; the footer stays last.
        from core.safety import SAFETY_FOOTER
        self.assertEqual(rows[0]["reason"], "retry-adult-cues")
        self.assertEqual(rows[0]["added"], S.RETRY_ADULT_CUES)
        first, second = self.sent_prompts
        self.assertNotIn(S.RETRY_ADULT_LINE, first)
        self.assertIn(S.RETRY_ADULT_LINE, second)
        self.assertTrue(second.endswith(SAFETY_FOOTER))
        self.assertEqual(second.count(SAFETY_FOOTER), 1)
        # Imagine is sent the diffusion body: retry cues, no footer.
        d_first, d_second = self.diffusion_prompts
        self.assertNotIn(S.RETRY_ADULT_LINE, d_first)
        self.assertIn(S.RETRY_ADULT_LINE, d_second)
        self.assertNotIn(SAFETY_FOOTER, d_first)
        self.assertNotIn(SAFETY_FOOTER, d_second)
        self.assertIn(S.ADULT_WORDING, d_first)

    def test_second_block_refuses(self):
        out, exc, notes, _, seq = self._run(["block", "block", "pass"])
        self.assertEqual(getattr(exc, "status", ""), "safety_block")
        self.assertEqual(seq, ["pass"])  # only two attempts

    def test_no_retry_for_sexual_or_school_cues(self):
        from core.asset_filter import school_sexual_cues
        for word in ("nude", "school"):
            if any(school_sexual_cues(f"a {word} scene")):
                _, exc, notes, _, _ = self._run(["block", "pass"], prompt=f"a {word} scene")
                self.assertIsNotNone(exc)
                self.assertEqual(notes, [])

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
