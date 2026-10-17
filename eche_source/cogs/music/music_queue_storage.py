from core.context_manager import get_home_guild
from core.discord_store import THREAD_MUSIC, ensure_bot_thread, ensure_record

QUEUE_HEADER = "Queue:\n"
_PIN_CAP = 1900


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


def _field(value) -> str:
    return (value or "").replace("|", "/").replace("\n", " ").strip()


def _duration_text(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0
    return f"{total // 60}:{total % 60:02d}"


def _duration_seconds(text) -> int:
    raw = (text or "").strip()
    if raw.isdigit():
        return int(raw)
    if raw.count(":") == 1:
        minutes, seconds = raw.split(":", 1)
        if minutes.isdigit() and seconds.isdigit():
            return int(minutes) * 60 + int(seconds)
    return 0


def _entry_line(entry, marker: str) -> str:
    artist = _field(entry.get("artist")) or "Unknown"
    title = _field(entry.get("title")) or "Unknown Title"
    duration = _duration_text(entry.get("duration"))
    query = _field(entry.get("url"))
    body = f"{artist} | {title} | {duration}"
    if query:
        body = f"{body} | {query}"
    if marker:
        return f"{marker} {body}"
    return body


def render_queue(waiting, current=None) -> str:
    """One pin: the song that is playing, then every song still waiting.

    The first line stays exactly "Queue:" so the stored message keeps its header.
    """
    lines = []
    if current:
        lines.append(_entry_line(current, "▶"))
    for index, entry in enumerate(waiting or [], start=1):
        lines.append(_entry_line(entry, f"{index}."))
    text = QUEUE_HEADER + "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    if len(text) <= _PIN_CAP:
        return text
    kept = []
    if current:
        kept.append(_entry_line(current, "▶"))
    hidden = 0
    for index, entry in enumerate(list(waiting or []), start=1):
        trial_lines = kept + [_entry_line(entry, f"{index}.")]
        trial = QUEUE_HEADER + "\n".join(trial_lines) + "\n"
        if len(trial) > _PIN_CAP - 40:
            hidden = len(list(waiting or [])) - (index - 1)
            break
        kept.append(_entry_line(entry, f"{index}."))
    if hidden:
        kept.append(f"(+{hidden} more)")
    text = QUEUE_HEADER + "\n".join(kept)
    if not text.endswith("\n"):
        text += "\n"
    return text


def _parse_line(line: str):
    text = (line or "").strip()
    if not text or text.startswith("("):
        return None
    now = False
    if text.startswith("▶"):
        now = True
        text = text[1:].strip()
    elif ". " in text[:8]:
        head, rest = text.split(". ", 1)
        if head.isdigit():
            text = rest.strip()
    parts = [part.strip() for part in text.split("|")]
    if len(parts) >= 3:
        query = "|".join(parts[3:]).strip() if len(parts) > 3 else ""
        return {
            "artist": parts[0] or "Unknown",
            "title": parts[1] or "Unknown Title",
            "duration": _duration_seconds(parts[2]),
            "url": query or None,
            "now": now,
        }
    return {
        "artist": "Unknown",
        "title": text,
        "duration": 0,
        "url": None,
        "now": now,
    }


def parse_queue(content: str) -> list:
    """Songs to play, now-playing first. Older pins stored only waiting songs."""
    if not (content or "").startswith("Queue:"):
        return []
    waiting = []
    current = None
    for line in content.splitlines()[1:]:
        entry = _parse_line(line)
        if entry is None:
            continue
        now = entry.pop("now")
        if now and current is None:
            current = entry
        else:
            waiting.append(entry)
    if current is not None:
        return [current, *waiting]
    return waiting


async def load_queue(bot):
    channel, pinned = await ensure_queue_message(bot)
    if channel is None or pinned is None:
        return []  # non-home server: in-memory queue only, no persistence
    return parse_queue(pinned.content or "")


async def save_queue(bot, queue_list, current=None):
    channel, pinned = await ensure_queue_message(bot)
    if channel is None or pinned is None:
        return  # non-home server: skip persistence, keep in-memory only
    new_content = render_queue(queue_list, current)
    from core.discord_store import edit_record, refresh_record

    live = await refresh_record(pinned) or pinned
    await edit_record(live, new_content)
