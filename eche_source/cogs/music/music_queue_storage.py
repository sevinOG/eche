import discord
from core.context_manager import get_home_guild

QUEUE_CHANNEL_NAME = "music-queue"
QUEUE_HEADER = "Queue:\n"


async def ensure_queue_message(bot, guild_id: int | None = None):
    guild = get_home_guild(bot)
    if guild_id:
        g = bot.get_guild(guild_id)
        if g:
            guild = g

    # find or create channel
    channel = discord.utils.get(guild.text_channels, name=QUEUE_CHANNEL_NAME)
    if channel is None:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(send_messages=False)
        }
        channel = await guild.create_text_channel(QUEUE_CHANNEL_NAME, overwrites=overwrites)

    pins = await channel.pins()
    if pins:
        return channel, pins[0]

    msg = await channel.send(QUEUE_HEADER)
    await msg.pin()
    return channel, msg


async def load_queue(bot, guild_id):
    channel, pinned = await ensure_queue_message(bot, guild_id)
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
    channel, pinned = await ensure_queue_message(bot, guild_id)

    lines = []
    for entry in queue_list:
        artist = (entry.get("artist") or "Unknown").replace("\n", " ").strip()
        title = (entry.get("title") or "Unknown Title").replace("\n", " ").strip()
        duration = entry.get("duration") or 0
        lines.append(f"{artist}|{title}|{duration}")

    new_content = QUEUE_HEADER + "\n".join(lines)
    await pinned.edit(content=new_content)
