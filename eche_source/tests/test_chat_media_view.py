"""Media Eche only views for chat: no child/age check, better reply reading,
video/GIF frames. Owner decision 2026-11-18. Mock-only; synthetic solid
colours and placeholder bytes, no pictures of people.
"""

import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from core import chat_media as cm
from core import message_media as mm


def run(c):
    return asyncio.run(c)


def png(size=(1600, 900), colour=(20, 120, 200)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


class Att:
    def __init__(self, data, name="pic.png", ctype="image/png", att_id=1):
        self.id = att_id
        self.filename = name
        self.content_type = ctype
        self.size = len(data)
        self.url = f"https://cdn.example/{att_id}/{name}"
        self.read = mock.AsyncMock(return_value=data)


def msg(content="", attachments=(), embeds=(), ref=None, snaps=(), mid=10):
    return SimpleNamespace(
        id=mid, content=content, attachments=list(attachments), embeds=list(embeds),
        stickers=[], message_snapshots=list(snaps),
        reference=(SimpleNamespace(resolved=ref, message_id=ref.id) if ref else None),
        channel=SimpleNamespace(id=5, fetch_message=mock.AsyncMock(return_value=ref)),
        author=SimpleNamespace(id=7), guild=SimpleNamespace(id=8),
    )


class Base(unittest.TestCase):
    def setUp(self):
        cm._by_url.clear()
        cm._by_sha.clear()
        cm._frames_by_sha.clear()
        p = mock.patch.object(mm, "_EMBED_WAIT_S", 0)
        p.start()
        self.addCleanup(p.stop)


class NoChildCheckOnViewedMedia(Base):
    def test_reply_and_own_pictures_skip_checks_and_are_logged(self):
        boom = mock.AsyncMock(side_effect=AssertionError("no check on viewed media"))
        rows = []
        replied = msg("look", [Att(png(), att_id=2)], mid=1)
        m = msg("what is this", [Att(png(colour=(9, 9, 9)), att_id=3)], ref=replied, mid=2)
        with mock.patch("core.reply_images.clear_reply_image", boom), \
             mock.patch("core.classifier.classify_media_async", boom), \
             mock.patch("core.classifier.age_vision_async", boom), \
             mock.patch("core.audit.record", side_effect=lambda ev, **kw: rows.append((ev, kw))):
            media = run(mm.collect_discord_media(m))
            cm.prepare_viewed(media, m)
        self.assertEqual(len(media.images), 1)
        self.assertEqual(len(media.reply_parts), 1)
        row = [kw for ev, kw in rows if ev == "chat-media"][0]
        self.assertTrue(row["safety"])
        self.assertEqual(row["count"], "2")
        self.assertNotIn("http", repr(row))
        self.assertNotIn("data:", repr(row))

    def test_failed_audit_row_drops_media(self):
        replied = msg("x", [Att(png(), att_id=4)], mid=1)
        m = msg("?", ref=replied, mid=2)
        media = run(mm.collect_discord_media(m))
        with mock.patch("core.audit.record", side_effect=OSError("disk")):
            cm.prepare_viewed(media, m)
        self.assertEqual(media.reply_parts, [])

    def test_viewed_parts_are_precleared_so_client_runs_no_image_check(self):
        from core import client as cl

        part = mm.image_part(png((10, 10)), "image/png")
        no_check = mock.AsyncMock(side_effect=AssertionError("checked"))
        messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}, part]}]
        token = cl.set_precleared_images([part])
        try:
            with mock.patch("core.classifier.classify_async", no_check):
                run(cl._outgoing_messages_async(messages))
        finally:
            cl.reset_precleared_images(token)

    def test_on_message_no_longer_uses_reply_check(self):
        from pathlib import Path

        src = (Path(mm.__file__).resolve().parent.parent / "cogs" / "events" / "on_message.py").read_text(encoding="utf-8")
        self.assertNotIn("cleared_reply_parts", src)
        self.assertIn("prepare_viewed", src)

    def test_asset_edit_source_check_unchanged(self):
        from pathlib import Path

        src = (Path(mm.__file__).resolve().parent.parent / "core" / "imagine.py").read_text(encoding="utf-8")
        self.assertIn("await check_source_media(", src)


class ReplyReading(Base):
    def test_large_picture_is_read_and_shrunk(self):
        big = png((4000, 3000))
        att = Att(big + b"\x00" * 1_500_000, att_id=11)
        part = run(mm._read_image(att, set()))
        self.assertIsNotNone(part)
        from PIL import Image
        self.assertLessEqual(max(Image.open(io.BytesIO(part["data"])).size), cm.IMAGE_EDGE)

    def test_unscalable_big_picture_is_not_sent(self):
        att = Att(b"\xff\xd8\xff" + b"\x00" * 2_000_000, name="x.jpg", ctype="image/jpeg", att_id=15)
        errors = []
        with mock.patch.object(cm, "shrink", side_effect=lambda d, m, e=1024: (d, m)):
            self.assertIsNone(run(mm._read_image(att, set(), errors)))
        self.assertEqual(errors, ["too-large"])

    def test_over_20mb_is_too_large_note(self):
        att = Att(b"x", att_id=12)
        att.size = cm.MAX_IMAGE_DOWNLOAD + 1
        replied = msg("x", [att], mid=1)
        media = run(mm.collect_discord_media(msg("?", ref=replied, mid=2)))
        self.assertIn(cm.TOO_LARGE_NOTE, media.text)

    def test_attachment_read_retried_once(self):
        att = Att(png((20, 20)), att_id=13)
        att.read = mock.AsyncMock(side_effect=[RuntimeError("cdn"), png((20, 20))])
        self.assertIsNotNone(run(mm._read_image(att, set())))
        att2 = Att(b"", att_id=14)
        att2.read = mock.AsyncMock(side_effect=RuntimeError("cdn"))
        errors = []
        self.assertIsNone(run(mm._read_image(att2, set(), errors)))
        self.assertEqual(att2.read.await_count, 2)
        self.assertEqual(errors, ["fetch"])

    def test_reply_embeds_links_and_forwards_are_read(self):
        fetched = []

        async def fake_fetch(url, limit):
            fetched.append(url)
            return png((30, 30)), "image/png", ""

        embed = SimpleNamespace(title="", description="", url="", fields=[], footer=None, author=None,
                                image=SimpleNamespace(proxy_url="https://media.example/card.png", url=""),
                                thumbnail=None, video=None)
        fwd = SimpleNamespace(content="", attachments=[Att(png((15, 15)), att_id=21)], embeds=[], stickers=[])
        replied = msg("see https://i.example/pic.jpg ok", embeds=[embed], snaps=[fwd], mid=1)
        with mock.patch.object(cm, "fetch", fake_fetch):
            media = run(mm.collect_discord_media(msg("?", ref=replied, mid=2)))
        self.assertIn("https://media.example/card.png", fetched)
        self.assertIn("https://i.example/pic.jpg", fetched)
        self.assertEqual(len(media.reply_images), 3)

    def test_fetch_error_note_differs_from_block(self):
        async def bad(url, limit):
            return b"", "", "fetch"

        replied = msg("https://i.example/a.png", mid=1)
        with mock.patch.object(cm, "fetch", bad):
            media = run(mm.collect_discord_media(msg("?", ref=replied, mid=2)))
        self.assertIn(cm.FETCH_ERROR_NOTE, media.text)
        self.assertNotIn("did not pass", media.text)

    def test_fetch_retries_once_and_caches(self):
        calls = {"n": 0}

        class Resp:
            def __init__(self, status):
                self.status = status
                self.headers = {"Content-Type": "image/png"}
                self.content = SimpleNamespace(read=mock.AsyncMock(return_value=b"PNGDATA"))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class Session:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def get(self, url, headers=None):
                calls["n"] += 1
                return Resp(503 if calls["n"] == 1 else 200)

        with mock.patch.object(cm.aiohttp, "ClientSession", Session):
            data, mime, err = run(cm.fetch("https://x/a.png", 100))
            self.assertEqual((data, err), (b"PNGDATA", ""))
            self.assertEqual(calls["n"], 2)
            run(cm.fetch("https://x/a.png", 100))
            self.assertEqual(calls["n"], 2)  # cached


class VideoFrames(Base):
    def test_pick_frames(self):
        self.assertEqual(cm.pick_frames(241), [0, 48, 96, 144, 192, 240])
        self.assertEqual(cm.pick_frames(3), [0, 1, 2])
        self.assertEqual(cm.pick_frames(0), [])

    def test_video_sampled_capped_and_cached(self):
        frames = [png((1280, 720), (i, i, i)) for i in range(300)]
        with mock.patch("core.reply_images.chat_video_frames", return_value=frames) as ex:
            out, note = run(cm.video_frames(b"vid", "video/mp4"))
            run(cm.video_frames(b"vid", "video/mp4"))
        self.assertEqual(ex.call_count, 1)
        self.assertEqual(len(out), 6)
        self.assertIn("0.0s", note)
        self.assertIn("60.0s", note)  # capped at 60 s
        self.assertIn("no audio", note)
        from PIL import Image
        self.assertLessEqual(max(Image.open(io.BytesIO(out[0]["data"])).size), cm.FRAME_EDGE)

    def test_reply_video_goes_to_chat_as_frames(self):
        frames = [png((64, 64), (i, 0, 0)) for i in range(20)]
        vid = Att(b"\x00\x00\x00 ftypmp4", name="clip.mp4", ctype="video/mp4", att_id=31)
        replied = msg("", [vid], mid=1)
        with mock.patch("core.reply_images.chat_video_frames", return_value=frames):
            media = run(mm.collect_discord_media(msg("what happens", ref=replied, mid=2)))
        self.assertEqual(len(media.video_parts), 6)
        self.assertIn("video frames in order", media.text)

    def test_tenor_embed_video_is_watched(self):
        frames = [png((64, 64)) for _ in range(8)]
        embed = SimpleNamespace(title="", description="", url="https://tenor.com/view/x", fields=[], footer=None,
                                author=None, image=None, thumbnail=SimpleNamespace(proxy_url="https://t/x.png", url=""),
                                video=SimpleNamespace(proxy_url="https://media.tenor.com/x.mp4", url=""))

        async def fake_fetch(url, limit):
            self.assertEqual(limit, cm.MAX_VIDEO_DOWNLOAD)
            return b"mp4bytes", "video/mp4", ""

        replied = msg("", embeds=[embed], mid=1)
        with mock.patch.object(cm, "fetch", fake_fetch), \
             mock.patch("core.reply_images.chat_video_frames", return_value=frames):
            media = run(mm.collect_discord_media(msg("lol", ref=replied, mid=2)))
        self.assertEqual(len(media.video_parts), 6)

    def test_frame_failure_uses_fetch_note(self):
        vid = Att(b"bad", name="clip.mp4", ctype="video/mp4", att_id=32)
        with mock.patch("core.reply_images.chat_video_frames", return_value=[]):
            media = run(mm.collect_discord_media(msg("?", ref=msg("", [vid], mid=1), mid=2)))
        self.assertEqual(media.video_parts, [])
        self.assertIn(cm.FETCH_ERROR_NOTE, media.text)


if __name__ == "__main__":
    unittest.main()
