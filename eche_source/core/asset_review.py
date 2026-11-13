# core/asset_review.py
# Owner must approve every generated file before it is posted.
# Fail closed if the owner cannot be reached, times out, or denies.

from __future__ import annotations

import asyncio
from typing import Callable, Awaitable

from core.imagine import AssetFile, ImagineError

OWNER_REVIEW_PENDING = "Waiting for the owner to approve that file before it is posted."
OWNER_REVIEW_DENIED = "The owner did not approve that file, so it was not posted."
OWNER_REVIEW_FAILED = "I could not reach the owner for review, so the file was not posted."
REVIEW_TIMEOUT_SECONDS = 10 * 60

# Tests can set a hook that returns True/False (or a coroutine) instead of
# sending a Discord DM. Production leaves this at None.
_review_hook: Callable[..., bool | Awaitable[bool]] | None = None


def set_review_hook(hook: Callable[..., bool | Awaitable[bool]] | None) -> None:
    global _review_hook
    _review_hook = hook


def _owner_user(bot):
    owner = getattr(bot, "owner", None) or getattr(bot, "app_owner", None)
    if owner is not None:
        return owner
    user = getattr(bot, "user", None)
    owner_id = getattr(bot, "owner_id", None)
    if owner_id and user is not None and getattr(user, "id", None) == owner_id:
        return user
    return owner


async def require_owner_review(
    result: AssetFile,
    *,
    bot=None,
    origin=None,
    user_id=None,
    guild_id=None,
    channel_id=None,
) -> AssetFile:
    """Hold the file until the owner approves it. Never posts by itself."""
    del origin, user_id, guild_id, channel_id
    if not isinstance(result, AssetFile) or not result.data:
        raise ImagineError(OWNER_REVIEW_FAILED)
    hook = _review_hook
    if hook is not None:
        try:
            decision = hook(result)
            if asyncio.iscoroutine(decision):
                decision = await decision
        except Exception as exc:
            raise ImagineError(OWNER_REVIEW_FAILED) from exc
        if not decision:
            raise ImagineError(OWNER_REVIEW_DENIED)
        return result
    if bot is None:
        raise ImagineError(OWNER_REVIEW_FAILED)
    owner = _owner_user(bot)
    send = getattr(owner, "send", None) if owner is not None else None
    if send is None:
        # Fall back to bot.fetch_user(owner_id) when the owner object is missing.
        fetch = getattr(bot, "fetch_user", None)
        owner_id = getattr(bot, "owner_id", None)
        if fetch is None or not owner_id:
            raise ImagineError(OWNER_REVIEW_FAILED)
        try:
            owner = await fetch(owner_id)
            send = getattr(owner, "send", None)
        except Exception as exc:
            raise ImagineError(OWNER_REVIEW_FAILED) from exc
        if send is None:
            raise ImagineError(OWNER_REVIEW_FAILED)
    try:
        import io
        import discord

        view = _OwnerReviewView(getattr(owner, "id", None))
        file = discord.File(io.BytesIO(result.data), filename=result.filename)
        await send(
            content="An asset is waiting for your approval before it is posted.",
            file=file,
            view=view,
        )
        try:
            approved = await asyncio.wait_for(view.decision, timeout=REVIEW_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise ImagineError(OWNER_REVIEW_FAILED) from exc
        if not approved:
            raise ImagineError(OWNER_REVIEW_DENIED)
        return result
    except ImagineError:
        raise
    except Exception as exc:
        raise ImagineError(OWNER_REVIEW_FAILED) from exc


class _OwnerReviewView:
    """Thin wrapper so tests can import this module without discord.ui at collection."""

    def __init__(self, owner_id):
        import discord

        class View(discord.ui.View):
            def __init__(self, expected_id):
                super().__init__(timeout=REVIEW_TIMEOUT_SECONDS)
                self.expected_id = expected_id
                self.decision: asyncio.Future[bool] = asyncio.get_event_loop().create_future()

            async def _decide(self, interaction, value: bool):
                user = getattr(interaction, "user", None)
                if self.expected_id and getattr(user, "id", None) != self.expected_id:
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

        inner = View(owner_id)
        self.decision = inner.decision
        self._view = inner

    def __getattr__(self, name):
        return getattr(self._view, name)
