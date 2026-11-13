# tests/test_chat_panel.py
# Every inbound user message and every outbound bot reply must reach the
# GUI chat panel model, across the Discord send paths.
#
# Run from eche_source:  python -m unittest tests.test_chat_panel -v

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from core import blocklist as bl  # noqa: E402
from core import chat_panel as cp  # noqa: E402
from core import guardrails as gr  # noqa: E402
from core import gui_bridge  # noqa: E402
from core.client import ModelTurn as _ModelTurn  # noqa: E402
from core.client import discord_chunks  # noqa: E402
from core.tools import ToolResult, format_tool_messages  # noqa: E402


def ModelTurn(reply="", tool_calls=None, **kw):
    kw.setdefault("thoughts", "")
    return _ModelTurn(reply=reply, tool_calls=tool_calls or [], **kw)


def run(coro):
    import asyncio

    return asyncio.run(coro)


def encode_event(event, data=None, sentinel=None):
    """One stdout line as emit() would write it, including the sentinel prefix."""
    import json

    token = sentinel if sentinel is not None else gui_bridge.get_sentinel()
    return token + json.dumps({"event": event, "data": data or {}}, ensure_ascii=False)


class _Recorder:
    def __init__(self):
        self.model = cp.ChatPanelModel()
        self.sent: list[str] = []

    def attach(self):
        cp.set_handler(self.model.append)

    def detach(self):
        cp.set_handler(None)


def _message(text="hello", *, display_name="Alice", channel=None):
    sent = []

    async def fake_send(content=None, **_kw):
        body = "" if content is None else str(content)
        sent.append(body)
        return SimpleNamespace(id=1, content=body, edit=mock.AsyncMock(), delete=mock.AsyncMock())

    if channel is None:
        channel = SimpleNamespace(id=99, send=fake_send)
        channel._sent = sent
    author = SimpleNamespace(id=42, name="alice", display_name=display_name)
    message = SimpleNamespace(
        content=text,
        author=author,
        channel=channel,
        guild=SimpleNamespace(id=7, name="srv"),
        attachments=[],
        to_reference=lambda **_kw: "ref",
    )
    return message, channel


class ChatPanelSetup(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="eche-panel-")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ECHE_")}
        env["ECHE_USER_ROOT"] = self.root
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        self._sentinel = gui_bridge.make_sentinel()
        os.environ[gui_bridge.SENTINEL_ENV] = self._sentinel
        gui_bridge.set_sentinel(self._sentinel)
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        bl.clear_recent_blocks()
        self.rec = _Recorder()
        self.rec.attach()

    def tearDown(self):
        self.rec.detach()
        gui_bridge.reset_sentinel()
        self._env.stop()
        shutil.rmtree(self.root, ignore_errors=True)
        bl.clear_recent_blocks()


class ModelAndEmitTests(ChatPanelSetup):
    def test_inbound_and_outbound_share_one_emit_point(self):
        seen: list[str] = []
        original = cp.emit_line

        def wrap(text):
            seen.append(text)
            return original(text)

        with mock.patch.object(cp, "emit_line", wrap):
            cp.inbound("Alice", "hello")
            cp.outbound("hi there")
        self.assertEqual(seen, ["Alice: hello", "Bot: hi there"])
        self.assertEqual(self.rec.model.lines(), ["Alice: hello", "Bot: hi there"])

    def test_empty_lines_are_dropped(self):
        self.assertEqual(cp.inbound("Alice", ""), "Alice: ")
        self.assertEqual(cp.outbound("   "), "")
        self.assertEqual(cp.emit_line("\n"), "")
        self.assertEqual(self.rec.model.lines(), ["Alice: "])

    def test_handler_exception_does_not_drop_later_lines(self):
        boom = {"n": 0}

        def flaky(line):
            boom["n"] += 1
            if boom["n"] == 1:
                raise RuntimeError("panel paint failed")
            self.rec.model.append(line)

        cp.set_handler(flaky)
        cp.outbound("first")
        cp.outbound("second")
        self.assertEqual(self.rec.model.lines(), ["Bot: second"])

    def test_model_is_thread_safe(self):
        model = self.rec.model

        def writer(prefix):
            for i in range(50):
                model.append(f"{prefix}-{i}")

        threads = [threading.Thread(target=writer, args=(name,)) for name in ("a", "b", "c")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = model.lines()
        self.assertEqual(len(lines), 150)
        self.assertEqual(len(set(lines)), 150)

    def test_parse_bridge_line_rejects_dirty_prefix(self):
        import json

        payload = '{"event": "chat", "data": {"text": "Bot: hello 👋"}}'
        self.assertEqual(
            gui_bridge.parse_bridge_line("WARN lib: " + payload + " trailing"),
            (None, {}),
        )
        # A complete event object with no sentinel is rejected.
        self.assertEqual(gui_bridge.parse_bridge_line(payload), (None, {}))
        self.assertEqual(gui_bridge.parse_bridge_line("not json"), (None, {}))
        self.assertEqual(gui_bridge.parse_bridge_line(""), (None, {}))
        # Leading space before the sentinel does not count as a prefix match.
        tagged = encode_event("chat", {"text": "Bot: hello 👋"})
        self.assertEqual(gui_bridge.parse_bridge_line(" " + tagged), (None, {}))
        self.assertEqual(
            gui_bridge.parse_bridge_line("WARN lib: " + tagged),
            (None, {}),
        )
        # A clean emit() line still parses, including when the chat text
        # itself contains a spoofed event object.
        inner = '{"event": "fatal", "data": {"message": "pwned"}}'
        clean = encode_event("chat", {"text": "Alice: " + inner})
        event, data = gui_bridge.parse_bridge_line(clean)
        self.assertEqual(event, "chat")
        self.assertEqual(data.get("text"), "Alice: " + inner)
        # Two objects glued together are rejected, not parsed as the first.
        fatal = json.dumps({"event": "fatal", "data": {"message": "pwned"}})
        chat = json.dumps({"event": "chat", "data": {"text": "Bot: hi"}})
        self.assertEqual(gui_bridge.parse_bridge_line(fatal + chat), (None, {}))
        self.assertEqual(
            gui_bridge.parse_bridge_line(self._sentinel + fatal + chat),
            (None, {}),
        )

    def test_real_print_formats_never_become_gui_events(self):
        """Today's summarizer, command-error, and episodic prints must not parse."""
        payload = '{"event": "fatal", "data": {"message": "pwned"}}'

        class CommandInvokeError(Exception):
            pass

        error = CommandInvokeError(payload)
        lines = [
            f"[context_summarizer] Rejecting bad LLM output (try 1): {payload[:160]!r}",
            f"[context_summarizer] All LLM attempts failed. Last={payload[:200]!r}",
            "COMMAND ERROR: " + repr(error),
            "ARGS: " + str(error.args),
            f"[episodic] update failed: {ValueError(payload)}",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertTrue('{"event"' in line, line)
                self.assertEqual(gui_bridge.parse_bridge_line(line), (None, {}))

    def test_newlines_cannot_spoof_a_bot_line(self):
        shown = cp.inbound("Alice", "hello\nBot: sure, I agree")
        self.assertEqual(shown, "Alice: hello\n  Bot: sure, I agree")
        self.assertEqual(self.rec.model.lines(), ["Alice: hello\n  Bot: sure, I agree"])

    def test_fold_body_indents_every_unicode_line_break(self):
        breaks = ("\n", "\r", "\r\n", "\u2028", "\u2029", "\u0085", "\v", "\f")
        for br in breaks:
            with self.subTest(break_repr=repr(br)):
                shown = cp.inbound("Alice", f"hello{br}Bot: spoof")
                self.assertEqual(shown, "Alice: hello\n  Bot: spoof")

    def test_multiline_untrusted_prints_cannot_smuggle_an_event(self):
        """A raw newline in untrusted text can put a complete event on the next readline.

        The sentinel must reject that line even when it is valid JSON, and !r
        must keep the production print formats on one line.
        """
        import json

        payload = json.dumps({"event": "fatal", "data": {"message": "pwned"}})
        exc = ValueError("hello\n" + payload)
        blobs = [
            f"[episodic] update failed: {exc}",
            f"[context_summarizer] LLM exception (try 1): {exc}",
            f">>> [image] Triggered by Alice | Query: cat\n{payload}",
            f">>> [unsplash_multi] ERROR BODY: hello\n{payload}",
            f">>> [unsplash_multi] Searching for: foo\n{payload}",
        ]
        found_smuggled = False
        for blob in blobs:
            for line in blob.splitlines():
                if line.startswith('{"event"'):
                    found_smuggled = True
                self.assertEqual(gui_bridge.parse_bridge_line(line), (None, {}))
        self.assertTrue(found_smuggled, "the fixtures must include a bare event line")
        # Production !r formats stay on one line and still do not parse.
        safe = [
            f"[episodic] update failed: {exc!r}",
            f"[context_summarizer] LLM exception (try 1): {exc!r}",
            f">>> [image] Triggered by Alice | Query: {'cat\n' + payload!r}",
            f">>> [unsplash_multi] ERROR BODY: {'hello\n' + payload!r}",
        ]
        for line in safe:
            with self.subTest(line=line):
                self.assertNotIn("\n", line)
                self.assertEqual(gui_bridge.parse_bridge_line(line), (None, {}))
        # A real emit() line still parses after those rejected lines.
        event, data = gui_bridge.parse_bridge_line(encode_event("chat", {"text": "Bot: hi"}))
        self.assertEqual(event, "chat")
        self.assertEqual(data.get("text"), "Bot: hi")

    def test_emit_survives_latin1_stdout(self):
        captured = bytearray()

        class FakeBuf:
            def write(self, data):
                captured.extend(data)

            def flush(self):
                pass

        class Latin1Stdout:
            encoding = "latin-1"
            buffer = FakeBuf()

            def write(self, s):
                s.encode("latin-1")
                raise AssertionError("text write should not succeed")

            def flush(self):
                pass

        text = "Bot: hello 👋 — yes"
        with mock.patch.object(gui_bridge, "_enabled", True), mock.patch.object(
            gui_bridge.sys, "stdout", Latin1Stdout()
        ):
            gui_bridge.emit("chat", {"text": text})
        event, data = gui_bridge.parse_bridge_line(captured.decode("utf-8"))
        self.assertEqual(event, "chat")
        self.assertEqual(data.get("text"), text)


class SendPathTests(ChatPanelSetup):
    def test_send_visible_mirrors_after_success(self):
        message, channel = _message()
        run(cp.send_visible(channel, "hello there"))
        self.assertEqual(channel._sent, ["hello there"])
        self.assertEqual(self.rec.model.lines(), ["Bot: hello there"])

    def test_send_visible_skips_panel_when_send_fails(self):
        async def boom(*_args, **_kw):
            raise RuntimeError("discord down")

        channel = SimpleNamespace(send=boom)
        with self.assertRaises(RuntimeError):
            run(cp.send_visible(channel, "never seen"))
        self.assertEqual(self.rec.model.lines(), [])

    def test_send_reply_emits_each_split_chunk(self):
        from cogs.events import on_message as om

        long = ("word " * 500).strip()
        chunks = discord_chunks(long)
        self.assertGreater(len(chunks), 1)
        message, channel = _message()
        run(om._send_reply(message, long))
        self.assertEqual(channel._sent, chunks)
        self.assertEqual(self.rec.model.lines(), [f"Bot: {c}" for c in chunks])

    def test_send_reply_retries_without_reference(self):
        from cogs.events import on_message as om

        class FakeHTTP(Exception):
            code = 50035

        import discord as discord_mod

        calls = []

        async def fake_send(content=None, **kw):
            calls.append(kw)
            if "reference" in kw:
                raise FakeHTTP("unknown message")
            return SimpleNamespace(id=2, content=content)

        message, channel = _message()
        channel.send = fake_send
        with mock.patch.object(discord_mod, "HTTPException", FakeHTTP):
            run(om._send_reply(message, "recovered"))
        self.assertEqual(self.rec.model.lines(), ["Bot: recovered"])
        self.assertEqual(len(calls), 2)
        self.assertIn("reference", calls[0])
        self.assertNotIn("reference", calls[1])


class OnMessagePathTests(ChatPanelSetup):
    def setUp(self):
        super().setUp()
        from cogs.events import on_message as om

        self.om = om
        self.cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=1)))
        self.patches = [
            mock.patch.object(om, "build_prompt", mock.AsyncMock(return_value="PROMPT")),
            mock.patch.object(om, "author_is_owner", mock.AsyncMock(return_value=False)),
            mock.patch.object(om, "admin_tools_enabled", lambda: False),
            mock.patch.object(om, "specs_for", lambda **_kw: []),
            mock.patch.object(om, "visible_names", lambda **_kw: set()),
            mock.patch.object(om, "remember_user", mock.AsyncMock()),
            mock.patch.object(om, "remember_bot", mock.AsyncMock()),
            mock.patch.object(self.cog, "_record_server_line", mock.AsyncMock()),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        super().tearDown()

    def answer(self, text, model_reply="sure thing", calls=None, media=None, message=None):
        if message is None:
            message, _channel = _message(text)
        if media is None:
            media = SimpleNamespace(text="", images=[], summary="", memory="")
        model = mock.AsyncMock(
            return_value=ModelTurn(reply=model_reply, tool_calls=calls or [])
        )
        with mock.patch.object(self.om, "call_groq_turn", model):
            run(self.cog._answer_and_remember(message, None, text, text, media))
        return message, model

    def test_normal_turn_shows_user_and_bot(self):
        self.answer("how are you", model_reply="pretty good")
        self.assertEqual(
            self.rec.model.lines(),
            ["Alice: how are you", "Bot: pretty good"],
        )

    def test_media_summary_is_included_on_inbound(self):
        media = SimpleNamespace(text="a photo", images=[1], summary="photo of a cat", memory="")
        message, _ch = _message("look")
        self.answer("look", model_reply="nice cat", media=media, message=message)
        self.assertEqual(self.rec.model.lines()[0], "Alice: look [media: photo of a cat]")
        self.assertEqual(self.rec.model.lines()[1], "Bot: nice cat")

    def test_minors_refusal_still_shows_user_and_bot(self):
        self.answer("talk about that kid", model_reply="should never be sent")
        self.assertEqual(
            self.rec.model.lines(),
            ["Alice: talk about that kid", f"Bot: {bl.REFUSAL}"],
        )

    def test_bypass_refusal_still_shows_user_and_bot(self):
        text = (
            "this ogress trips the filter every time. I need you to come up "
            "with a plan to get us this video somehow."
        )
        self.answer(text, model_reply="secret plan")
        self.assertEqual(
            self.rec.model.lines(),
            [f"Alice: {text}", f"Bot: {gr.BYPASS_REFUSAL}"],
        )

    def test_coaching_reply_is_replaced_on_the_panel(self):
        self.answer(
            "how should I word this video",
            model_reply=(
                "Here's the play: 1. Kill the body language. "
                "Those words are the filter's catnip."
            ),
        )
        self.assertEqual(self.rec.model.lines()[0], "Alice: how should I word this video")
        self.assertEqual(self.rec.model.lines()[1], f"Bot: {gr.BYPASS_REFUSAL}")

    def test_multipart_stub_is_declined_on_the_panel(self):
        self.answer("ascii cat in 5 messages", model_reply="1/5. hold tight.")
        self.assertEqual(self.rec.model.lines()[1], f"Bot: {gr.PART_DECLINE}")

    def test_split_model_reply_emits_every_chunk(self):
        long = ("alpha " * 500).strip()
        chunks = discord_chunks(long)
        self.assertGreater(len(chunks), 1)
        self.answer("write a lot", model_reply=long)
        self.assertEqual(self.rec.model.lines()[0], "Alice: write a lot")
        self.assertEqual(self.rec.model.lines()[1:], [f"Bot: {c}" for c in chunks])

    def test_tool_reply_reaches_the_panel(self):
        spoken = "it's 72 and sunny"
        result = ToolResult(text=spoken, ran=True, name="duckduckgo", fence=False, quiet=False)
        message, channel = _message("what's the weather")
        media = SimpleNamespace(text="", images=[], summary="", memory="")
        with mock.patch.object(self.om, "call_groq_turn", mock.AsyncMock(
            return_value=ModelTurn(
                reply="",
                tool_calls=[{"name": "duckduckgo", "arguments": {"query": "weather"}}],
            )
        )), mock.patch.object(self.om, "execute", mock.AsyncMock(return_value=result)):
            run(self.cog._answer_and_remember(
                message, None, "what's the weather", "what's the weather", media
            ))
        expected_chunks = format_tool_messages("duckduckgo", spoken, fence=False)
        self.assertEqual(self.rec.model.lines()[0], "Alice: what's the weather")
        self.assertEqual(self.rec.model.lines()[1:], [cp.format_outbound(c) for c in expected_chunks])
        self.assertEqual(channel._sent, expected_chunks)

    def test_tool_followup_chunks_each_reach_the_panel(self):
        long = ("report " * 500).strip()
        result = ToolResult(text=long, ran=True, name="context_raw", fence=False, quiet=False)
        message, channel = _message("show me my memory")
        media = SimpleNamespace(text="", images=[], summary="", memory="")
        with mock.patch.object(self.om, "call_groq_turn", mock.AsyncMock(
            return_value=ModelTurn(
                reply="",
                tool_calls=[{"name": "context_raw", "arguments": {}}],
            )
        )), mock.patch.object(self.om, "execute", mock.AsyncMock(return_value=result)):
            run(self.cog._answer_and_remember(
                message, None, "show me my memory", "show me my memory", media
            ))
        expected = format_tool_messages("context_raw", long, fence=False)
        self.assertGreater(len(expected), 1)
        self.assertEqual(self.rec.model.lines()[0], "Alice: show me my memory")
        self.assertEqual(self.rec.model.lines()[1:], [cp.format_outbound(c) for c in expected])
        self.assertEqual(channel._sent, expected)

    def test_tool_argument_refusal_reaches_the_panel(self):
        message, channel = _message("make that")
        media = SimpleNamespace(text="", images=[], summary="", memory="")
        with mock.patch.object(self.om, "call_groq_turn", mock.AsyncMock(
            return_value=ModelTurn(
                reply="",
                tool_calls=[{"name": "asset", "arguments": {"prompt": "a kid"}}],
            )
        )), mock.patch.object(self.om, "execute", mock.AsyncMock()) as exe:
            run(self.cog._answer_and_remember(message, None, "make that", "make that", media))
        exe.assert_not_called()
        self.assertEqual(self.rec.model.lines()[0], "Alice: make that")
        self.assertEqual(self.rec.model.lines()[1], f"Bot: {bl.REFUSAL}")

    def test_prompt_failure_still_keeps_the_user_line(self):
        self.om.build_prompt.side_effect = RuntimeError("prompt died")
        message, _ch = _message("still here")
        media = SimpleNamespace(text="", images=[], summary="", memory="")
        with self.assertRaises(RuntimeError):
            run(self.cog._answer_and_remember(
                message, None, "still here", "still here", media
            ))
        self.assertEqual(self.rec.model.lines(), ["Alice: still here"])


class QuietAndSidePathTests(ChatPanelSetup):
    def test_asset_post_mirrors_caption(self):
        from core.imagine import AssetFile
        from core.tools_asset import _post, asset_text

        replies = []

        async def fake_reply(content=None, **_kw):
            replies.append(content)
            return SimpleNamespace(id=3, content=content)

        message = SimpleNamespace(reply=fake_reply)
        result = AssetFile(data=b"abc", filename="pic.png", mime="image/png", mode="image")
        run(_post(message, result, "a red boat"))
        self.assertEqual(replies, [asset_text("a red boat")])
        self.assertEqual(self.rec.model.lines(), [cp.format_outbound(asset_text("a red boat"))])

    def test_thread_asset_send_mirrors_caption(self):
        from core.imagine import AssetFile
        from core.tools_asset import asset_text
        from core.tools_thread import _send_asset

        message, channel = _message()
        result = AssetFile(data=b"abc", filename="pic.png", mime="image/png", mode="image")
        run(_send_asset(channel, result, "a sunset"))
        self.assertEqual(channel._sent, [asset_text("a sunset")])
        self.assertEqual(self.rec.model.lines(), [cp.format_outbound(asset_text("a sunset"))])

    def test_boo_shows_user_and_bot(self):
        from core import boo_kaitar

        message, channel = _message("hey you")
        message.author.id = boo_kaitar.TARGET_USER_ID
        with mock.patch.object(boo_kaitar.random, "random", return_value=-1.0), mock.patch.object(
            boo_kaitar.random, "choice", return_value="boo."
        ):
            # BOO_CHANCE is 0, so force the branch by patching the chance check.
            with mock.patch.object(boo_kaitar, "BOO_CHANCE", 1):
                self.assertTrue(run(boo_kaitar.maybe_boo(message)))
        self.assertEqual(self.rec.model.lines(), ["Alice: hey you", "Bot: boo."])
        self.assertEqual(channel._sent, ["boo."])

    def test_charoverride_chunks_reach_the_panel(self):
        from cogs import charoverride as co
        import core.blocklist as blocklist
        import core.client as client

        long = ("say this " * 500).strip()
        chunks = discord_chunks(long)
        self.assertGreater(len(chunks), 1)
        message, channel = _message("do it")
        bot = SimpleNamespace(
            next_reply_override=False,
            override_waiting_for=None,
            is_owner=mock.AsyncMock(return_value=True),
        )
        message.reference = SimpleNamespace(message_id=99)
        cog = co.CharOverride(bot)
        bot.next_reply_override = True
        bot.override_waiting_for = 99
        turn = ModelTurn(reply=long)
        with mock.patch.object(blocklist, "is_blocked", return_value=False), mock.patch.object(
            client, "call_groq_turn", mock.AsyncMock(return_value=turn)
        ):
            run(cog.on_message(message))
        self.assertEqual(
            self.rec.model.lines(),
            ["Alice: do it"] + [cp.format_outbound(c) for c in chunks],
        )
        self.assertEqual(channel._sent, chunks)

    def test_announce_reaches_the_panel(self):
        sent = []

        async def fake_send(text):
            sent.append(text)
            return SimpleNamespace(id=8)

        channel = SimpleNamespace(send=fake_send, permissions_for=lambda _me: SimpleNamespace(send_messages=True))
        guild = SimpleNamespace(
            system_channel=channel,
            text_channels=[channel],
            me=object(),
        )
        bot = SimpleNamespace(get_guild=lambda _id: guild)
        with mock.patch("core.home_id.home_server_id_from_env", return_value=7), mock.patch(
            "core.discord_store.find_category", return_value=None
        ), mock.patch.object(gui_bridge, "log"):
            run(gui_bridge._announce(bot, "server is up"))
        self.assertEqual(sent, ["server is up"])
        self.assertEqual(self.rec.model.lines(), ["Bot: server is up"])

    def test_asset_command_refusal_shows_user_and_bot(self):
        from cogs.asset import Asset
        from core.imagine import ImagineError

        replies = []

        async def fake_reply(content=None, **_kw):
            replies.append(content)
            return SimpleNamespace(id=4, content=content, edit=mock.AsyncMock(), delete=mock.AsyncMock())

        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42, name="alice", display_name="Alice"),
            message=SimpleNamespace(channel=SimpleNamespace(id=99)),
            channel=SimpleNamespace(id=99),
            reply=fake_reply,
            send=fake_reply,
        )
        cog = Asset(SimpleNamespace())
        with mock.patch("core.admin_tools.author_is_owner", mock.AsyncMock(return_value=True)), mock.patch(
            "cogs.asset.speaker_allowed", return_value=True
        ), mock.patch("cogs.asset.check_prompt", side_effect=ImagineError("Nope. Not going there.")):
            run(Asset.asset.callback(cog, ctx, prompt="a kid"))
        self.assertEqual(self.rec.model.lines()[0], "Alice: ?asset a kid")
        self.assertEqual(self.rec.model.lines()[1], "Bot: Nope. Not going there.")


    def test_charoverride_command_is_recorded_inbound(self):
        from cogs import charoverride as co

        sent = []

        async def fake_send(content=None, **_kw):
            sent.append(content)
            return SimpleNamespace(id=99, content=content)

        ctx = SimpleNamespace(
            author=SimpleNamespace(id=1, name="sevin", display_name="Sevin"),
            send=fake_send,
        )
        bot = SimpleNamespace(next_reply_override=False, override_waiting_for=None)
        cog = co.CharOverride(bot)
        run(co.CharOverride.charoverride.callback(cog, ctx))
        self.assertEqual(self.rec.model.lines()[0], "Sevin: ?charoverride")
        self.assertEqual(self.rec.model.lines()[1], "Bot: What would you like me to say?")
        self.assertTrue(bot.next_reply_override)


class QtQueuedDeliveryTests(unittest.TestCase):
    def test_queued_signal_reaches_the_model(self):
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
            from PyQt6.QtWidgets import QApplication
        except Exception:
            self.skipTest("PyQt6 is not available")

        app = QApplication.instance() or QApplication([])
        model = cp.ChatPanelModel()

        class Source(QObject):
            line_received = pyqtSignal(str)

        class Reader(QThread):
            def __init__(self, source, text):
                super().__init__()
                self._source = source
                self._text = text

            def run(self):
                self._source.line_received.emit(self._text)

        source = Source()
        source.line_received.connect(model.append, Qt.ConnectionType.QueuedConnection)
        reader = Reader(source, "Bot: queued hello")
        reader.start()
        reader.wait(2000)
        # The queued slot runs once the GUI thread processes events.
        for _ in range(20):
            app.processEvents()
            if model.lines():
                break
        self.assertEqual(model.lines(), ["Bot: queued hello"])
        reader.deleteLater()


class CommandErrorPathTests(ChatPanelSetup):
    def test_owner_and_error_replies_reach_the_panel(self):
        from discord.ext import commands
        from core.bot import Eche

        sent = []

        async def fake_send(content=None, **_kw):
            sent.append(content)
            return SimpleNamespace(id=1, content=content)

        ctx = SimpleNamespace(send=fake_send)
        bot = Eche.__new__(Eche)
        run(bot.on_command_error(ctx, commands.NotOwner()))
        self.assertEqual(
            self.rec.model.lines(),
            ["Bot: Only an owner from Settings → Security can use that command."],
        )
        self.rec.model.clear()
        sent.clear()
        run(bot.on_command_error(ctx, RuntimeError("boom")))
        self.assertEqual(self.rec.model.lines(), ["Bot: Error: boom"])


class ProductionSendSiteTests(unittest.TestCase):
    """Public Discord sends and message edits in production go through the panel.

    Hidden store posts are allowlisted by exact (file, receiver) only. A
    comment such as 'not the chat panel' must not skip a site by itself.
    """

    ALLOW_PREFIXES = (
        "tests/",
        "gui/",
    )
    # Hidden memory/shop/bank/reminder stores, plus non-message edits
    # (member mute, thread archive, channel topic).
    HIDDEN_SITES = {
        ("cogs/remind/remind_handler.py", "thread"),
        ("cogs/economy/shops.py", "owned_file.channel"),
        ("cogs/economy/shops.py", "hosted_file.channel"),
        ("cogs/economy/shops.py", "target_owned_file.channel"),
        ("cogs/economy/bank.py", "thread"),
        ("core/discord_store.py", "parent"),
        ("core/discord_store.py", "message"),
        ("core/discord_store.py", "thread"),
        ("core/discord_store.py", "channel"),
        ("core/tools_admin.py", "member"),
    }

    SEND = __import__("re").compile(
        r"await\s+([A-Za-z_][\w\.]*)\.(send|reply)\("
    )
    EDIT = __import__("re").compile(
        r"await\s+([A-Za-z_][\w\.]*)\.(edit|edit_message)\("
    )
    RESP = __import__("re").compile(
        r"await\s+[A-Za-z_][\w\.]*\.response\.send_message\("
    )
    FOLLOW = __import__("re").compile(
        r"await\s+([A-Za-z_][\w\.]*)\.followup\.send\("
    )

    def test_public_send_sites_use_send_visible(self):
        import os
        root = SOURCE
        leftovers = []
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, root).replace("\\", "/")
                if rel.startswith(self.ALLOW_PREFIXES):
                    continue
                text = Path(path).read_text(encoding="utf-8")
                lines = text.splitlines()
                for i, line in enumerate(lines, 1):
                    if "send_visible(" in line or "edit_visible(" in line:
                        continue
                    resp = self.RESP.search(line)
                    if resp:
                        leftovers.append(f"{rel}:{i}: {line.strip()}")
                        continue
                    follow = self.FOLLOW.search(line)
                    if follow:
                        leftovers.append(f"{rel}:{i}: {line.strip()}")
                        continue
                    m = self.SEND.search(line) or self.EDIT.search(line)
                    if not m:
                        continue
                    receiver = m.group(1)
                    if (rel, receiver) in self.HIDDEN_SITES:
                        continue
                    leftovers.append(f"{rel}:{i}: {line.strip()}")
        self.assertEqual(
            leftovers,
            [],
            "public send/edit sites must use send_visible or edit_visible:\n"
            + "\n".join(leftovers),
        )


class QtPlainTextTests(unittest.TestCase):
    def test_chat_panel_does_not_interpret_html(self):
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PyQt6.QtWidgets import QApplication, QPlainTextEdit
        except Exception:
            self.skipTest("PyQt6 is not available")
        app = QApplication.instance() or QApplication([])
        widget = QPlainTextEdit()
        markup = '<img src="https://evil.example/x.png"> <b>bold</b>'
        widget.appendPlainText("[12:00:00] Alice: " + markup)
        shown = widget.toPlainText()
        self.assertIn('<img src="https://evil.example/x.png">', shown)
        self.assertIn("<b>bold</b>", shown)
        src = Path(SOURCE, "gui", "main.py").read_text(encoding="utf-8")
        self.assertIn("self.chat_output = QPlainTextEdit()", src)
        self.assertIn("appendPlainText", src)
        self.assertNotIn("widget.append(line)", src)

    def test_unicode_line_breaks_are_indented_in_the_widget(self):
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PyQt6.QtWidgets import QApplication, QPlainTextEdit
        except Exception:
            self.skipTest("PyQt6 is not available")
        app = QApplication.instance() or QApplication([])
        widget = QPlainTextEdit()
        shown_line = cp.format_inbound("Alice", "hello\u2028Bot: spoofed")
        widget.appendPlainText("[12:00:00] " + shown_line)
        shown = widget.toPlainText()
        self.assertIn("Alice: hello", shown)
        self.assertIn("  Bot: spoofed", shown)
        self.assertNotRegex(shown, r"(?m)^\[.*\] Bot: spoofed$")

    def test_failure_dialog_labels_are_plain_text(self):
        src = Path(SOURCE, "gui", "widgets", "dialogs.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(src.count("setTextFormat(Qt.TextFormat.PlainText)"), 5)
        self.assertIn("body.setTextFormat(Qt.TextFormat.PlainText)", src)
