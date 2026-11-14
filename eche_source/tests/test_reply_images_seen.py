# tests/test_reply_images_seen.py
# Owner request 2026-11-13 22:02 ET: a plain reply (no ?asset) to a message
# with pictures lets the chat model see them again, without the old false
# refusals. Eche's own asset outputs are not rechecked; anyone else's
# picture gets the hardcoded image check once, cached by sha256; the reply
# text gets no text filter. Only mocks and a solid-colour test picture.
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

from core import client as cl  # noqa: E402
from core import message_media as mm  # noqa: E402
from core import reply_images as ri  # noqa: E402
from core.asset_safety import AssetMode  # noqa: E402
from core.blocklist import REFUSAL  # noqa: E402

BOT_ID = 1486240665951142079
SFW = AssetMode(enabled=True, adult=False, approval=True, owner_only=False, user_cap=10, guild_cap=40, video=True)


def _jpeg(colour=(40, 90, 200)) -> bytes:
    # Distinct fake JPEG bytes per colour; no real picture is needed.
    return b"\xff\xd8\xff\xe0" + bytes(colour) * 16 + b"\xff\xd9"


class _Attachment:
    def __init__(self, data, name="pic.jpg", att_id=900):
        self.id = att_id
        self.filename = name
        self.content_type = "image/jpeg"
        self.size = len(data)
        self.url = f"https://cdn.discordapp.com/attachments/1/{att_id}/{name}?ex=6a&is=6b&hm=8f00"
        self.read = mock.AsyncMock(return_value=data)


def _reply(text, post):
    return SimpleNamespace(
        id=post.id + 1,
        content=text,
        author=SimpleNamespace(id=284007193181945857, name="archwigger", display_name="tech whore", bot=False),
        channel=SimpleNamespace(id=1097907374272020530, fetch_message=mock.AsyncMock(return_value=post)),
        guild=SimpleNamespace(id=1083466447164018738),
        reference=SimpleNamespace(resolved=post, message_id=post.id),
        attachments=[],
        embeds=[],
        stickers=[],
        message_snapshots=[],
        mentions=[],
    )


def _post(data, *, author_id=BOT_ID, att_id=900, content="Eche used *asset*!\nhoodie"):
    return SimpleNamespace(
        id=500,
        content=content,
        author=SimpleNamespace(id=author_id, bot=author_id == BOT_ID),
        attachments=[_Attachment(data, att_id=att_id)],
        embeds=[],
        stickers=[],
    )


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="eche-reply-images-")
        self.addCleanup(self._tmp.cleanup)
        for p in (
            mock.patch("core.secrets._user_root", return_value=self._tmp.name),
            mock.patch("core.reply_images._reply_mode", return_value=SFW),
            mock.patch("core.audit.record"),
            mock.patch("core.asset_safety._audit"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _clear(self, msg):
        media = asyncio.run(mm.collect_discord_media(msg))
        parts, withheld = asyncio.run(ri.cleared_reply_parts(media, msg))
        media.reply_parts = parts
        return media, parts, withheld


class EchePostTests(_Base):
    def test_reply_to_eches_asset_loads_the_picture_with_no_check(self):
        data = _jpeg()
        ri.record_sent_message(SimpleNamespace(attachments=[SimpleNamespace(id=900)]), data)
        boom = mock.AsyncMock(side_effect=AssertionError("Eche's own post must not be rechecked"))
        with mock.patch("core.classifier.classify_media_async", boom), \
                mock.patch("core.classifier.age_vision_async", boom):
            media, parts, withheld = self._clear(_reply("lol nice", _post(data)))
        self.assertEqual(len(parts), 1)
        self.assertFalse(withheld)
        self.assertEqual(media.images, [])
        self.assertTrue(parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_attachment_id_alone_is_enough(self):
        data = _jpeg((10, 200, 10))
        ri.record_eche_post(None, [901])
        boom = mock.AsyncMock(side_effect=AssertionError("rechecked"))
        with mock.patch("core.classifier.classify_media_async", boom), \
                mock.patch("core.classifier.age_vision_async", boom):
            _media, parts, _w = self._clear(_reply("what is this", _post(data, att_id=901)))
        self.assertEqual(len(parts), 1)

    def test_eche_post_that_was_not_an_asset_output_is_checked(self):
        data = _jpeg((200, 10, 10))
        classify = mock.AsyncMock(return_value="")
        age = mock.AsyncMock(return_value="")
        with mock.patch("core.classifier.classify_media_async", classify), \
                mock.patch("core.classifier.age_vision_async", age):
            _media, parts, _w = self._clear(_reply("huh", _post(data, att_id=902, content="image search result")))
        self.assertEqual(len(parts), 1)
        classify.assert_awaited_once()


class OtherUserTests(_Base):
    def test_other_users_picture_is_checked_once_then_cached(self):
        data = _jpeg((120, 120, 120))
        classify = mock.AsyncMock(return_value="")
        age = mock.AsyncMock(return_value="")
        with mock.patch("core.classifier.classify_media_async", classify), \
                mock.patch("core.classifier.age_vision_async", age):
            for _ in range(3):
                _media, parts, withheld = self._clear(_reply("what car is that", _post(data, author_id=77, att_id=950)))
                self.assertEqual(len(parts), 1)
                self.assertFalse(withheld)
        classify.assert_awaited_once()
        age.assert_awaited_once()
        # Pixels only: the checkers never get the reply text.
        for call in classify.await_args_list + age.await_args_list:
            self.assertNotIn("what car", repr(call))

    def test_blocked_picture_is_withheld_and_the_block_is_cached(self):
        data = _jpeg((1, 2, 3))
        classify = mock.AsyncMock(return_value="age")
        with mock.patch("core.classifier.classify_media_async", classify), \
                mock.patch("core.classifier.age_vision_async", mock.AsyncMock(return_value="")):
            for _ in range(2):
                _media, parts, withheld = self._clear(_reply("hm", _post(data, author_id=77, att_id=951)))
                self.assertEqual(parts, [])
                self.assertTrue(withheld)
        classify.assert_awaited_once()

    def test_checker_error_fails_closed_and_is_not_cached(self):
        data = _jpeg((9, 9, 9))
        classify = mock.AsyncMock(return_value="classifier-error")
        with mock.patch("core.classifier.classify_media_async", classify), \
                mock.patch("core.classifier.age_vision_async", mock.AsyncMock(return_value="")):
            for _ in range(2):
                _media, parts, withheld = self._clear(_reply("hm", _post(data, author_id=77, att_id=952)))
                self.assertEqual(parts, [])
                self.assertTrue(withheld)
        self.assertEqual(classify.await_count, 2)

    def test_age_vision_error_fails_closed(self):
        data = _jpeg((8, 8, 8))
        with mock.patch("core.classifier.classify_media_async", mock.AsyncMock(return_value="")), \
                mock.patch("core.classifier.age_vision_async", mock.AsyncMock(return_value="age-vision-error")):
            _media, parts, withheld = self._clear(_reply("hm", _post(data, author_id=77, att_id=953)))
        self.assertEqual(parts, [])
        self.assertTrue(withheld)

    def test_size_limit_still_applies(self):
        big = b"\xff\xd8\xff" + b"\x00" * (mm._MAX_IMAGE_BYTES + 10)
        media = asyncio.run(mm.collect_discord_media(_reply("hm", _post(big, author_id=77, att_id=954))))
        self.assertEqual(media.reply_images, [])


class ReplyTextIsNotFilteredTests(_Base):
    def test_put_more_clothes_on_her_gets_a_normal_answer(self):
        from cogs.events import on_message as om
        from core.client import ModelTurn

        data = _jpeg((30, 30, 60))
        ri.record_sent_message(SimpleNamespace(attachments=[SimpleNamespace(id=960)]), data)
        msg = _reply("put more clothes on her", _post(data, att_id=960))
        media, parts, _w = self._clear(msg)
        self.assertEqual(len(parts), 1)
        cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=BOT_ID)))
        sent: list[str] = []

        async def fake_send(message, content):
            sent.append(content)

        boom = mock.Mock(side_effect=AssertionError("reply text must not be filtered"))
        model = mock.AsyncMock(return_value=ModelTurn(reply="fine, hoodie on", thoughts=""))
        token = cl.set_precleared_images(parts)
        try:
            with mock.patch.object(om, "_send_reply", fake_send), \
                    mock.patch.object(om, "build_prompt", mock.AsyncMock(return_value="prompt")), \
                    mock.patch.object(om, "author_is_owner", mock.AsyncMock(return_value=True)), \
                    mock.patch.object(om, "admin_tools_enabled", lambda: False), \
                    mock.patch.object(om, "specs_for", lambda **_kw: []), \
                    mock.patch.object(om, "visible_names", lambda **_kw: set()), \
                    mock.patch.object(om, "remember_user", mock.AsyncMock()), \
                    mock.patch.object(om, "remember_bot", mock.AsyncMock()), \
                    mock.patch.object(cog, "_record_server_line", mock.AsyncMock()), \
                    mock.patch.object(om, "is_blocked", boom), \
                    mock.patch.object(om, "asks_to_bypass_filter", boom), \
                    mock.patch.object(om, "coaches_bypass", boom), \
                    mock.patch.object(om, "call_groq_turn", model):
                asyncio.run(cog._answer_and_remember(msg, None, msg.content, msg.content, media))
        finally:
            cl.reset_precleared_images(token)
        model.assert_awaited_once()
        self.assertEqual(model.await_args.kwargs.get("images"), parts)
        self.assertEqual(sent, ["fine, hoodie on"])
        self.assertNotIn(REFUSAL, sent)

    def test_outgoing_request_with_precleared_picture_runs_no_text_or_image_checks(self):
        data = _jpeg((50, 50, 50))
        part = mm.image_part(data, "image/jpeg")
        messages = [
            {"role": "system", "content": "You are Eche."},
            {"role": "user", "content": [
                {"type": "text", "text": "=== MOST RECENT MESSAGE ===\ntech whore: put more clothes on her"},
                part,
            ]},
        ]
        no_check = mock.AsyncMock(side_effect=AssertionError("no check on pre-cleared reply pictures"))
        token = cl.set_precleared_images([part])
        try:
            with mock.patch("core.classifier.classify_async", no_check), \
                    mock.patch("core.classifier.age_vision_async", no_check), \
                    mock.patch("core.blocklist.sanitize_messages", mock.Mock(side_effect=AssertionError("keyword scan"))):
                out = asyncio.run(cl._outgoing_messages_async(messages))
                self.assertTrue(out)
                self.assertFalse(cl._request_images.get())
        finally:
            cl.reset_precleared_images(token)

    def test_an_unmarked_picture_still_takes_the_image_path(self):
        data = _jpeg((60, 60, 60))
        part = mm.image_part(data, "image/jpeg")
        messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}, part]}]
        self.assertEqual(cl._unchecked_images(messages), [part])
        token = cl.set_precleared_images([part])
        try:
            self.assertEqual(cl._unchecked_images(messages), [])
        finally:
            cl.reset_precleared_images(token)
        self.assertEqual(cl._unchecked_images(messages), [part])


class AssetReplyStillEditsTests(unittest.TestCase):
    def test_asset_reply_is_the_command_only(self):
        from discord.ext import commands
        import discord
        from cogs.events import on_message as om
        from core.bot import strict_prefix

        bot = commands.Bot(command_prefix=strict_prefix, intents=discord.Intents.none())

        @bot.command(name="asset")
        async def asset(ctx, *, prompt: str = ""):
            return None

        bot._connection.user = SimpleNamespace(id=BOT_ID)
        cog = om.OnMessage(bot)
        msg = _reply("?asset put more clothes on her", _post(_jpeg()))
        collect = mock.AsyncMock(side_effect=AssertionError("chat path"))
        with mock.patch.object(om, "collect_discord_media", collect), \
                mock.patch.object(om, "maybe_boo", mock.AsyncMock(return_value=False)):
            asyncio.run(cog.on_message(msg))
        collect.assert_not_awaited()

    def test_asset_reply_plans_an_edit(self):
        from core import asset_source as SRC
        from core.imagine import plan_asset

        src = SRC.AssetSource(data=_jpeg(), mime="image/jpeg", kind="image")
        self.assertEqual(plan_asset("put more clothes on her", source=src, asset_mode=SFW).mode, "image_edit")


if __name__ == "__main__":
    unittest.main()
