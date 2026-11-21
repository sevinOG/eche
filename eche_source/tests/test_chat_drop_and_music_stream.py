# tests/test_chat_drop_and_music_stream.py
# Live 2026-11-20 (ET):
# - 22:53 and 23:30: Danetta's mention/reply got no answer. Two model calls
#   ran (the reply hit the 64-token cap with no finished sentence, then the
#   repair call came back cut/empty) and the turn ended with nothing posted
#   and nothing logged. Now: the repair gets max(160, 2x cap), a still-cut
#   repair falls back to the original text trimmed at its last word plus an
#   ellipsis, an empty turn is logged (gui line + audit outcome=empty with a
#   reason) and answered with a short fallback, and every api row carries the
#   model, finish reason and token counts.
# - 22:27 and 22:28: ?play got HTTP 403 from googlevideo because ffmpeg opened
#   the IP-locked URL itself. Now yt-dlp fetches the audio (same session,
#   headers, cookies, IPv4) and pipes it into ffmpeg; a refused or instantly
#   ended track is re-extracted once with cookies and another player client,
#   then reported in the channel; failures are audited as errors with stderr.
# Mocks only; nothing is fetched or played.
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

from core import client as C  # noqa: E402
from core import tools as T  # noqa: E402
from core import tools_asset  # noqa: E402
from core.client import ModelTurn  # noqa: E402
from cogs.music import music as M  # noqa: E402

BOT_ID = 1486240665951142079


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- chat: repair
class TrimAndRepairTests(unittest.TestCase):
    def test_trim_cut_text(self):
        self.assertEqual(C.trim_cut_text("well honestly I think the best part of the whole thi"),
                         "well honestly I think the best part of the whole\u2026")
        self.assertEqual(C.trim_cut_text("first, second, thir"), "first, second\u2026")
        self.assertEqual(C.trim_cut_text("one"), "one\u2026")
        self.assertEqual(C.trim_cut_text(""), "")
        self.assertEqual(C.trim_cut_text("   ...  "), "")

    def test_repair_tokens(self):
        self.assertEqual(C.repair_tokens(64), 160)
        self.assertEqual(C.repair_tokens(120), 240)
        self.assertEqual(C.repair_tokens(None), 160)

    def _settle(self, turn, repaired=None, *, raises=False, cap=64):
        if raises:
            model = mock.AsyncMock(side_effect=RuntimeError("down"))
        else:
            model = mock.AsyncMock(return_value=repaired)
        with mock.patch.object(C, "call_groq_turn", model):
            out = run(C.settle_cut_reply(turn, "prompt", 1, max_completion_tokens=cap))
        return out, model

    def test_repair_call_gets_the_bigger_cap(self):
        turn = ModelTurn("so the thing about that is", "", [], False, True)
        out, model = self._settle(turn, ModelTurn("Short and done.", "", [], False, False))
        self.assertEqual(model.await_args.kwargs["max_completion_tokens"], 160)
        self.assertEqual(out.reply, "Short and done.")
        _out, model = self._settle(ModelTurn("cut here and", "", [], False, True),
                                   ModelTurn("Done.", "", [], False, False), cap=100)
        self.assertEqual(model.await_args.kwargs["max_completion_tokens"], 200)

    def test_still_cut_repair_falls_back_to_trimmed_original(self):
        turn = ModelTurn("honestly the vibe tonight is kind of immacul", "", [], False, True)
        out, _ = self._settle(turn, ModelTurn("again it was cut befo", "", [], False, True))
        self.assertEqual(out.reply, "honestly the vibe tonight is kind of\u2026")

    def test_empty_repair_falls_back_to_trimmed_original(self):
        turn = ModelTurn("ok so basically what happened was", "", [], False, True)
        out, _ = self._settle(turn, ModelTurn("", "", [], False, False))
        self.assertEqual(out.reply, "ok so basically what happened\u2026")

    def test_repair_error_falls_back_to_trimmed_original(self):
        turn = ModelTurn("ok so basically what happened was", "", [], False, True)
        out, _ = self._settle(turn, raises=True)
        self.assertEqual(out.reply, "ok so basically what happened\u2026")

    def test_empty_original_uses_trimmed_repair(self):
        turn = ModelTurn("", "", [], False, True)
        out, _ = self._settle(turn, ModelTurn("I would say that it depends on", "", [], False, True))
        self.assertEqual(out.reply, "I would say that it depends\u2026")

    def test_all_empty_stays_empty(self):
        out, _ = self._settle(ModelTurn("", "", [], False, True), ModelTurn("", "", [], False, True))
        self.assertEqual(out.reply, "")

    def test_finished_sentence_needs_no_repair(self):
        turn = ModelTurn("That works. And then", "", [], False, True)
        out, model = self._settle(turn, ModelTurn("x", "", [], False, False))
        model.assert_not_awaited()
        self.assertEqual(out.reply, "That works.")


# ---------------------------------------------------------------- chat: api row
class ApiRowTests(unittest.TestCase):
    def test_rest_body_facts(self):
        data = {"model": "anthropic/some-model", "choices": [{"finish_reason": "length"}],
                "usage": {"completion_tokens": 64}}
        facts = C._completion_facts(SimpleNamespace(status_code=200), data,
                                    {"model": "openrouter/auto", "max_tokens": 64})
        self.assertEqual(facts, {"model": "anthropic/some-model", "finish_reason": "length",
                                 "completion_tokens": 64, "max_tokens": 64})

    def test_sdk_object_facts(self):
        completion = SimpleNamespace(model="qwen", choices=[SimpleNamespace(finish_reason="stop")],
                                     usage=SimpleNamespace(completion_tokens=12))
        facts = C._completion_facts(completion, "id-1", {"max_completion_tokens": 64})
        self.assertEqual(facts["model"], "qwen")
        self.assertEqual(facts["finish_reason"], "stop")
        self.assertEqual(facts["completion_tokens"], 12)
        self.assertEqual(facts["max_tokens"], 64)

    def test_audit_http_passes_facts(self):
        with mock.patch("core.audit.api_from_http") as api:
            C._audit_http(SimpleNamespace(status_code=200),
                          {"model": "m1", "choices": [{"finish_reason": "length"}], "usage": {"completion_tokens": 64}},
                          outcome="success", request={"max_tokens": 64})
        kw = api.call_args.kwargs
        self.assertEqual((kw["model"], kw["finish_reason"], kw["completion_tokens"], kw["max_tokens"]),
                         ("m1", "length", 64, 64))
        self.assertEqual(kw["outcome"], "success")

    def test_gui_line_shows_model_finish_and_tokens(self):
        from core import audit as A

        lines = []
        with mock.patch.object(A, "_append_line"), mock.patch.object(A, "_enqueue_line"), \
                mock.patch("core.gui_bridge.log", side_effect=lambda line, **kw: lines.append(line)):
            A.api("OpenRouter", response_id="gen-1", outcome="success", model="m1",
                  finish_reason="length", completion_tokens=64, max_tokens=64)
            A.record("chat", outcome="empty", reason="cut-unrepaired")
        self.assertIn("model=m1", lines[0])
        self.assertIn("finish=length", lines[0])
        self.assertIn("tokens=64/64", lines[0])
        self.assertIn("reason=cut-unrepaired", lines[1])


# ---------------------------------------------------------------- chat: empty turn
def _msg(text):
    return SimpleNamespace(
        id=601, content=text,
        author=SimpleNamespace(id=186263638472916992, name="danetta", display_name="Danetta", bot=False),
        guild=SimpleNamespace(id=1083466447164018738), channel=SimpleNamespace(id=1083466447734452308),
        reference=None, attachments=[], embeds=[], stickers=[], message_snapshots=[],
        mentions=[SimpleNamespace(id=BOT_ID)],
    )


class EmptyTurnTests(unittest.TestCase):
    def _turn(self, turns, *, settle=None, text="hey eche what do you think"):
        from cogs.events import on_message as om

        msg = _msg(text)
        media = SimpleNamespace(text="", memory="", summary="", images=[], reply_parts=[],
                                reply_images=[], message=msg)
        cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=BOT_ID)))
        sent, rows, gui = [], [], []

        async def fake_send(message, content):
            sent.append(content)

        offered = [{"type": "function", "function": {"name": "asset"}},
                   {"type": "function", "function": {"name": "duckduckgo"}}]
        remember_bot = mock.AsyncMock()
        patches = [
            mock.patch.object(om, "_send_reply", fake_send),
            mock.patch.object(om, "build_prompt", mock.AsyncMock(return_value="prompt")),
            mock.patch.object(om, "author_is_owner", mock.AsyncMock(return_value=False)),
            mock.patch.object(om, "admin_tools_enabled", lambda: False),
            mock.patch.object(om, "specs_for", lambda **_kw: offered),
            mock.patch.object(om, "visible_names", lambda **_kw: {"asset", "duckduckgo"}),
            mock.patch.object(om, "remember_user", mock.AsyncMock()),
            mock.patch.object(om, "remember_bot", remember_bot),
            mock.patch.object(cog, "_record_server_line", mock.AsyncMock()),
            mock.patch.object(T, "default_tools_enabled", return_value=True),
            mock.patch.object(T, "tool_prompt_enabled", return_value=True),
            mock.patch.object(tools_asset, "run_asset", mock.AsyncMock(side_effect=AssertionError("no run"))),
            mock.patch("core.audit.record", side_effect=lambda event, **kw: rows.append((event, kw)) or {}),
            mock.patch("core.gui_bridge.log", side_effect=lambda line, **kw: gui.append(line)),
            mock.patch.object(om, "call_groq_turn", mock.AsyncMock(side_effect=turns)),
        ]
        if settle is not None:
            patches.append(mock.patch.object(om, "settle_cut_reply", mock.AsyncMock(return_value=settle)))
        for p in patches:
            p.start()
        try:
            run(cog._answer_and_remember(msg, None, msg.content, msg.content, media))
        finally:
            for p in reversed(patches):
                p.stop()
        empty = [kw for e, kw in rows if e == "chat" and kw.get("outcome") == "empty"]
        return sent, empty, gui, remember_bot

    def _assert_fallback(self, sent, empty, gui, remember_bot, reason):
        from cogs.events import on_message as om

        self.assertEqual(sent, [om.EMPTY_REPLY_FALLBACK])
        self.assertEqual(len(empty), 1)
        self.assertEqual(empty[0]["reason"], reason)
        self.assertEqual(empty[0]["user_id"], 186263638472916992)
        self.assertTrue(any(line.startswith("No reply to Danetta: " + reason) for line in gui), gui)
        remember_bot.assert_not_awaited()  # the fallback is not remembered as Eche's words

    def test_cut_and_unrepaired(self):
        out = self._turn([ModelTurn("", "", [], False, True)], settle=ModelTurn("", "", [], False, False))
        self._assert_fallback(*out, "cut-unrepaired")
        self.assertTrue(out[1][0]["cut"])

    def test_empty_trace(self):
        self._assert_fallback(*self._turn([ModelTurn("", "", [], False, False)]), "empty-trace")

    def _declined_first(self):
        return ModelTurn("", "", [{"name": "asset", "arguments": {"prompt": "a jet", "kind": "image"}}])

    def test_declined_retry_toolcall(self):
        second = ModelTurn("", "", [{"name": "duckduckgo", "arguments": {"query": "x"}}])
        self._assert_fallback(*self._turn([self._declined_first(), second], text="lol ok"),
                              "declined-retry-toolcall")

    def test_declined_retry_empty(self):
        second = ModelTurn("", "", [])
        self._assert_fallback(*self._turn([self._declined_first(), second], text="lol ok"),
                              "declined-retry-empty")

    def test_normal_reply_logs_no_empty_row(self):
        sent, empty, gui, remember_bot = self._turn([ModelTurn("Pretty good, honestly.", "", [], False, False)])
        self.assertEqual(sent, ["Pretty good, honestly."])
        self.assertEqual(empty, [])
        self.assertIn("Replied to Danetta", gui)

    def test_fallback_wording(self):
        from cogs.events import on_message as om
        self.assertIn("lost my train of thought", om.EMPTY_REPLY_FALLBACK)


# ---------------------------------------------------------------- music
class PlaybackAuditTests(unittest.TestCase):
    def _audit(self, *a, **kw):
        rows = []
        with mock.patch("core.audit.safe_activity", side_effect=lambda name, **f: rows.append((name, f))):
            reason = M.audit_after_playback(SimpleNamespace(author=SimpleNamespace(id=5)), *a, **kw)
        return reason, rows[0][1]

    def test_403_exit_is_an_error_with_stderr(self):
        reason, row = self._audit(None, exit_code=3436169992, elapsed=0.4,
                                  stderr="[https] HTTP error 403 Forbidden")
        self.assertEqual(row["outcome"], "error")
        self.assertEqual(row["exit_code"], 3436169992)
        self.assertIn("403 Forbidden", row["stderr"])
        self.assertIn("ffmpeg exited with code", reason)

    def test_stream_error_is_an_error(self):
        reason, row = self._audit(None, exit_code=0, elapsed=0.2, stream_error="HTTP 403 Forbidden")
        self.assertEqual(row["outcome"], "error")
        self.assertIn("HTTP 403", row["stderr"])
        self.assertEqual(reason, "HTTP 403 Forbidden")

    def test_very_short_playback_is_an_error(self):
        reason, row = self._audit(None, exit_code=0, elapsed=1.2)
        self.assertEqual(row["outcome"], "error")
        self.assertIn("1.2s", reason)

    def test_normal_end_and_user_skip_are_success(self):
        self.assertEqual(self._audit(None, exit_code=0, elapsed=180.0)[1]["outcome"], "success")
        reason, row = self._audit(None, exit_code=None, elapsed=0.5, stopped=True)
        self.assertEqual((reason, row["outcome"]), ("", "success"))

    def test_player_error_is_an_error(self):
        self.assertEqual(self._audit(RuntimeError("boom"), elapsed=50.0)[1]["outcome"], "error")

    def test_stderr_tail_keeps_the_end(self):
        tail = M.StderrTail(limit=10)
        tail.write(b"0123456789")
        tail.write(b"abc")
        self.assertEqual(tail.text(), "3456789abc")
        self.assertFalse(hasattr(tail, "fileno"))

    def test_ffmpeg_exit_code(self):
        self.assertEqual(M._ffmpeg_exit_code(SimpleNamespace(_process=SimpleNamespace(poll=lambda: 1))), 1)
        self.assertIsNone(M._ffmpeg_exit_code(SimpleNamespace()))


class _Resp:
    def __init__(self, data, status=206):
        self.data, self.pos, self.status, self.closed = data, 0, status, False

    def read(self, n):
        out = self.data[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def close(self):
        self.closed = True


class _HTTPError(Exception):
    def __init__(self, status, reason):
        super().__init__(f"HTTP Error {status}: {reason}")
        self.status, self.reason = status, reason


class YtdlStreamTests(unittest.TestCase):
    PAYLOAD = bytes(range(256)) * 100  # 25,600 bytes

    def _opener(self, *, ignore_range=False, fail_at=None):
        calls = []

        def opener(url, headers):
            calls.append(dict(headers))
            if fail_at is not None and len(calls) - 1 == fail_at:
                raise _HTTPError(403, "Forbidden")
            if ignore_range:
                return _Resp(self.PAYLOAD, status=200)
            start, end = (int(x) for x in headers["Range"].split("=")[1].split("-"))
            if start >= len(self.PAYLOAD):
                raise _HTTPError(416, "Range Not Satisfiable")
            return _Resp(self.PAYLOAD[start:end + 1])
        return opener, calls

    def _drain(self, stream):
        out = b""
        while True:
            data = stream.read(4096)
            if not data:
                return out
            out += data

    def test_ranged_reads_rebuild_the_file_with_headers(self):
        opener, calls = self._opener()
        stream = M.YtdlStream("u", {"User-Agent": "UA", "Accept": "*/*"}, opener=opener, chunk=10000).prime()
        self.assertEqual(self._drain(stream), self.PAYLOAD)
        self.assertEqual(calls[0]["Range"], "bytes=0-9999")
        self.assertEqual(calls[1]["Range"], "bytes=10000-19999")
        self.assertEqual(calls[0]["User-Agent"], "UA")
        self.assertEqual(stream.error, "")

    def test_known_filesize_stops_without_an_extra_request(self):
        opener, calls = self._opener()
        stream = M.YtdlStream("u", {}, opener=opener, chunk=10000, filesize=len(self.PAYLOAD)).prime()
        self.assertEqual(self._drain(stream), self.PAYLOAD)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1]["Range"], "bytes=20000-25599")

    def test_server_ignoring_range_is_read_to_the_end(self):
        opener, calls = self._opener(ignore_range=True)
        stream = M.YtdlStream("u", {}, opener=opener, chunk=1000).prime()
        self.assertEqual(self._drain(stream), self.PAYLOAD)
        self.assertEqual(len(calls), 1)

    def test_refused_first_chunk_raises_before_ffmpeg(self):
        opener, _ = self._opener(fail_at=0)
        with self.assertRaises(M.StreamError) as raised:
            M.open_stream({"http_headers": {}}, "u", opener=opener)
        self.assertIn("403", str(raised.exception))

    def test_refused_later_chunk_ends_the_stream_and_keeps_the_error(self):
        opener, _ = self._opener(fail_at=1)
        stream = M.YtdlStream("u", {}, opener=opener, chunk=10000).prime()
        self.assertEqual(len(self._drain(stream)), 10000)
        self.assertIn("HTTP 403", stream.error)

    def test_open_stream_passes_track_settings(self):
        seen = {}
        real = M.YtdlStream

        def spy(url, headers, **kw):
            seen.update(kw, url=url, headers=headers)
            return real(url, headers, **kw)

        opener, _ = self._opener()
        info = {"http_headers": {"User-Agent": "UA"}, "filesize": 25600, "_eche_cookies": True,
                "downloader_options": {"http_chunk_size": 10485760}}
        with mock.patch.object(M, "YtdlStream", spy):
            M.open_stream(info, "https://example.invalid/a", opener=opener).close()
        self.assertTrue(seen["use_cookies"])
        self.assertEqual(seen["filesize"], 25600)
        self.assertEqual(seen["chunk"], 10485760)
        self.assertEqual(seen["headers"], {"User-Agent": "UA"})


class ExtractOptionsTests(unittest.TestCase):
    def test_ipv4_and_player_client(self):
        opts = M.ydl_options()
        self.assertEqual(opts["source_address"], "0.0.0.0")
        self.assertNotIn("extractor_args", opts)
        opts = M.ydl_options(player_client=M.RETRY_PLAYER_CLIENTS)
        self.assertEqual(opts["extractor_args"]["youtube"]["player_client"], list(M.RETRY_PLAYER_CLIENTS))

    def test_retry_extract_uses_cookies_and_another_client_first(self):
        calls = []
        info = {"url": "https://x/a", "title": "t"}

        def once(url, *, use_cookies, player_client=None):
            calls.append((use_cookies, player_client))
            return dict(info), None, None

        with mock.patch.object(M, "_extract_once", once), \
                mock.patch.object(M, "youtube_cookie_file", return_value="/c.txt"), \
                mock.patch.object(M, "_node_binary", return_value="/node"):
            got, _notes, exc = M.extract_youtube("u", retry=True)
        self.assertIsNone(exc)
        self.assertEqual(calls, [(True, M.RETRY_PLAYER_CLIENTS)])
        self.assertTrue(got["_eche_cookies"])

    def test_first_extract_is_logged_out(self):
        calls = []

        def once(url, *, use_cookies, player_client=None):
            calls.append((use_cookies, player_client))
            return {"url": "https://x/a"}, None, None

        with mock.patch.object(M, "_extract_once", once):
            got, _notes, _exc = M.extract_youtube("u")
        self.assertEqual(calls, [(False, None)])
        self.assertFalse(got["_eche_cookies"])

    def test_direct_fallback_passes_headers_and_user_agent(self):
        opts = M.direct_ffmpeg_options({"http_headers": {"User-Agent": "Mozilla/5.0 X", "Accept": "*/*"}})
        self.assertIn("-user_agent 'Mozilla/5.0 X'", opts["before_options"])
        self.assertIn("-headers", opts["before_options"])
        self.assertIn("Accept: */*", opts["before_options"])
        self.assertIn("-reconnect 1", opts["before_options"])

    def test_pipe_options_read_stdin(self):
        self.assertNotIn("-nostdin", M.PIPE_FFMPEG_OPTIONS["before_options"])


class TrackFailedTests(unittest.TestCase):
    def _cog(self):
        cog = M.Music(SimpleNamespace(loop=None))
        cog.play_next = mock.AsyncMock()
        return cog

    def test_first_failure_requeues_for_one_retry(self):
        cog = self._cog()
        entry = {"query": "song", "title": "Song"}
        cog.current = entry
        ctx = SimpleNamespace(send=mock.AsyncMock())
        run(cog._track_failed(ctx, entry, "HTTP 403 Forbidden"))
        self.assertTrue(entry["_stream_retry"])
        self.assertIs(cog.queue[0], entry)
        self.assertIsNone(cog.current)
        ctx.send.assert_not_awaited()
        cog.play_next.assert_awaited_once()

    def test_second_failure_says_403_and_moves_on(self):
        cog = self._cog()
        entry = {"query": "song", "title": "Song", "_stream_retry": True}
        cog.current = entry
        ctx = SimpleNamespace(send=mock.AsyncMock())
        run(cog._track_failed(ctx, entry, "HTTP 403 Forbidden"))
        text = ctx.send.await_args.args[0]
        self.assertIn("403 Forbidden", text)
        self.assertIn("**Song**", text)
        self.assertEqual(cog.queue, [])
        self.assertIsNone(cog.current)
        cog.play_next.assert_awaited_once()

    def test_skip_marks_user_stop(self):
        cog = self._cog()
        vc = SimpleNamespace(is_paused=lambda: False, is_playing=lambda: True, stop=mock.Mock())
        cog.vc = vc
        ctx = SimpleNamespace(send=mock.AsyncMock())
        run(M.Music.skip.callback(cog, ctx))
        self.assertTrue(cog._user_stopped)
        vc.stop.assert_called_once()


class RequirementsTests(unittest.TestCase):
    def test_yt_dlp_is_at_least_2026_10_26_and_hashed(self):
        text = (HERE.parent / "requirements.txt").read_text(encoding="utf-8")
        import re

        m = re.search(r"^yt-dlp==(\d+)\.(\d+)\.(\d+) \\\n((?:\s+--hash=sha256:[0-9a-f]{64}.*\n)+)", text, re.M)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(tuple(int(x) for x in m.groups()[:3]), (2026, 10, 26))
        self.assertGreaterEqual(m.group(4).count("--hash=sha256:"), 1)


if __name__ == "__main__":
    unittest.main()
