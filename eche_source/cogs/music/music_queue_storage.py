from core.context_manager import get_home_guild
from core.discord_store import THREAD_MUSIC, ensure_bot_thread, ensure_record

QUEUE_HEADER = "Queue:\n"


async def ensure_queue_message(bot):
    # The queue lives in the bot's channel on the home server:
    # bot memory / bot / music-queue.
    # Playback still happens in the server where the command was used.
    if get_home_guild(bot) is None:
        return None, None

    try:
        thread = await ensure_bot_thread(bot, THREAD_MUSIC)
    except Exception as exc:
        raise RuntimeError(f"Failed to open the music queue thread: {exc}") from exc
    if thread is None:
        raise RuntimeError(
            "Could not open the music queue thread in the home server's bot memory category."
        )

    try:
        message = await ensure_record(thread, "Queue:", QUEUE_HEADER)
    except Exception as exc:
        raise RuntimeError(f"Failed to pin the queue header: {exc}") from exc
    if message is None:
        raise RuntimeError("Failed to pin the queue header in the music queue thread.")
    return thread, message


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
    from core.discord_store import edit_record
    await edit_record(pinned, new_content)
