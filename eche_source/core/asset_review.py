# core/asset_review.py
# Owner must approve every generated file before it is posted.
# Fail closed if the owner cannot be reached, times out, or denies.

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Iterable

from core.imagine import AssetFile, ImagineError

OWNER_REVIEW_PENDING = "Waiting for the owner to approve that file before it is posted."
OWNER_REVIEW_DENIED = "The owner did not approve that file, so it was not posted."
OWNER_REVIEW_FAILED = "I could not reach the owner for review, so the file was not posted."
OWNER_REVIEW_TIMEOUT = "The owner did not respond in time, so the file was not posted."
REVIEW_TIMEOUT_SECONDS = 10 * 60

# Tests can set a hook that returns True/False (or a coroutine) instead of
# sending a Discord DM. Production leaves this at None.
_review_hook: Callable[..., bool | Awaitable[bool]] | None = None


def set_review_hook(hook: Callable[..., bool | Awaitable[bool]] | None) -> None:
    global _review_hook
    _review_hook = hook


def _audit_review(outcome: str, **ids) -> None:
    try:
        from core.audit import record

        record(
            "review",
            outcome=outcome,
            user_id=ids.get("user_id"),
            guild_id=ids.get("guild_id"),
            channel_id=ids.get("channel_id"),
            request_id=ids.get("request_id") or "",
            response_id=ids.get("response_id") or "",
            safety=outcome not in {"approved", "posted"},
        )
    except Exception:
        pass


def audit_posted(**ids) -> None:
    """Log that a reviewed file was actually posted. Ids only, no prompt."""
    _audit_review("posted", **ids)


async def _reviewer_ids(bot) -> list[int]:
    """Configured Owner IDs, else the Discord app owner (including team apps)."""
    ids: list[int] = []
    try:
        from core.admin_tools import configured_owner_ids

        ids = [int(x) for x in (configured_owner_ids() or []) if x]
    except Exception:
        ids = []
    if ids:
        return ids
    owner_id = getattr(bot, "owner_id", None)
    if owner_id:
        try:
            return [int(owner_id)]
        except (TypeError, ValueError):
            pass
    info_fn = getattr(bot, "application_info", None)
    if info_fn is None:
        return []
    try:
        info = await info_fn()
    except Exception:
        return []
    out: list[int] = []
    owner = getattr(info, "owner", None)
    if getattr(owner, "id", None):
        out.append(int(owner.id))
    team = getattr(info, "team", None)
    if team is not None:
        team_owner = getattr(team, "owner_id", None) or getattr(getattr(team, "owner", None), "id", None)
        if team_owner:
            value = int(team_owner)
            if value not in out:
                out.append(value)
    return out


def _review_message(*, user_id=None, guild_id=None, channel_id=None, prompt: str = "") -> str:
    lines = ["An asset is waiting for your approval before it is posted."]
    if user_id:
        lines.append(f"Requester: {user_id}")
    if guild_id:
        lines.append(f"Server: {guild_id}")
    if channel_id:
        lines.append(f"Channel: {channel_id}")
    text = (prompt or "").strip()
    if text:
        shown = text if len(text) <= 400 else text[:399].rstrip() + "…"
        lines.append(f"Prompt: {shown}")
    return "\n".join(lines)


async def require_owner_review(
    result: AssetFile,
    *,
    bot=None,
    origin=None,
    user_id=None,
    guild_id=None,
    channel_id=None,
    prompt: str = "",
    request_id: str = "",
    response_id: str = "",
) -> AssetFile:
    """Hold the file until an owner approves it. Never posts by itself."""
    del origin
    ids = dict(
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        request_id=request_id,
        response_id=response_id,
    )
    if not isinstance(result, AssetFile) or not result.data:
        _audit_review("unreachable", **ids)
        raise ImagineError(OWNER_REVIEW_FAILED)
    hook = _review_hook
    if hook is not None:
        try:
            decision = hook(result)
            if asyncio.iscoroutine(decision):
                decision = await decision
        except Exception as exc:
            _audit_review("unreachable", **ids)
            raise ImagineError(OWNER_REVIEW_FAILED) from exc
        if not decision:
            _audit_review("denied", **ids)
            raise ImagineError(OWNER_REVIEW_DENIED)
        _audit_review("approved", **ids)
        return result
    if bot is None:
        _audit_review("unreachable", **ids)
        raise ImagineError(OWNER_REVIEW_FAILED)
    reviewers = await _reviewer_ids(bot)
    if not reviewers:
        _audit_review("unreachable", **ids)
        raise ImagineError(OWNER_REVIEW_FAILED)
    fetch = getattr(bot, "fetch_user", None)
    sent = 0
    view = _OwnerReviewView(reviewers)
    body = _review_message(
        user_id=user_id, guild_id=guild_id, channel_id=channel_id, prompt=prompt
    )
    try:
        import io
        import discord

        for owner_id in reviewers:
            try:
                owner = await fetch(owner_id) if fetch else None
                send = getattr(owner, "send", None)
                if send is None:
                    continue
                file = discord.File(io.BytesIO(result.data), filename=result.filename)
                await send(content=body, file=file, view=view)
                sent += 1
            except Exception:
                continue
    except Exception as exc:
        _audit_review("unreachable", **ids)
        raise ImagineError(OWNER_REVIEW_FAILED) from exc
    if sent == 0:
        _audit_review("unreachable", **ids)
        raise ImagineError(OWNER_REVIEW_FAILED)
    _audit_review("review-sent", **ids)
    try:
        approved = await asyncio.wait_for(view.decision, timeout=REVIEW_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        _audit_review("timeout", **ids)
        raise ImagineError(OWNER_REVIEW_TIMEOUT) from exc
    except Exception as exc:
        _audit_review("unreachable", **ids)
        raise ImagineError(OWNER_REVIEW_FAILED) from exc
    if not approved:
        _audit_review("denied", **ids)
        raise ImagineError(OWNER_REVIEW_DENIED)
    _audit_review("approved", **ids)
    return result


class _OwnerReviewView:
    """Thin wrapper so tests can import this module without discord.ui at collection."""

    def __init__(self, owner_ids: Iterable[int] | int | None):
        import discord

        if owner_ids is None:
            expected: set[int] = set()
        elif isinstance(owner_ids, (int, str)):
            try:
                expected = {int(owner_ids)}
            except (TypeError, ValueError):
                expected = set()
        else:
            expected = set()
            for item in owner_ids:
                try:
                    expected.add(int(item))
                except (TypeError, ValueError):
                    pass

        class View(discord.ui.View):
            def __init__(self, expected_ids: set[int]):
                super().__init__(timeout=REVIEW_TIMEOUT_SECONDS)
                self.expected_ids = expected_ids
                self.decision: asyncio.Future[bool] = asyncio.get_event_loop().create_future()

            async def _decide(self, interaction, value: bool):
                user = getattr(interaction, "user", None)
                uid = getattr(user, "id", None)
                if not self.expected_ids or uid not in self.expected_ids:
                    try:
                        await interaction.response.send_message(
                            "Only the owner can approve this.", ephemeral=True
                        )
                    except Exception:
                        pass
                    return
                if not self.decision.done():
                    self.decision.set_result(value)
                try:
                    await interaction.response.send_message(
                        "Approved. The file will be posted." if value else "Denied. The file will not be posted.",
                        ephemeral=True,
                    )
                except Exception:
                    pass
                self.stop()

            @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
            async def approve(self, interaction, button):
                del button
                await self._decide(interaction, True)

            @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
            async def deny(self, interaction, button):
                del button
                await self._decide(interaction, False)

        inner = View(expected)
        self.decision = inner.decision
        self._view = inner

    def __getattr__(self, name):
        return getattr(self._view, name)
