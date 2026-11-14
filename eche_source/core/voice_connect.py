"""Joining voice for ?play, with a bounded timeout, one clean retry and logs.

discord.py waits for two gateway events after it asks to join a channel:
VOICE_STATE_UPDATE (our own session) and VOICE_SERVER_UPDATE (the voice
server endpoint and token). When Discord never sends the second one, the
connect sits at "Starting voice handshake" until it times out and ?play
failed with a bare TimeoutError. This module records which event arrived,
tears the half-open voice client down, tries once more, and tells the
channel what happened. Tokens and endpoints are never logged.
"""

from __future__ import annotations

import asyncio
import time

VOICE_CONNECT_TIMEOUT = 20.0
VOICE_CONNECT_ATTEMPTS = 2
VOICE_RETRY_DELAY = 2.0

# Monotonic arrival times of the bot's own voice gateway events, per guild.
_seen: dict[tuple[int, str], float] = {}


def note_gateway_event(guild_id, event_type: str) -> None:
    """Record that one of our voice gateway events arrived (type only)."""
    try:
        _seen[(int(guild_id or 0), event_type)] = time.monotonic()
    except Exception:
        pass


def handshake_stage(guild_id, started: float) -> str:
    """Which part of the join was missing when it timed out."""
    try:
        key = int(guild_id or 0)
    except Exception:
        key = 0
    got_state = _seen.get((key, "VOICE_STATE_UPDATE"), -1.0) >= started
    got_server = _seen.get((key, "VOICE_SERVER_UPDATE"), -1.0) >= started
    if not got_state and not got_server:
        return "no-voice-events"
    if not got_server:
        return "no-voice-server-update"
    if not got_state:
        return "no-voice-state-update"
    return "voice-websocket"


_client_cls = None


def voice_client_class():
    """discord.VoiceClient that notes which handshake events arrived.

    ConnectionState hands our own VOICE_STATE_UPDATE and VOICE_SERVER_UPDATE
    to the voice client, so no gateway listener (and no per-event audit row)
    is needed. The payloads hold the voice token; only the event type is kept.
    """
    global _client_cls
    if _client_cls is not None:
        return _client_cls
    import discord

    class EcheVoiceClient(discord.VoiceClient):
        async def on_voice_state_update(self, data):
            note_gateway_event(getattr(self.guild, "id", 0), "VOICE_STATE_UPDATE")
            return await super().on_voice_state_update(data)

        async def on_voice_server_update(self, data):
            note_gateway_event(getattr(self.guild, "id", 0), "VOICE_SERVER_UPDATE")
            return await super().on_voice_server_update(data)

    _client_cls = EcheVoiceClient
    return _client_cls


_STAGE_TEXT = {
    "no-voice-events": "Discord never answered the join request",
    "no-voice-server-update": "Discord never sent the voice server details",
    "no-voice-state-update": "Discord never confirmed the voice session",
    "voice-websocket": "the voice server didn't finish connecting",
}


def failure_message(stage: str) -> str:
    reason = _STAGE_TEXT.get(stage, "the voice connection timed out")
    return (
        f"❌ I couldn't join voice: {reason}. "
        "This is on Discord's side, not the song. Try `?play` again in a minute. "
        "If it keeps happening, set the voice channel's Region Override to a "
        "different region (Edit Channel → Overview) and try again."
    )


async def _drop(voice_client) -> None:
    if voice_client is None:
        return
    try:
        await voice_client.disconnect(force=True)
    except Exception:
        pass
    try:
        voice_client.cleanup()
    except Exception:
        pass


def _audit(outcome: str, ctx, **extra) -> None:
    try:
        from core.audit import safe_activity

        guild = getattr(ctx, "guild", None)
        author = getattr(ctx, "author", None)
        voice = getattr(author, "voice", None)
        channel = getattr(voice, "channel", None)
        safe_activity(
            "voice-connect",
            outcome=outcome,
            user_id=getattr(author, "id", None),
            guild_id=getattr(guild, "id", None),
            channel_id=getattr(channel, "id", None),
            **extra,
        )
    except Exception:
        pass


async def connect_voice(
    ctx,
    *,
    timeout: float = VOICE_CONNECT_TIMEOUT,
    attempts: int = VOICE_CONNECT_ATTEMPTS,
    retry_delay: float = VOICE_RETRY_DELAY,
):
    """Return a connected voice client for the author's channel, or None.

    On failure the channel has already been told why, and an audit row names
    the stage that was missing.
    """
    channel = ctx.author.voice.channel
    guild = getattr(ctx, "guild", None) or getattr(channel, "guild", None)
    stage = "unknown"
    for attempt in range(1, max(1, attempts) + 1):
        existing = getattr(guild, "voice_client", None)
        if existing is not None:
            if existing.is_connected():
                if getattr(existing, "channel", None) != channel:
                    try:
                        await existing.move_to(channel)
                    except Exception:
                        pass
                return existing
            # Half-open client left by an earlier timeout. discord.py refuses
            # a new connect while it exists, so drop it first.
            await _drop(existing)
        started = time.monotonic()
        try:
            vc = await channel.connect(
                timeout=timeout, reconnect=True, cls=voice_client_class()
            )
        except (asyncio.TimeoutError, TimeoutError):
            stage = handshake_stage(getattr(guild, "id", 0), started)
            print(
                f">>> voice connect timed out (attempt {attempt}/{attempts}, "
                f"stage={stage}, {round(time.monotonic() - started, 1)}s)"
            )
            _audit(
                "error",
                ctx,
                error_type="TimeoutError",
                stage=stage,
                attempt=attempt,
                seconds=round(time.monotonic() - started, 1),
            )
            await _drop(getattr(guild, "voice_client", None))
            if attempt < attempts:
                await asyncio.sleep(retry_delay)
            continue
        except Exception as exc:
            stage = type(exc).__name__
            print(f">>> voice connect failed (attempt {attempt}/{attempts}): {stage}")
            _audit("error", ctx, error_type=stage, stage="connect", attempt=attempt)
            await _drop(getattr(guild, "voice_client", None))
            if attempt < attempts:
                await asyncio.sleep(retry_delay)
            continue
        _audit(
            "success",
            ctx,
            attempt=attempt,
            seconds=round(time.monotonic() - started, 1),
        )
        return vc
    await ctx.send(failure_message(stage))
    return None
