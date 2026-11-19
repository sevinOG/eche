"""?play voice join: bounded timeout, one retry, stage logged, clear message.

Mock-only. Nothing here talks to Discord.
"""

import asyncio
import time
import unittest
from unittest import mock

from core import voice_connect as vcm


class FakeVC:
    def __init__(self, connected=True, channel=None):
        self._connected = connected
        self.channel = channel
        self.disconnected = False
        self.cleaned = False

    def is_connected(self):
        return self._connected

    async def disconnect(self, force=False):
        self.disconnected = True
        self._connected = False

    def cleanup(self):
        self.cleaned = True

    async def move_to(self, channel):
        self.channel = channel


class FakeGuild:
    id = 1083466447164018738

    def __init__(self):
        self.voice_client = None


class FakeChannel:
    id = 1117508053638598656

    def __init__(self, guild, outcomes):
        self.guild = guild
        self.outcomes = list(outcomes)
        self.calls = []

    async def connect(self, *, timeout, reconnect, cls):
        self.calls.append({"timeout": timeout, "reconnect": reconnect, "cls": cls})
        outcome = self.outcomes.pop(0)
        if outcome == "server-missing":
            # Discord sent our voice state but never the voice server update.
            vcm.note_gateway_event(self.guild.id, "VOICE_STATE_UPDATE")
            self.guild.voice_client = FakeVC(connected=False, channel=self)
            raise asyncio.TimeoutError()
        if outcome == "silent":
            self.guild.voice_client = FakeVC(connected=False, channel=self)
            raise asyncio.TimeoutError()
        vc = FakeVC(connected=True, channel=self)
        self.guild.voice_client = vc
        return vc


class FakeCtx:
    def __init__(self, outcomes):
        self.guild = FakeGuild()
        self.channel_obj = FakeChannel(self.guild, outcomes)
        voice = mock.Mock()
        voice.channel = self.channel_obj
        self.author = mock.Mock(id=284007193181945857, voice=voice)
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


def run(coro):
    return asyncio.run(coro)


class VoiceConnectTests(unittest.TestCase):
    def setUp(self):
        vcm._seen.clear()
        self.audits = []
        patcher = mock.patch(
            "core.audit.safe_activity",
            side_effect=lambda name, **kw: self.audits.append((name, kw)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep = mock.patch("core.voice_connect.asyncio.sleep", new=mock.AsyncMock())
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_success_first_try_uses_bounded_timeout_and_tracking_client(self):
        ctx = FakeCtx(["ok"])
        vc = run(vcm.connect_voice(ctx))
        self.assertTrue(vc.is_connected())
        call = ctx.channel_obj.calls[0]
        self.assertEqual(call["timeout"], vcm.VOICE_CONNECT_TIMEOUT)
        self.assertLess(call["timeout"], 30.0)
        self.assertTrue(call["reconnect"])
        import discord
        self.assertTrue(issubclass(call["cls"], discord.VoiceClient))
        self.assertEqual(ctx.sent, [])
        self.assertEqual(self.audits[-1][0], "voice-connect")
        self.assertEqual(self.audits[-1][1]["outcome"], "success")

    def test_timeout_then_retry_succeeds_after_dropping_half_open_client(self):
        ctx = FakeCtx(["server-missing", "ok"])
        vc = run(vcm.connect_voice(ctx))
        self.assertIsNotNone(vc)
        self.assertEqual(len(ctx.channel_obj.calls), 2)
        self.assertEqual(ctx.sent, [])
        err = [kw for name, kw in self.audits if kw.get("outcome") == "error"]
        self.assertEqual(len(err), 1)
        self.assertEqual(err[0]["stage"], "no-voice-server-update")
        self.assertEqual(err[0]["error_type"], "TimeoutError")
        self.assertEqual(err[0]["attempt"], 1)
        self.assertEqual(err[0]["guild_id"], FakeGuild.id)

    def test_both_attempts_time_out_sends_clear_message_not_exception(self):
        ctx = FakeCtx(["server-missing", "server-missing"])
        vc = run(vcm.connect_voice(ctx))
        self.assertIsNone(vc)
        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("couldn't join voice", ctx.sent[0])
        self.assertIn("voice server details", ctx.sent[0])
        self.assertIn("Region Override", ctx.sent[0])
        errs = [kw for _, kw in self.audits if kw.get("outcome") == "error"]
        self.assertEqual([e["attempt"] for e in errs], [1, 2])
        # Half-open client left by the timeout was dropped.
        self.assertTrue(ctx.guild.voice_client.disconnected)

    def test_silent_gateway_stage(self):
        ctx = FakeCtx(["silent", "silent"])
        run(vcm.connect_voice(ctx))
        errs = [kw for _, kw in self.audits if kw.get("outcome") == "error"]
        self.assertEqual(errs[0]["stage"], "no-voice-events")
        self.assertIn("never answered", ctx.sent[0])

    def test_existing_connected_client_is_reused(self):
        ctx = FakeCtx([])
        existing = FakeVC(connected=True, channel=ctx.channel_obj)
        ctx.guild.voice_client = existing
        self.assertIs(run(vcm.connect_voice(ctx)), existing)
        self.assertEqual(ctx.channel_obj.calls, [])

    def test_stale_client_dropped_before_connect(self):
        ctx = FakeCtx(["ok"])
        stale = FakeVC(connected=False, channel=ctx.channel_obj)
        ctx.guild.voice_client = stale
        run(vcm.connect_voice(ctx))
        self.assertTrue(stale.disconnected)
        self.assertTrue(stale.cleaned)

    def test_stage_ignores_events_from_before_this_attempt(self):
        vcm.note_gateway_event(1, "VOICE_SERVER_UPDATE")
        vcm.note_gateway_event(1, "VOICE_STATE_UPDATE")
        started = time.monotonic() + 1
        self.assertEqual(vcm.handshake_stage(1, started), "no-voice-events")
        self.assertEqual(vcm.handshake_stage(1, 0.0), "voice-websocket")

    def test_tracking_client_notes_events_without_payload(self):
        cls = vcm.voice_client_class()
        inst = cls.__new__(cls)
        inst.channel = mock.Mock(guild=FakeGuild())
        seen = []
        with mock.patch(
            "discord.VoiceClient.on_voice_server_update",
            new=mock.AsyncMock(side_effect=lambda data: seen.append("server")),
        ), mock.patch(
            "discord.VoiceClient.on_voice_state_update",
            new=mock.AsyncMock(side_effect=lambda data: seen.append("state")),
        ):
            run(inst.on_voice_server_update({"token": "secret-token", "guild_id": "1", "endpoint": "x"}))
            run(inst.on_voice_state_update({"channel_id": "2", "session_id": "s"}))
        self.assertEqual(seen, ["server", "state"])
        self.assertEqual(vcm.handshake_stage(FakeGuild.id, 0.0), "voice-websocket")
        self.assertNotIn("secret-token", repr(vcm._seen))


class MusicCogJoinTests(unittest.TestCase):
    def test_play_stops_cleanly_when_join_fails(self):
        from cogs.music import music as mm

        cog = mm.Music(mock.Mock())
        ctx = mock.Mock()
        ctx.author.voice = mock.Mock()
        with mock.patch("core.voice_connect.connect_voice", new=mock.AsyncMock(return_value=None)):
            self.assertFalse(run(cog.ensure_vc(ctx)))
        self.assertIsNone(cog.vc)

    def test_concurrent_plays_share_one_join(self):
        from cogs.music import music as mm

        cog = mm.Music(mock.Mock())
        calls = []

        async def fake_connect(ctx):
            calls.append(ctx)
            await asyncio.sleep(0.01)
            return FakeVC(connected=True)

        async def both():
            ctx = mock.Mock()
            ctx.author.voice = mock.Mock()
            with mock.patch("core.voice_connect.connect_voice", new=fake_connect):
                return await asyncio.gather(cog.ensure_vc(ctx), cog.ensure_vc(ctx))

        self.assertEqual(run(both()), [True, True])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
