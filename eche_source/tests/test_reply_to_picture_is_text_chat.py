# tests/test_reply_to_picture_is_text_chat.py
# Live bug 2026-11-13 20:47 ET (#ai-porn): the owner replied "put more
# clothes on her" to Eche's own asset post (a clothed adult). The replied-to
# picture was loaded into the turn, so the text reply took the image path,
# age vision ran on Eche's own post, and the reply was "Nope. Not going
# there." (llm-input-age). A text reply to a picture is text chat.
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

from core import client as cl  # noqa: E402
from core import message_media as mm  # noqa: E402
from core.blocklist import REFUSAL  # noqa: E402

BOT_ID = 1486240665951142079
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class _Attachment:
    def __init__(self, name="asset.jpg"):
        self.filename = name
        self.content_type = "image/jpeg"
        self.size = len(JPEG)
        self.url = f"https://cdn.example/{name}"
        self.read = mock.AsyncMock(return_value=JPEG)


def _asset_post():
    return SimpleNamespace(
        id=500,
        content="*asset*!\nhuge titties sfw",
        author=SimpleNamespace(id=BOT_ID, bot=True),
        attachments=[_Attachment()],
        embeds=[],
        stickers=[],
    )


def _owner_reply(attachments=()):
    post = _asset_post()
    return SimpleNamespace(
        id=501,
        content="put more clothes on her",
        author=SimpleNamespace(id=284007193181945857, name="archwigger", display_name="tech whore"),
        channel=SimpleNamespace(id=1097907374272020530, fetch_message=mock.AsyncMock(return_value=post)),
        guild=SimpleNamespace(id=1083466447164018738),
        reference=SimpleNamespace(resolved=post, message_id=500),
        attachments=list(attachments),
        embeds=[],
        stickers=[],
        message_snapshots=[],
        to_reference=lambda **_kw: None,
    ), post


class ReplyMediaTests(unittest.TestCase):
    def test_reply_to_asset_post_loads_no_pictures(self):
        msg, post = _owner_reply()
        media = asyncio.run(mm.collect_discord_media(msg))
        self.assertEqual(media.images, [])
        post.attachments[0].read.assert_not_awaited()
        # The model still knows what was replied to.
        self.assertIn("Replied-to message", media.text)
        self.assertIn("asset.jpg", media.text)
        self.assertIn("If you cannot see an image, say so", media.text)

    def test_own_attached_picture_still_takes_the_image_path(self):
        msg, _post = _owner_reply(attachments=[_Attachment("mine.jpg")])
        media = asyncio.run(mm.collect_discord_media(msg))
        self.assertEqual(len(media.images), 1)


class ReplyIsTextChatTests(unittest.TestCase):
    def test_owner_reply_is_answered_without_any_filter(self):
        from cogs.events import on_message as om
        from core.client import ModelTurn

        cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=BOT_ID)))
        sent: list[str] = []

        async def fake_send(message, content):
            sent.append(content)

        msg, _post = _owner_reply()
        media = asyncio.run(mm.collect_discord_media(msg))
        boom = mock.Mock(side_effect=AssertionError("text chat must not be filtered"))
        model = mock.AsyncMock(return_value=ModelTurn(reply="fine, hoodie on", thoughts=""))
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
                mock.patch.object(om, "call_groq_turn", model):
            asyncio.run(cog._answer_and_remember(msg, None, msg.content, msg.content, media))
        model.assert_awaited_once()
        self.assertEqual(sent, ["fine, hoodie on"])
        self.assertNotIn(REFUSAL, sent)

    def test_outgoing_text_with_reply_note_runs_no_checks(self):
        msg, _post = _owner_reply()
        media = asyncio.run(mm.collect_discord_media(msg))
        messages = [
            {"role": "system", "content": "You are Eche."},
            {"role": "user", "content": f"{media.text}\n\n=== MOST RECENT MESSAGE ===\ntech whore: put more clothes on her"},
        ]
        no_check = mock.AsyncMock(side_effect=AssertionError("no classifier on text chat"))
        with mock.patch("core.classifier.classify_async", no_check), \
                mock.patch("core.classifier.age_vision_async", no_check):
            out = asyncio.run(cl._outgoing_messages_async(messages))
        self.assertTrue(out)


if __name__ == "__main__":
    unittest.main()
