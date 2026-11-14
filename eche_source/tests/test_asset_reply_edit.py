# tests/test_asset_reply_edit.py
# Reply targeting: "?asset <change>" sent as a reply to a picture or video
# edits that file. The source passes the child checks BEFORE any provider
# call; failures give the cap slot back. All media here are tiny synthetic
# byte strings and every checker / provider call is mocked.
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import asset_limits as L  # noqa: E402
from core import asset_safety as S  # noqa: E402
from core import asset_source as SRC  # noqa: E402
from core import imagine as IM  # noqa: E402
from core.asset_backend import XaiImagineBackend, IMAGE_MODEL, VIDEO_EDIT_MODEL  # noqa: E402
from core.asset_safety import AssetMode  # noqa: E402
from core.imagine import ImagineError, plan_asset, run_asset  # noqa: E402

BOT_ID = 1486240665951142079
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
SFW = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=3, guild_cap=10, video=False)
WITH_VIDEO = AssetMode(enabled=True, adult=False, approval=True, owner_only=False, user_cap=10, guild_cap=30, video=True)


class _Attachment:
    def __init__(self, name, data, ctype, size=None, url=None):
        self.filename = name
        self.content_type = ctype
        self.size = len(data) if size is None else size
        self.url = url or f"https://cdn.discordapp.com/attachments/1/2/{name}"
        self.read = mock.AsyncMock(return_value=data)


def _post(*attachments, embeds=()):
    return SimpleNamespace(id=500, content="*asset*!\nred car", author=SimpleNamespace(id=BOT_ID),
                           attachments=list(attachments), embeds=list(embeds))


def _reply_to(post, content="?asset make it blue"):
    return SimpleNamespace(id=501, content=content, reference=SimpleNamespace(resolved=post, message_id=500),
                           channel=SimpleNamespace(id=2, fetch_message=mock.AsyncMock(return_value=post)))


class SourceTests(unittest.TestCase):
    def _src(self, msg):
        return asyncio.run(SRC.source_from_reply(msg))

    def test_reply_to_eche_picture_gives_an_image_source(self):
        src = self._src(_reply_to(_post(_Attachment("asset.jpg", JPEG, "image/jpeg"))))
        self.assertEqual((src.kind, src.mime), ("image", "image/jpeg"))
        self.assertNotIn("data", repr(src).split("mime")[0].replace("AssetSource(", ""))

    def test_not_a_reply_or_no_media_is_a_new_asset(self):
        self.assertIsNone(self._src(SimpleNamespace(content="?asset car", reference=None)))
        self.assertIsNone(self._src(_reply_to(_post())))

    def test_type_and_size_limits(self):
        with self.assertRaises(ImagineError) as e:
            self._src(_reply_to(_post(_Attachment("a.gif", GIF, "image/gif"))))
        self.assertEqual(e.exception.text, SRC.UNSUPPORTED)
        big = _Attachment("big.png", PNG, "image/png", size=SRC.MAX_IMAGE_SOURCE_BYTES + 1)
        with self.assertRaises(ImagineError) as e:
            self._src(_reply_to(_post(big)))
        self.assertEqual(e.exception.text, SRC.TOO_BIG)
        big.read.assert_not_awaited()
        # Content that lies about its type is refused by its magic bytes.
        with self.assertRaises(ImagineError):
            self._src(_reply_to(_post(_Attachment("x.jpg", b"<html>nope</html>", "image/jpeg"))))
        with self.assertRaises(ImagineError):
            self._src(_reply_to(_post(_Attachment("x.mp4", MOV, "video/mp4"))))
        # Only Discord's media hosts.
        with self.assertRaises(ImagineError):
            self._src(_reply_to(_post(_Attachment("x.jpg", JPEG, "image/jpeg", url="https://evil.example/x.jpg"))))

    def test_video_length_limit_fails_closed(self):
        clip = _reply_to(_post(_Attachment("clip.mp4", MP4, "video/mp4")))
        with mock.patch.object(SRC, "video_seconds", return_value=12.0):
            with self.assertRaises(ImagineError) as e:
                self._src(clip)
            self.assertEqual(e.exception.text, SRC.TOO_LONG)
        with mock.patch.object(SRC, "video_seconds", return_value=None):
            with self.assertRaises(ImagineError):
                self._src(clip)
        with mock.patch.object(SRC, "video_seconds", return_value=6.0):
            self.assertEqual(self._src(clip).kind, "video")


class PlanAndRequestTests(unittest.TestCase):
    def _prepared(self):
        return SimpleNamespace(sent_prompt="make it blue\n[footer]", footer_hash="f")

    def test_image_reply_goes_to_the_image_edit_endpoint(self):
        src = SRC.AssetSource(data=JPEG, mime="image/jpeg", kind="image")
        plan = plan_asset("make it blue", source=src, asset_mode=SFW)
        self.assertEqual(plan.mode, "image_edit")
        path, body = XaiImagineBackend().build_request(plan, self._prepared())
        self.assertEqual(path, "/images/edits")
        self.assertEqual(body["model"], IMAGE_MODEL)
        self.assertTrue(body["image"]["url"].startswith("data:image/jpeg;base64,"))

    def test_video_reply_goes_to_the_video_edit_endpoint_only_when_video_is_allowed(self):
        src = SRC.AssetSource(data=MP4, mime="video/mp4", kind="video")
        with self.assertRaises(ImagineError):
            plan_asset("make it night", source=src, asset_mode=SFW)  # approval off: no video
        plan = plan_asset("make it night", source=src, asset_mode=WITH_VIDEO)
        self.assertEqual(plan.mode, "video_edit")
        path, body = XaiImagineBackend().build_request(plan, self._prepared())
        self.assertEqual((path, body["model"]), ("/videos/edits", VIDEO_EDIT_MODEL))
        self.assertTrue(body["video"]["url"].startswith("data:video/mp4;base64,"))
        self.assertNotIn("duration", body)

    def test_edit_needs_an_instruction(self):
        src = SRC.AssetSource(data=JPEG, mime="image/jpeg", kind="image")
        with self.assertRaises(ImagineError):
            plan_asset("", source=src, asset_mode=SFW)


class ChokePointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eche-edit-")
        self._patches = [
            mock.patch("core.secrets._user_root", return_value=self.tmp),
            mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}),
            mock.patch("core.asset_safety._audit"),
            mock.patch("core.asset_safety.require_enabled", lambda *a, **k: None),
            mock.patch("core.asset_safety.prepare_generation",
                       mock.AsyncMock(return_value=SimpleNamespace(sent_prompt="make it blue [footer]", footer_hash="f"))),
        ]
        for p in self._patches:
            p.start()
        self.src = SRC.AssetSource(data=JPEG, mime="image/jpeg", kind="image")
        self.plan = IM.AssetPlan(mode="image_edit", prompt="make it blue", source=self.src)

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _count(self):
        data = L._load(self.tmp)
        return len(L._prune(data.get("users", {}).get("7", []), L._now()))

    def _run(self, **kw):
        return asyncio.run(run_asset(self.plan, "key", user_id=7, guild_id=9, asset_mode=SFW, **kw))

    def test_source_child_check_blocks_before_any_edit_call(self):
        http = mock.Mock(side_effect=AssertionError("provider must not be called"))
        with mock.patch("core.classifier.classify_media_async", mock.AsyncMock(return_value="")), \
                mock.patch("core.classifier.age_vision_async", mock.AsyncMock(return_value="age-vision-yes")), \
                mock.patch("core.imagine.http_json", http):
            with self.assertRaises(ImagineError) as e:
                self._run()
        self.assertEqual(e.exception.text, S.SOURCE_REFUSAL)
        http.assert_not_called()
        self.assertEqual(self._count(), 0)  # refunded

    def test_source_check_error_fails_closed_before_any_edit_call(self):
        http = mock.Mock(side_effect=AssertionError("provider must not be called"))
        with mock.patch("core.classifier.classify_media_async", mock.AsyncMock(side_effect=RuntimeError("down"))), \
                mock.patch("core.imagine.http_json", http):
            with self.assertRaises(ImagineError):
                self._run()
        http.assert_not_called()
        self.assertEqual(self._count(), 0)

    def test_source_is_checked_with_the_channel_mode(self):
        age = mock.AsyncMock(return_value="age-vision-yes")
        with mock.patch("core.classifier.classify_media_async", mock.AsyncMock(return_value="")), \
                mock.patch("core.classifier.age_vision_async", age), \
                mock.patch("core.imagine.http_json", mock.Mock()):
            with self.assertRaises(ImagineError):
                asyncio.run(run_asset(self.plan, "key", user_id=7, guild_id=9,
                                      asset_mode=S.mode_for_channel(SFW, nsfw=False)))
        self.assertFalse(age.await_args.kwargs["adult"])

    def test_provider_failure_after_a_clean_source_refunds_the_slot(self):
        order = []
        check = mock.AsyncMock(side_effect=lambda *a, **k: order.append("source-check"))

        def boom(*a, **k):
            order.append("edit-call")
            raise ImagineError("That one was filtered.")
        with mock.patch("core.asset_safety.check_source_media", check), \
                mock.patch("core.imagine.http_json", side_effect=boom):
            with self.assertRaises(ImagineError):
                self._run()
        self.assertEqual(order, ["source-check", "edit-call"])
        self.assertEqual(self._count(), 0)

    def test_clean_edit_checks_the_output_and_keeps_the_slot(self):
        calls = []

        class R(tuple):
            def ids(self):
                return {}
        with mock.patch("core.asset_safety.check_source_media", mock.AsyncMock()), \
                mock.patch("core.imagine.http_json", return_value=R((200, {"data": [{"url": "https://imgen.x.ai/a.jpg"}]}))), \
                mock.patch("core.imagine.http_bytes", return_value=(JPEG, "image/jpeg")), \
                mock.patch("core.asset_safety.check_generated_media",
                           mock.AsyncMock(side_effect=lambda *a, **k: calls.append("output-check"))), \
                mock.patch("core.asset_backend.XaiImagineBackend.image_url", return_value="https://imgen.x.ai/a.jpg"):
            out = self._run()
        self.assertEqual(out.mode, "image_edit")
        self.assertEqual(calls, ["output-check"])
        self.assertEqual(self._count(), 1)

    def test_owner_edit_uses_no_slot_but_is_still_checked(self):
        http = mock.Mock(side_effect=AssertionError("provider must not be called"))
        with mock.patch("core.classifier.classify_media_async", mock.AsyncMock(return_value="classifier-unsafe")), \
                mock.patch("core.imagine.http_json", http):
            with self.assertRaises(ImagineError) as e:
                self._run(is_owner=True)
        self.assertEqual(e.exception.text, S.SOURCE_REFUSAL)
        http.assert_not_called()
        self.assertEqual(self._count(), 0)


class CogRoutingTests(unittest.TestCase):
    def test_asset_reply_to_a_picture_plans_an_edit(self):
        from cogs import asset as cog_mod

        post = _post(_Attachment("asset.jpg", JPEG, "image/jpeg"))
        msg = _reply_to(post, "?asset make it blue")
        msg.author = SimpleNamespace(id=7)
        msg.guild = SimpleNamespace(id=9)
        msg.channel.is_nsfw = lambda: False
        ctx = SimpleNamespace(message=msg, author=msg.author, guild=msg.guild, channel=msg.channel,
                              reply=mock.AsyncMock())
        seen = {}

        async def fake_run(plan, key, **kw):
            seen["plan"] = plan
            raise ImagineError("stop here")
        cog = cog_mod.Asset(SimpleNamespace())
        with mock.patch("core.admin_tools.author_is_owner", mock.AsyncMock(return_value=False)), \
                mock.patch.object(cog_mod, "refuse_speaker", lambda *a, **k: (k["out"].update(mode=SFW, owner=False) or None)), \
                mock.patch.object(cog_mod, "run_asset", fake_run), \
                mock.patch.object(cog_mod, "api_key", lambda: "key"):
            asyncio.run(cog_mod.Asset.asset.callback(cog, ctx, prompt="make it blue"))
        self.assertEqual(seen["plan"].mode, "image_edit")
        self.assertEqual(seen["plan"].source.mime, "image/jpeg")


if __name__ == "__main__":
    unittest.main()
