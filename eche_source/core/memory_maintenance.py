# core/memory_maintenance.py
# Background upkeep for long-term memory, every SWEEP_SECONDS:
#   * people whose chat went quiet get their waiting messages folded into
#     long-term memory (core/context_summarizer.fold_person, reason "idle");
#   * open episodes that went quiet are closed and summarized;
#   * once a night (after NIGHT_HOUR local time), old episodes are
#     consolidated into weekly and monthly summaries.
# Every pass writes one audit line. Nothing here deletes a pin.

from __future__ import annotations

import asyncio
import time
from datetime import datetime

SWEEP_SECONDS = 600
NIGHT_HOUR = 4
_MAX_FOLDS_PER_SWEEP = 20


def consolidation_due(last: float, now: float | None = None) -> bool:
    """Once per local day, after NIGHT_HOUR, or when a day has passed."""
    now = time.time() if now is None else float(now)
    if not last:
        return datetime.fromtimestamp(now).hour >= NIGHT_HOUR
    if now - last >= 36 * 3600:
        return True
    today = datetime.fromtimestamp(now)
    before = datetime.fromtimestamp(last)
    return today.date() > before.date() and today.hour >= NIGHT_HOUR


async def sweep(bot, *, now: float | None = None) -> dict:
    """One pass. Returns counts. Errors are logged and the pass goes on."""
    from core import episodic
    from core.context_manager import get_home_guild
    from core.context_summarizer import fold_person, fold_reason
    from core.personal_recent import users_waiting

    now = time.time() if now is None else float(now)
    report = {"folds": 0, "episodes_closed": 0, "consolidated": 0}
    try:
        from core.audit import safe_activity

        safe_activity("memory-maintenance", outcome="success", action="sweep")
    except Exception:
        pass
    home = get_home_guild(bot) if bot is not None else None
    for uid in users_waiting()[:200]:
        if report["folds"] >= _MAX_FOLDS_PER_SWEEP:
            break
        try:
            if fold_reason(uid, now) != "idle":
                continue
            if await fold_person(bot, home, uid, None, reason="idle"):
                report["folds"] += 1
        except Exception as exc:
            print(f"[memory] idle update failed for {uid}: {type(exc).__name__}", flush=True)
    for guild_id in episodic.guilds_on_disk():
        try:
            report["episodes_closed"] += await episodic.close_idle(bot, guild_id, now=now)
        except Exception as exc:
            print(f"[episodic] idle close failed for {guild_id}: {type(exc).__name__}", flush=True)
        try:
            if consolidation_due(episodic.consolidated_at(guild_id), now):
                report["consolidated"] += await episodic.consolidate(bot, guild_id, now=now)
        except Exception as exc:
            print(f"[episodic] consolidation failed for {guild_id}: {type(exc).__name__}", flush=True)
    return report


async def memory_maintenance_loop(bot) -> None:
    await bot.wait_until_ready()
    try:
        from core.audit import safe_activity

        safe_activity("memory-maintenance", outcome="success", action="start")
    except Exception:
        pass
    while not bot.is_closed():
        try:
            await sweep(bot)
        except Exception as exc:
            print(f"[memory] maintenance pass failed: {type(exc).__name__}", flush=True)
        await asyncio.sleep(SWEEP_SECONDS)
