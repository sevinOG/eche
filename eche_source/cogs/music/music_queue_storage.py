import discord

QUEUE_CHANNEL_NAME = "music-queue"
QUEUE_HEADER = "Queue:\n"


async def ensure_queue_message(bot, guild_id: int | None = None):
    # Music queue is ALWAYS per-server where the command runs.
    # Never fall back to home guild.
    if not guild_id:
        raise RuntimeError("Music queue requires a guild context (run the command in a server).")

    guild = bot.get_guild(guild_id)
    if guild is None:
        raise RuntimeError(f"Bot is not in guild {guild_id} or guild not cached.")

    # find or create channel (per-guild)
    channel = discord.utils.get(guild.text_channels, name=QUEUE_CHANNEL_NAME)
    if channel is None:
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False)
            }
            channel = await guild.create_text_channel(QUEUE_CHANNEL_NAME, overwrites=overwrites)
        except discord.Forbidden:
            raise RuntimeError(f"Missing 'Manage Channels' permission to create #{QUEUE_CHANNEL_NAME} here.")
        except Exception as e:
            raise RuntimeError(f"Failed to create queue channel: {e}")

    try:
        pins = await channel.pins()
    except discord.Forbidden:
        raise RuntimeError(f"Missing 'Read Message History' / 'Manage Messages' to read pins in #{QUEUE_CHANNEL_NAME}.")
    if pins:
        return channel, pins[0]

    try:
        msg = await channel.send(QUEUE_HEADER)
        await msg.pin()
    except discord.Forbidden:
        raise RuntimeError(f"Missing 'Send Messages' + 'Manage Messages' to pin queue header in this server.")
    except Exception as e:
        raise RuntimeError(f"Failed to pin queue header: {e}")

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
