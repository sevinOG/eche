# tests/test_music_formats_hls.py
# Live 2026-11-21 (ET), after PR #28:
# - every ?play first attempt: "HTTP 403 Forbidden" (logged-out extraction);
# - the retry (web_safari/mweb/tv) picked an HLS format and the pipe fed the
#   m3u8 playlist text into ffmpeg's stdin: "Not detecting m3u8/hls with non
#   standard extension ... Error opening input file -. Invalid data";
# - 21:34: ConnectionResetError(10054) mid-song, partial file.
# Now: cookies on the first attempt, PO-token-free player clients, a
# progressive-only pipe (each progressive candidate tried in turn), HLS handed
# straight to ffmpeg's hls demuxer with yt-dlp's headers, and one or two
# mid-stream resumes at the current byte. Mocks only; nothing is fetched.
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

from cogs.music import music as M  # noqa: E402


def _fmt(fid, proto, *, c="VISIONOS", acodec="opus", vcodec="none", abr=None, tbr=None, url=None):
    host = "manifest.googlevideo.com/api/manifest/hls_playlist" if "m3u8" in proto else "rr1---x.googlevideo.com/videoplayback"
    return {
        "format_id": fid, "protocol": proto, "acodec": acodec, "vcodec": vcodec, "abr": abr, "tbr": tbr,
        "url": url or f"https://{host}?itag={fid}&c={c}",
        "http_headers": {"User-Agent": f"UA-{c}", "Accept": "*/*"},
    }


class _Resp:
    def __init__(self, data, status=206, fail_after=None, exc=None):
        self.data, self.pos, self.status = data, 0, status
        self.fail_after, self.exc = fail_after, exc

    def read(self, n):
        if self.fail_after is not None and self.pos >= self.fail_after:
            raise self.exc
        end = self.pos + n
        if self.fail_after is not None:
            end = min(end, self.fail_after)
        out = self.data[self.pos:end]
        self.pos += len(out)
        return out

    def close(self):
        pass


class _HTTPError(Exception):
    def __init__(self, status, reason="Forbidden"):
        super().__init__(f"HTTP Error {status}: {reason}")
        self.status, self.reason = status, reason


class FormatSelectionTests(unittest.TestCase):
    def test_selector_prefers_progressive_and_excludes_manifests(self):
        self.assertTrue(M.FORMAT_SELECTOR.startswith(
            "bestaudio[protocol^=http][protocol!*=m3u8][protocol!*=dash]/bestaudio[protocol=https]"))
        self.assertEqual(M.ydl_options()["format"], M.FORMAT_SELECTOR)

    def test_selector_picks_progressive_over_hls_in_yt_dlp(self):
        if M.yt_dlp is None:
            self.skipTest("yt-dlp not installed")
        formats = [
            dict(_fmt("234", "m3u8_native", c="WEB", abr=None, tbr=200), ext="mp4", acodec="mp4a.40.2"),
            dict(_fmt("140", "https", c="TVHTML5", abr=129, tbr=129), ext="m4a", acodec="mp4a.40.2"),
            dict(_fmt("dash1", "http_dash_segments", c="TVHTML5", abr=300, tbr=300), ext="m4a", acodec="mp4a.40.2"),
        ]
        with M.yt_dlp.YoutubeDL({"format": M.FORMAT_SELECTOR, "quiet": True}) as ydl:
            chosen = list(ydl.build_format_selector(M.FORMAT_SELECTOR)({"formats": formats, "has_merged_format": False,
                                                                          "incomplete_formats": False}))
        self.assertEqual(chosen[0]["format_id"], "140")
        only_hls = [formats[0]]
        with M.yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            chosen = list(ydl.build_format_selector(M.FORMAT_SELECTOR)({"formats": only_hls, "has_merged_format": False,
                                                                          "incomplete_formats": False}))
        self.assertEqual(chosen[0]["format_id"], "234")

    def test_progressive_and_hls_predicates(self):
        self.assertTrue(M.is_progressive_format(_fmt("251", "https")))
        self.assertTrue(M.is_progressive_format(_fmt("18", "https", acodec="mp4a", vcodec="avc1")))
        for proto in ("m3u8_native", "m3u8", "http_dash_segments", "dash", "mhtml"):
            self.assertFalse(M.is_progressive_format(_fmt("x", proto)), proto)
        self.assertFalse(M.is_progressive_format(_fmt("sb", "https", acodec="none", vcodec="none")))
        self.assertTrue(M.is_hls_format(_fmt("96", "m3u8_native")))
        self.assertFalse(M.is_hls_format(_fmt("251", "https")))

    def test_candidates_order(self):
        info = dict(_fmt("251", "https", abr=130))
        info["formats"] = [
            _fmt("249", "https", abr=50),
            _fmt("251", "https", abr=130),
            _fmt("18", "https", acodec="mp4a", vcodec="avc1", tbr=500),
            _fmt("140w", "https", c="WEB", abr=129),         # PO-token client: last
            _fmt("96", "m3u8_native", acodec="mp4a", vcodec="avc1", tbr=1500),
            _fmt("91", "m3u8_native", acodec="mp4a", vcodec="avc1", tbr=200),
            _fmt("233", "m3u8_native", abr=48),
            _fmt("sb0", "mhtml", acodec="none", vcodec="none"),
        ]
        progressive, hls = M.stream_candidates(info)
        self.assertEqual([f["format_id"] for f in progressive], ["251", "249", "18", "140w"])
        self.assertEqual([f["format_id"] for f in hls], ["233", "91", "96"])

    def test_chosen_hls_leads_the_hls_group(self):
        info = dict(_fmt("96", "m3u8_native", acodec="mp4a", vcodec="avc1", tbr=1500))
        info["formats"] = [_fmt("91", "m3u8_native", acodec="mp4a", vcodec="avc1", tbr=200), dict(info)]
        progressive, hls = M.stream_candidates(info)
        self.assertEqual(progressive, [])
        self.assertEqual(hls[0]["format_id"], "96")


class OpenTrackTests(unittest.TestCase):
    PAYLOAD = b"x" * 5000

    def test_first_progressive_answer_is_piped(self):
        info = dict(_fmt("251", "https"))
        info["formats"] = [dict(info), _fmt("96", "m3u8_native", acodec="mp4a", vcodec="avc1")]
        seen = []

        def opener(url, headers):
            seen.append((url, headers))
            return _Resp(self.PAYLOAD)

        mode, stream, fmt = M.open_track(info, opener=opener)
        self.assertEqual((mode, fmt["format_id"]), ("pipe", "251"))
        self.assertEqual(seen[0][1]["User-Agent"], "UA-VISIONOS")
        self.assertTrue(seen[0][1]["Range"].startswith("bytes=0-"))
        stream.close()

    def test_refused_progressive_tries_the_next_then_pipes(self):
        info = dict(_fmt("251", "https"))
        info["formats"] = [dict(info), _fmt("140", "https", c="TVHTML5", abr=128)]
        calls = []

        def opener(url, headers):
            calls.append(url)
            if "itag=251" in url:
                raise _HTTPError(403)
            return _Resp(self.PAYLOAD)

        mode, stream, fmt = M.open_track(info, opener=opener)
        self.assertEqual((mode, fmt["format_id"]), ("pipe", "140"))
        self.assertEqual(len(calls), 2)
        stream.close()

    def test_all_progressive_refused_goes_hls_direct(self):
        info = dict(_fmt("251", "https"))
        info["formats"] = [dict(info), _fmt("233", "m3u8_native", abr=48)]
        opener = mock.Mock(side_effect=_HTTPError(403))
        mode, stream, fmt = M.open_track(info, opener=opener)
        self.assertEqual((mode, stream, fmt["format_id"]), ("hls", None, "233"))

    def test_hls_only_never_touches_the_pipe(self):
        info = dict(_fmt("96", "m3u8_native", acodec="mp4a", vcodec="avc1"))
        info["formats"] = [dict(info)]
        opener = mock.Mock(side_effect=AssertionError("an m3u8 must not be piped"))
        mode, stream, fmt = M.open_track(info, opener=opener)
        self.assertEqual((mode, stream), ("hls", None))
        opener.assert_not_called()

    def test_nothing_playable_raises_with_what_was_tried(self):
        info = dict(_fmt("251", "https"))
        info["formats"] = [dict(info)]
        with self.assertRaises(M.StreamError) as raised:
            M.open_track(info, opener=mock.Mock(side_effect=_HTTPError(403)))
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertIn("251/https/VISIONOS", str(raised.exception))
        with self.assertRaises(M.StreamError):
            M.open_track({"formats": []})

    def test_pipe_tries_are_capped(self):
        info = {"formats": [_fmt(str(i), "https", abr=100 - i) for i in range(10)]}
        opener = mock.Mock(side_effect=_HTTPError(403))
        with self.assertRaises(M.StreamError):
            M.open_track(info, opener=opener)
        self.assertEqual(opener.call_count, M.MAX_PIPE_TRIES)


class HlsDirectTests(unittest.TestCase):
    def test_hls_options_use_the_hls_demuxer_and_headers(self):
        opts = M.hls_ffmpeg_options(_fmt("96", "m3u8_native", c="WEB"))
        before = opts["before_options"]
        self.assertIn("-f hls", before)
        self.assertIn("-user_agent UA-WEB", before)
        self.assertIn("-headers 'Accept: */*\r\n'", before)
        self.assertIn("-nostdin", before)
        self.assertNotIn("pipe", before)
        self.assertEqual(opts["options"], "-vn")

    def test_start_current_hands_hls_url_to_ffmpeg(self):
        cog = M.Music(SimpleNamespace(loop=None))
        entry = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        cog.current, cog.playing = entry, True
        vc = SimpleNamespace(play=mock.Mock(), is_playing=lambda: False)
        cog.vc = vc
        hls = _fmt("96", "m3u8_native", acodec="mp4a", vcodec="avc1", c="WEB")
        info = dict(hls, title="Song", uploader="Artist", duration=200, formats=[dict(hls)])
        made = []

        class FakeFFmpeg:
            def __init__(self, source, **kw):
                made.append((source, kw))

        ctx = SimpleNamespace(send=mock.AsyncMock(), author=None, guild=None, channel=None)
        with mock.patch.object(cog, "_resolve_ffmpeg", mock.AsyncMock(return_value="/usr/bin/ffmpeg")), \
                mock.patch.object(cog, "extract_audio_url", return_value=(info, info["url"])), \
                mock.patch.object(cog, "update_queue_message", mock.AsyncMock()), \
                mock.patch.object(cog, "send_now_playing", mock.AsyncMock()), \
                mock.patch.object(M.discord, "FFmpegPCMAudio", FakeFFmpeg):
            asyncio.run(cog._start_current(ctx))
        source, kw = made[0]
        self.assertEqual(source, hls["url"])
        self.assertNotIn("pipe", kw)
        self.assertIn("-f hls", kw["before_options"])
        vc.play.assert_called_once()


class PlayerClientTests(unittest.TestCase):
    def test_no_po_token_clients(self):
        for clients in (M.PLAYER_CLIENTS_COOKIES, M.PLAYER_CLIENTS_NO_COOKIES,
                        M.RETRY_PLAYER_CLIENTS_COOKIES, M.RETRY_PLAYER_CLIENTS_NO_COOKIES):
            self.assertFalse(set(clients) & M.PO_TOKEN_CLIENTS, clients)
            self.assertEqual(clients[-1], "web_safari")  # HLS fallback last

    def test_retry_uses_a_different_client_set(self):
        self.assertNotEqual(M.player_clients(cookies=True), M.player_clients(cookies=True, retry=True))
        self.assertNotEqual(M.player_clients(cookies=False), M.player_clients(cookies=False, retry=True))

    def test_cookie_lists_lead_with_cookie_clients(self):
        self.assertNotIn("visionos", M.PLAYER_CLIENTS_COOKIES)  # yt-dlp skips it with cookies
        self.assertEqual(M.PLAYER_CLIENTS_COOKIES[0], "tv")

    def _extract(self, results, *, cookie="/c.txt", node="/node", retry=False):
        calls = []
        seq = list(results)

        def once(url, *, use_cookies, player_client=None):
            calls.append((use_cookies, player_client))
            return seq.pop(0)

        with mock.patch.object(M, "_extract_once", once), \
                mock.patch.object(M, "youtube_cookie_file", return_value=cookie), \
                mock.patch.object(M, "_node_binary", return_value=node):
            out = M.extract_youtube("https://www.youtube.com/watch?v=x", retry=retry)
        return out, calls

    def test_cookies_on_the_first_attempt(self):
        (info, _n, exc), calls = self._extract([({"url": "https://x/a"}, None, None)])
        self.assertEqual(calls, [(True, M.PLAYER_CLIENTS_COOKIES)])
        self.assertTrue(info["_eche_cookies"])
        self.assertIsNone(exc)

    def test_cookie_pass_failure_falls_back_to_logged_out(self):
        (info, _n, exc), calls = self._extract([(None, None, RuntimeError("cookies stale")),
                                                ({"url": "https://x/a"}, None, None)])
        self.assertEqual(calls, [(True, M.PLAYER_CLIENTS_COOKIES), (False, M.PLAYER_CLIENTS_NO_COOKIES)])
        self.assertFalse(info["_eche_cookies"])

    def test_no_node_means_no_cookies(self):
        (_info, _n, _e), calls = self._extract([({"url": "https://x/a"}, None, None)], node=None)
        self.assertEqual(calls, [(False, M.PLAYER_CLIENTS_NO_COOKIES)])


class ResumeTests(unittest.TestCase):
    PAYLOAD = bytes(range(256)) * 40  # 10,240 bytes

    def _drain(self, stream):
        out = b""
        while True:
            data = stream.read(1000)
            if not data:
                return out
            out += data

    def test_reset_mid_stream_resumes_at_the_current_byte(self):
        calls = []
        reset = ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")

        def opener(url, headers):
            calls.append(headers["Range"])
            start, end = (int(x) for x in headers["Range"].split("=")[1].split("-"))
            data = self.PAYLOAD[start:end + 1]
            if len(calls) == 1:
                return _Resp(data, fail_after=3000, exc=reset)
            return _Resp(data)

        stream = M.YtdlStream("u", {}, opener=opener, chunk=8000, filesize=len(self.PAYLOAD)).prime()
        self.assertEqual(self._drain(stream), self.PAYLOAD)
        self.assertEqual(calls[:2], ["bytes=0-7999", "bytes=3000-7999"])
        self.assertEqual(stream.resumes, 1)
        self.assertEqual(stream.error, "")

    def test_resumes_are_limited_and_the_error_is_kept(self):
        reset = ConnectionResetError(10054, "reset")

        def opener(url, headers):
            start, end = (int(x) for x in headers["Range"].split("=")[1].split("-"))
            return _Resp(self.PAYLOAD[start:end + 1], fail_after=500, exc=reset)

        stream = M.YtdlStream("u", {}, opener=opener, chunk=8000).prime()
        got = self._drain(stream)
        self.assertEqual(len(got), 500 * (M.YtdlStream.MAX_RESUMES + 1))
        self.assertEqual(stream.resumes, M.YtdlStream.MAX_RESUMES)
        self.assertIn("ConnectionResetError", stream.error)

    def test_refused_resume_keeps_both_errors(self):
        reset = ConnectionResetError(10054, "reset")
        calls = []

        def opener(url, headers):
            calls.append(1)
            if len(calls) > 1:
                raise _HTTPError(403)
            return _Resp(self.PAYLOAD, fail_after=700, exc=reset)

        stream = M.YtdlStream("u", {}, opener=opener, chunk=8000).prime()
        self.assertEqual(len(self._drain(stream)), 700)
        self.assertIn("resume failed: HTTP 403", stream.error)


class AuditFormatTests(unittest.TestCase):
    def test_audit_row_names_the_format_and_mode(self):
        rows = []
        with mock.patch("core.audit.safe_activity", side_effect=lambda name, **f: rows.append(f)):
            M.audit_after_playback(None, None, exit_code=1, elapsed=0.1, stderr="Invalid data",
                                   stream_format="96/m3u8_native/WEB", mode="hls")
        self.assertEqual(rows[0]["outcome"], "error")
        self.assertEqual(rows[0]["format"], "96/m3u8_native/WEB")
        self.assertEqual(rows[0]["mode"], "hls")

    def test_format_label(self):
        self.assertEqual(M.format_label(_fmt("251", "https", c="TVHTML5")), "251/https/TVHTML5")


if __name__ == "__main__":
    unittest.main()
