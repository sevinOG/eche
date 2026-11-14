# tests/test_chat_tool_edits_reply.py
# Live test 2026-11-13 22:11 / 22:16 ET: plain replies to Eche asset posts
# ("@eche give her smaller breasts but a larger ass", "Give her even bigger
# breasts") got "Can't touch it this turn. Reply to that pic with ?asset ...".
# Root cause: the asset tool's description told the model that editing a
# replied-to file was not available, and the system prompt talked about the
# tool not being offered. Now the chat tool edits the replied-to file through
# the same path as ?asset sent as a reply. Mocks only; no media is generated.
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
from core import asset_source as SRC  # noqa: E402
from core import imagine as IM  # noqa: E402
from core import prompt_text  # noqa: E402
from core import reply_images as ri  # noqa: E402
from core import tools_asset  # noqa: E402
from core.asset_safety import AssetMode  # noqa: E402
from core.imagine import AssetFile, ImagineError, run_asset  # noqa: E402
from core.tools import ToolContext  # noqa: E402

BOT_ID = 1486240665951142079
JPEG = b"\xff\xd8\xff\xe0" + b"\x11" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
SFW = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=10, guild_cap=40,
                video=True, video_user_cap=5, video_guild_cap=15)


class _Attachment:
    def __init__(self, name, data, ctype, att_id=700):
        self.id = att_id
        self.filename = name
        self.content_type = ctype
        self.size = len(data)
        self.url = f"https://cdn.discordapp.com/attachments/1/{att_id}/{name}?ex=6a&is=6b&hm=8f00"
        self.read = mock.AsyncMock(return_value=data)


def _post(att):
    return SimpleNamespace(id=500, content="Eche used *asset*!\nbeach outfit", author=SimpleNamespace(id=BOT_ID, bot=True),
                           attachments=[att], embeds=[], stickers=[])


def _reply(text, post):
    return SimpleNamespace(
        id=501,
        content=text,
        author=SimpleNamespace(id=284007193181945857, name="archwigger", display_name="tech whore", bot=False),
        guild=SimpleNamespace(id=9),
        channel=SimpleNamespace(id=2, fetch_message=mock.AsyncMock(return_value=post)),
        reference=SimpleNamespace(resolved=post, message_id=500),
        attachments=[],
        embeds=[],
        stickers=[],
        message_snapshots=[],
        mentions=[SimpleNamespace(id=BOT_ID)],
    )


def _gate(*_a, **k):
    k["out"].update(mode=SFW, owner=False)
    return None


class ChatToolEditTests(unittest.TestCase):
    def _call(self, text, args, *, post=None, run=None):
        post = post or _post(_Attachment("asset.jpg", JPEG, "image/jpeg"))
        msg = _reply(text, post)
        ctx = ToolContext(bot=SimpleNamespace(get_command=lambda name: None), message=msg)
        run = run or mock.AsyncMock(return_value=AssetFile(data=JPEG, mime="image/jpeg", mode="image_edit", filename="asset.jpg"))
        posted = mock.AsyncMock()
        with mock.patch("core.admin_tools.author_is_owner", mock.AsyncMock(return_value=False)), \
                mock.patch.object(tools_asset, "refuse_speaker", _gate), \
                mock.patch.object(tools_asset, "run_asset", run), \
                mock.patch.object(tools_asset, "api_key", lambda: "key"), \
                mock.patch.object(tools_asset, "require_owner_review", mock.AsyncMock(side_effect=lambda r, **k: r)), \
                mock.patch.object(tools_asset, "confirm_asset_delivery", lambda *a, **k: None), \
                mock.patch.object(tools_asset, "require_enabled", lambda *a, **k: None), \
                mock.patch.object(tools_asset, "_post", posted), \
                mock.patch("core.asset_review.audit_posted", lambda **k: None):
            result = asyncio.run(tools_asset.make_asset(ctx, args))
        return result, run, posted

    def test_plain_reply_change_request_is_one_edit_of_the_replied_to_file(self):
        result, run, posted = self._call(
            "@eche give her smaller breasts but a larger ass",
            {"prompt": "give her smaller breasts but a larger ass"},
        )
        run.assert_awaited_once()
        plan = run.await_args.args[0]
        self.assertEqual(plan.mode, "image_edit")
        self.assertEqual(plan.source.data, JPEG)
        self.assertEqual(plan.source.attachment_id, 700)
        self.assertEqual(plan.prompt, "give her smaller breasts but a larger ass")
        posted.assert_awaited_once()
        self.assertTrue(result.quiet)

    def test_kind_from_the_model_does_not_turn_an_edit_into_a_new_file(self):
        _result, run, _posted = self._call("Give her even bigger breasts", {"prompt": "even bigger breasts", "kind": "image"})
        self.assertEqual(run.await_args.args[0].mode, "image_edit")

    def test_reply_to_a_video_is_a_video_edit(self):
        post = _post(_Attachment("clip.mp4", MP4, "video/mp4"))
        with mock.patch.object(SRC, "video_seconds", return_value=6.0):
            _result, run, _posted = self._call("make it night", {"prompt": "make it night"}, post=post)
        self.assertEqual(run.await_args.args[0].mode, "video_edit")

    def test_not_a_reply_is_a_new_picture(self):
        msg = _reply("make a red car", _post(_Attachment("a.jpg", JPEG, "image/jpeg")))
        msg.reference = None
        ctx = ToolContext(bot=SimpleNamespace(get_command=lambda name: None), message=msg)
        run = mock.AsyncMock(return_value=AssetFile(data=JPEG, mime="image/jpeg", mode="image", filename="asset.jpg"))
        with mock.patch("core.admin_tools.author_is_owner", mock.AsyncMock(return_value=False)), \
                mock.patch.object(tools_asset, "refuse_speaker", _gate), \
                mock.patch.object(tools_asset, "run_asset", run), \
                mock.patch.object(tools_asset, "api_key", lambda: "key"), \
                mock.patch.object(tools_asset, "require_owner_review", mock.AsyncMock(side_effect=lambda r, **k: r)), \
                mock.patch.object(tools_asset, "confirm_asset_delivery", lambda *a, **k: None), \
                mock.patch.object(tools_asset, "require_enabled", lambda *a, **k: None), \
                mock.patch.object(tools_asset, "_post", mock.AsyncMock()), \
                mock.patch("core.asset_review.audit_posted", lambda **k: None):
            asyncio.run(tools_asset.make_asset(ctx, {"prompt": "a red car"}))
        self.assertEqual(run.await_args.args[0].mode, "image")
        self.assertIsNone(run.await_args.args[0].source)


class EditPathTests(unittest.TestCase):
    """The shared edit choke point (?asset reply and chat tool alike)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eche-chat-edit-")
        self._patches = [
            mock.patch("core.secrets._user_root", return_value=self.tmp),
            mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}),
            mock.patch("core.asset_safety._audit"),
            mock.patch("core.asset_safety.require_enabled", lambda *a, **k: None),
            mock.patch("core.asset_safety.prepare_generation",
                       mock.AsyncMock(return_value=SimpleNamespace(sent_prompt="bigger [footer]", footer_hash="f"))),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self._patches)])

    def _plan(self, att_id=None):
        src = SRC.AssetSource(data=JPEG, mime="image/jpeg", kind="image", attachment_id=att_id)
        return IM.AssetPlan(mode="image_edit", prompt="bigger", source=src)

    def _count(self, bucket="users", key="7"):
        return len(L._prune(L._load(self.tmp).get(bucket, {}).get(key, []), L._now()))

    def test_eches_own_output_is_not_rechecked_as_a_source(self):
        ri.record_eche_post(None, [700])
        check = mock.AsyncMock(side_effect=AssertionError("own output rechecked"))
        with mock.patch("core.asset_safety.check_source_media", check), \
                mock.patch("core.imagine.http_json", side_effect=ImagineError("provider stub")):
            with self.assertRaises(ImagineError) as e:
                asyncio.run(run_asset(self._plan(700), "key", user_id=7, guild_id=9, asset_mode=SFW))
        self.assertEqual(e.exception.text, "provider stub")
        check.assert_not_awaited()

    def test_someone_elses_file_is_checked_and_a_block_refunds_the_slot(self):
        check = mock.AsyncMock(side_effect=ImagineError("That source was filtered."))
        http = mock.Mock(side_effect=AssertionError("provider must not be called"))
        with mock.patch("core.asset_safety.check_source_media", check), \
                mock.patch("core.imagine.http_json", http):
            with self.assertRaises(ImagineError):
                asyncio.run(run_asset(self._plan(999), "key", user_id=7, guild_id=9, asset_mode=SFW))
        check.assert_awaited_once()
        self.assertEqual(self._count(), 0)

    def test_image_edit_uses_the_image_cap(self):
        for _ in range(10):
            L.record_use(7, 9, root=self.tmp)
        with self.assertRaises(ImagineError) as e:
            asyncio.run(run_asset(self._plan(), "key", user_id=7, guild_id=9, asset_mode=SFW))
        self.assertTrue(e.exception.text.startswith(L.USER_CAP_REFUSAL), e.exception.text)

    def test_owner_edit_uses_no_slot(self):
        check = mock.AsyncMock()
        with mock.patch("core.asset_safety.check_source_media", check), \
                mock.patch("core.imagine.http_json", side_effect=ImagineError("provider stub")):
            with self.assertRaises(ImagineError):
                asyncio.run(run_asset(self._plan(), "key", user_id=7, guild_id=9, asset_mode=SFW, is_owner=True))
        check.assert_awaited_once()
        self.assertEqual(self._count(), 0)


class PromptTests(unittest.TestCase):
    def test_no_not_live_text_anywhere_the_model_reads(self):
        texts = [prompt_text._PICTURES_ON, prompt_text.TOOL_LINES["asset"], tools_asset._DESCRIPTION]
        for text in texts:
            low = text.casefold()
            for bad in ("not live", "isn't live", "not available", "not offered", "this turn"):
                self.assertNotIn(bad, low, text)
        self.assertIn("replied-to", tools_asset._DESCRIPTION)
        self.assertIn("call the asset tool", prompt_text._PICTURES_ON)

    def test_asset_tool_is_offered_on_reply_turns(self):
        from core.tools import specs_for

        with mock.patch("core.asset_safety.asset_enabled", return_value=True), \
                mock.patch("core.tools.default_tools_enabled", return_value=True), \
                mock.patch("core.tools.tool_prompt_enabled", return_value=True):
            names = {spec["function"]["name"] for spec in specs_for(speaker_is_owner=False)}
        self.assertIn("asset", names)


class QuestionReplyTests(unittest.TestCase):
    def test_question_about_the_image_gets_a_normal_chat_answer(self):
        from cogs.events import on_message as om
        from core.client import ModelTurn

        post = _post(_Attachment("asset.jpg", JPEG, "image/jpeg"))
        msg = _reply("what is she wearing?", post)
        media = SimpleNamespace(text="Replied-to message:\nasset.jpg", memory="", summary="asset.jpg",
                                images=[], reply_parts=[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}}],
                                reply_images=[], message=msg)
        cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=BOT_ID)))
        sent: list[str] = []

        async def fake_send(message, content):
            sent.append(content)

        model = mock.AsyncMock(return_value=ModelTurn(reply="a red bikini", thoughts=""))
        tool_run = mock.AsyncMock(side_effect=AssertionError("no tool for a question"))
        with mock.patch.object(om, "_send_reply", fake_send), \
                mock.patch.object(om, "build_prompt", mock.AsyncMock(return_value="prompt")), \
                mock.patch.object(om, "author_is_owner", mock.AsyncMock(return_value=False)), \
                mock.patch.object(om, "admin_tools_enabled", lambda: False), \
                mock.patch.object(om, "specs_for", lambda **_kw: [{"type": "function", "function": {"name": "asset"}}]), \
                mock.patch.object(om, "visible_names", lambda **_kw: {"asset"}), \
                mock.patch.object(om, "remember_user", mock.AsyncMock()), \
                mock.patch.object(om, "remember_bot", mock.AsyncMock()), \
                mock.patch.object(cog, "_record_server_line", mock.AsyncMock()), \
                mock.patch.object(tools_asset, "make_asset", tool_run), \
                mock.patch.object(om, "call_groq_turn", model):
            asyncio.run(cog._answer_and_remember(msg, None, msg.content, msg.content, media))
        self.assertEqual(sent, ["a red bikini"])
        tools = model.await_args.kwargs.get("tools") or []
        self.assertIn("asset", {t["function"]["name"] for t in tools})


if __name__ == "__main__":
    unittest.main()
