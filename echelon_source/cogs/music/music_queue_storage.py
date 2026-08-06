import discord
from core.context_manager import get_home_guild

QUEUE_CHANNEL_ID = 1503819397255532684
QUEUE_HEADER = "Queue:\n"


async def ensure_queue_message(bot):
    home = get_home_guild(bot)
    channel = home.get_channel(QUEUE_CHANNEL_ID)

    if channel is None:
        raise RuntimeError(f"Queue channel {QUEUE_CHANNEL_ID} not found.")

    pins = await channel.pins()
    if pins:
        return channel, pins[0]

    msg = await channel.send(QUEUE_HEADER)
    await msg.pin()
    return channel, msg


async def load_queue(bot, guild_id):
    channel, pinned = await ensure_queue_message(bot)
    content = pinned.content or ""

    if not content.startswith("Queue:"):
        return []

    lines = content.splitlines()[1:]
    entries = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        parts = line.split("|")
        if len(parts) == 3:
            artist, title, duration = parts
            entries.append({
                "artist": artist.strip() or "Unknown",
                "title": title.strip() or "Unknown Title",
                "duration": int(duration.strip()) if duration.strip().isdigit() else None,
                "url": None  # URL intentionally not stored
            })
        else:
            entries.append({
                "artist": "Unknown",
                "title": line,
                "duration": None,
                "url": None
            })

    return entries


async def save_queue(bot, guild_id, queue_list):
    channel, pinned = await ensure_queue_message(bot)

    lines = []
    for entry in queue_list:
        artist = (entry.get("artist") or "Unknown").replace("\n", " ").strip()
        title = (entry.get("title") or "Unknown Title").replace("\n", " ").strip()
        duration = entry.get("duration") or 0
        lines.append(f"{artist}|{title}|{duration}")

    new_content = QUEUE_HEADER + "\n".join(lines)
    await pinned.edit(content=new_content)
