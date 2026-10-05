import discord

from core.context_manager import HOME_SERVER_ID, get_home_guild

QUEUE_CHANNEL_NAME = "music-queue"
QUEUE_HEADER = "Queue:\n"


async def ensure_queue_message(bot):
    # Music queue is ALWAYS created/stored in the BOT'S HOME SERVER.
    # This avoids "Missing Permissions" (50013) when running ?play in other servers
    # where the bot may not have Manage Channels / Manage Messages.
    # The actual voice playback still happens in the server where the command was used.
    guild = get_home_guild(bot)
    if guild is None:
        # Non-home server or HOME_SERVER_ID not set / bot not in home guild.
        # Allow playback with in-memory queue only (no persistence).
        return None, None

    # find or create channel (in home guild only)
    channel = discord.utils.get(guild.text_channels, name=QUEUE_CHANNEL_NAME)
    if channel is None:
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False)
            }
            channel = await guild.create_text_channel(QUEUE_CHANNEL_NAME, overwrites=overwrites)
        except discord.Forbidden:
            raise RuntimeError(f"Missing 'Manage Channels' permission to create #{QUEUE_CHANNEL_NAME} in HOME server.")
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


async def load_queue(bot):
    channel, pinned = await ensure_queue_message(bot)
    if channel is None or pinned is None:
        return []  # non-home server: in-memory queue only, no persistence
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


async def save_queue(bot, queue_list):
    channel, pinned = await ensure_queue_message(bot)
    if channel is None or pinned is None:
        return  # non-home server: skip persistence, keep in-memory only
    lines = []
    for entry in queue_list:
        artist = (entry.get("artist") or "Unknown").replace("\n", " ").strip()
        title = (entry.get("title") or "Unknown Title").replace("\n", " ").strip()
        duration = entry.get("duration") or 0
        lines.append(f"{artist}|{title}|{duration}")

    new_content = QUEUE_HEADER + "\n".join(lines)
    await pinned.edit(content=new_content)
