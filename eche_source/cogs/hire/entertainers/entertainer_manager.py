# cogs/hire/entertainers/entertainer_manager.py

import random
import asyncio
from datetime import datetime, timedelta
import discord
import os

from cogs.economy.bank import Bank

# In-memory registry: batch_id -> job dict
ENTERTAINER_JOBS = {}

TICK_INTERVAL_SECONDS = 15 * 60
TICKS_PER_BATCH = 4


def register_entertainer_batch(
    user_id: int,
    units: int,
    guild_id: int,
    channel_id: int,
    batch_id: str,
    created_at: datetime,
    expires_at: datetime,
    invoke_guild_id: int,
    invoke_channel_id: int,
):
    """
    Register a new entertainer batch in memory.
    Batches are stored in HOME_SERVER, but updates go to invoke_guild/channel.
    """
    ENTERTAINER_JOBS[batch_id] = {
        "batch_id": batch_id,
        "user_id": user_id,
        "units": units,
        "home_guild_id": guild_id,
        "home_channel_id": channel_id,
        "invoke_guild_id": invoke_guild_id,
        "invoke_channel_id": invoke_channel_id,
        "created_at": created_at,
        "expires_at": expires_at,
        "ticks_remaining": TICKS_PER_BATCH,
        "next_tick": (created_at + timedelta(seconds=TICK_INTERVAL_SECONDS)).timestamp(),
    }


async def rebuild_jobs_from_pins(bot):
    """
    On startup, rebuild entertainer batches by scanning pinned messages
    in #workers channels under memory-<user_id> categories in the HOME_SERVER only.
    """

    home_id = int(os.getenv("HOME_SERVER_ID"))
    guild = bot.get_guild(home_id)
    if guild is None:
        print("[Entertainers] HOME_SERVER_ID invalid, cannot rebuild jobs.")
        return

    now = datetime.utcnow()

    for category in guild.categories:
        if not category.name.startswith("memory-"):
            continue

        # Extract user ID from category name
        try:
            user_id = int(category.name.replace("memory-", ""))
        except ValueError:
            continue

        workers = discord.utils.get(category.channels, name="workers")
        if workers is None:
            continue

        pins = await workers.pins()
        if not pins:
            continue

        for pinned in pins:
            content = pinned.content
            lines = content.split("\n")

            # Expected format:
            # 0: Batch ID: <uuid>
            # 1: Units: <number>
            # 2: Purchased by: <@user>
            # 3: Server ID: <home_guild_id>
            # 4: Channel ID: <home_channel_id>
            # 5: Invoke Server ID: <invoke_guild_id>
            # 6: Invoke Channel ID: <invoke_channel_id>
            # 7: Created at (UTC): <iso>
            # 8: Expires at (UTC): <iso>
            # 9: Ticks remaining: <int>   (ignored, we recompute)
            try:
                batch_id = lines[0].split("Batch ID:")[1].strip()
                units = int(lines[1].split("Units:")[1].strip())
                home_guild_id = int(lines[3].split("Server ID:")[1].strip())
                home_channel_id = int(lines[4].split("Channel ID:")[1].strip())
                invoke_guild_id = int(lines[5].split("Invoke Server ID:")[1].strip())
                invoke_channel_id = int(lines[6].split("Invoke Channel ID:")[1].strip())
                created_str = lines[7].split("Created at (UTC):")[1].strip()
                expires_str = lines[8].split("Expires at (UTC):")[1].strip()

                created_at = datetime.fromisoformat(created_str)
                expires_at = datetime.fromisoformat(expires_str)

            except Exception as e:
                print(f"[Entertainers] Failed to parse pinned batch: {e}")
                continue

            # Enforce home server only
            if home_guild_id != home_id:
                continue

            # Skip expired
            if now >= expires_at:
                continue

            # Compute how many ticks should have happened since creation
            elapsed_seconds = (now - created_at).total_seconds()
            ticks_elapsed = int(elapsed_seconds // TICK_INTERVAL_SECONDS)
            ticks_elapsed = max(0, min(TICKS_PER_BATCH, ticks_elapsed))

            ticks_remaining = max(0, TICKS_PER_BATCH - ticks_elapsed)
            if ticks_remaining <= 0:
                # Batch fully completed while bot was offline
                continue

            # Next tick is the next interval after now
            next_tick_time = now + timedelta(seconds=TICK_INTERVAL_SECONDS)

            ENTERTAINER_JOBS[batch_id] = {
                "batch_id": batch_id,
                "user_id": user_id,
                "units": units,
                "home_guild_id": home_guild_id,
                "home_channel_id": home_channel_id,
                "invoke_guild_id": invoke_guild_id,
                "invoke_channel_id": invoke_channel_id,
                "created_at": created_at,
                "expires_at": expires_at,
                "ticks_remaining": ticks_remaining,
                "next_tick": next_tick_time.timestamp(),
            }

            print(
                f"[Entertainers] Rebuilt batch {batch_id} for user {user_id} "
                f"({units} units, {ticks_remaining} ticks remaining)"
            )


async def entertainer_background_loop(bot):
    """
    Background loop:
    - Rebuilds batches from pinned messages on startup (HOME_SERVER only)
    - Every minute, checks which batches need a tick
    - Applies scaled payouts (per batch, scaled by units)
    - Updates user bank
    - Sends updates to the invoking server/channel
    - Expires batches when done
    """

    await bot.wait_until_ready()
    bank: Bank = bot.get_cog("Bank")

    await rebuild_jobs_from_pins(bot)

    while not bot.is_closed():
        now_ts = datetime.utcnow().timestamp()
        now_dt = datetime.utcnow()
        to_remove = []

        for batch_id, job in list(ENTERTAINER_JOBS.items()):
            if job["ticks_remaining"] <= 0 or now_dt >= job["expires_at"]:
                to_remove.append(batch_id)
                continue

            if now_ts >= job["next_tick"]:
                # Schedule next tick
                job["ticks_remaining"] -= 1
                job["next_tick"] = now_ts + TICK_INTERVAL_SECONDS

                units = job["units"]
                user_id = job["user_id"]
                invoke_guild_id = job["invoke_guild_id"]
                invoke_channel_id = job["invoke_channel_id"]

                # Roll outcome (SCALING BY UNITS)
                roll = random.random()

                if roll < 0.30:
                    base = 35000
                    amount = base * units
                    msg = f"Your {units} entertainers earned **+{amount}** coins!"
                elif roll < 0.60:
                    base = 5000
                    amount = base * units
                    msg = f"Your {units} entertainers earned **+{amount}** coins!"
                elif roll < 0.80:
                    base = -15000
                    amount = base * units
                    msg = f"Your {units} entertainers cost you **{amount}** coins in legal fees!"
                elif roll < 0.90:
                    base = 50000
                    amount = base * units
                    msg = f"Your {units} entertainers earned **+{amount}** coins!"
                else:
                    amount = 0
                    msg = f"Your {units} entertainers had no earnings this cycle."

                # Update bank
                member = bot.get_user(user_id)
                if member and bank:
                    bal = await bank.load_bank(member)
                    bal += amount
                    await bank.save_bank(member, bal)

                # Send update to the invoking server/channel
                channel = None
                guild = bot.get_guild(invoke_guild_id)
                if guild:
                    channel = guild.get_channel(invoke_channel_id)

                if channel:
                    await channel.send(f"<@{user_id}> {msg} (Batch: `{batch_id}`)")

                # If no ticks left or expired, mark for removal
                if job["ticks_remaining"] <= 0 or datetime.utcnow() >= job["expires_at"]:
                    to_remove.append(batch_id)

        for bid in to_remove:
            ENTERTAINER_JOBS.pop(bid, None)

        await asyncio.sleep(60)
