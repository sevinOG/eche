"""Home-server memory layout.

One category, ``bot memory``:

* a channel per user, ``user-{id}``, with a thread for each record
  (context, bank, items, workers)
* the bot's own ``bot`` channel, with ``music-queue`` and ``reminders`` threads

The context thread holds two pinned messages: the user's context, and the
bot's self context for that user. Old ``memory-{id}`` categories are left
alone. This store does not read them.
"""

from __future__ import annotations

import asyncio

import discord

from core.debuglog import dprint
from core.home_id import home_server_id_from_env

CATEGORY_NAME = "bot memory"
BOT_CHANNEL_NAME = "bot"
USER_CHANNEL_PREFIX = "user-"

THREAD_CONTEXT = "context"
THREAD_BANK = "bank"
THREAD_ITEMS = "items"
THREAD_WORKERS = "workers"
THREAD_MUSIC = "music-queue"
THREAD_REMINDERS = "reminders"
THREAD_EPISODIC = "episodic"

USER_THREADS = (THREAD_CONTEXT, THREAD_BANK, THREAD_ITEMS, THREAD_WORKERS)

USER_CONTEXT_HEADER = "Context for "
# The bot pin must not start with "Context for ". find_record returns the
# first pin with that prefix, so a shared prefix would hand back the wrong one.
BOT_CONTEXT_HEADER = "Eche with "
OLD_BOT_CONTEXT_HEADER = "Self Conversation Data"
BANK_HEADER = "BANK DATA"
OWNED_HEADER = "OWNED ITEMS"
HOSTED_HEADER = "HOSTED ITEMS"

_layout_lock = asyncio.Lock()
# Message ids live only for this process. Discord stays the copy.
# A record is pinned when it is created, then found by this cache.
_records: dict[tuple[int, str], object] = {}
_missing: set[tuple[int, str]] = set()
_pins_scanned: set[int] = set()
_threads: dict[tuple[int, str], object] = {}
_ready_channels: set[int] = set()


def _header_key(content: str) -> str:
    return (content or "").split("\n", 1)[0].strip()


def bot_memory_header(label: str) -> str:
    """Title for one person's bot pin. Same shape as the user pin's title."""
    name = " ".join(str(label or "").split()) or "them"
    return f"Eche with {name}:\n\n"


def bot_memory_initial(label: str) -> str:
    return bot_memory_header(label) + "Summary:\n(none yet)\n"


def renamed_bot_pin(header: str, content: str) -> str:
    """Keep Summary and New. An older title is replaced. An Eche with pin stays."""
    text = content or ""
    first = text.lstrip().split("\n", 1)[0].strip()
    if first.startswith(BOT_CONTEXT_HEADER):
        return text
    if "Summary:" in text:
        body = text[text.index("Summary:") :]
        if not body.endswith("\n"):
            body += "\n"
        return header + body
    return header + "Summary:\n(none yet)\n"


def remember_record(parent_id: int, message) -> None:
    """Keep a message we already resolved so the next turn does not list pins."""
    key = _header_key(getattr(message, "content", "") or "")
    if not parent_id or not key:
        return
    parent_id = int(parent_id)
    _records[(parent_id, key)] = message
    _missing.discard((parent_id, key))


def cached_record(parent_id: int, header: str):
    """The cached message whose text starts with `header`, if this process has it."""
    if not parent_id or not header:
        return None
    parent_id = int(parent_id)
    hit = _records.get((parent_id, header))
    if hit is not None:
        return hit
    for (pid, _key), message in _records.items():
        if pid != parent_id:
            continue
        if (getattr(message, "content", "") or "").startswith(header):
            return message
    return None


def forget_record(message) -> None:
    """Drop one message so the next lookup asks Discord again."""
    mid = getattr(message, "id", None)
    if mid is None:
        return
    for key, stored in list(_records.items()):
        if getattr(stored, "id", None) == mid:
            _records.pop(key, None)
            _missing.discard(key)


def _forget_channel_tree(channel_id: int) -> None:
    """Drop a deleted user channel and the threads that were cached under it."""
    channel_id = int(channel_id)
    thread_ids = []
    for key, thread in list(_threads.items()):
        parent_id = getattr(thread, "parent_id", None)
        if key[0] == channel_id or parent_id == channel_id:
            thread_id = getattr(thread, "id", None)
            if thread_id:
                thread_ids.append(int(thread_id))
            _threads.pop(key, None)
    forget_parent(channel_id)
    for thread_id in thread_ids:
        forget_parent(thread_id)


def forget_parent(parent_id: int) -> None:
    """Drop cached records and the pin scan for one thread or channel."""
    parent_id = int(parent_id)
    _pins_scanned.discard(parent_id)
    _ready_channels.discard(parent_id)
    for key in [key for key in _records if key[0] == parent_id]:
        _records.pop(key, None)
        _missing.discard(key)
    for key in [key for key in _threads if key[0] == parent_id]:
        _threads.pop(key, None)


async def refresh_record(message):
    """Re-read one stored message before changing it.

    The GUI saves from another process. A cached copy would overwrite that
    edit. discord.py 2 also leaves the old object stale after Message.edit.
    """
    if message is None:
        return None
    channel = getattr(message, "channel", None)
    mid = getattr(message, "id", None)
    if channel is None or mid is None or not hasattr(channel, "fetch_message"):
        return message
    try:
        fresh = await channel.fetch_message(mid)
    except Exception as exc:
        dprint(f"[discord_store] refresh failed: {exc}")
        return message
    content = getattr(fresh, "content", "") or ""
    for held in (message, fresh):
        if getattr(held, "content", None) != content:
            try:
                held.content = content
            except Exception:
                pass
    parent_id = getattr(channel, "id", None)
    if parent_id:
        remember_record(int(parent_id), fresh)
    return fresh


async def edit_record(message, content: str) -> None:
    """Edit a stored message. A failed edit forgets the id so it can be found again.

    discord.py 2 returns a new Message and leaves the old one's text unchanged.
    The cache has to keep the returned message, or the next prompt reads the
    pin from before this edit.
    """
    try:
        edited = await message.edit(content=content)
    except Exception:
        forget_record(message)
        channel = getattr(message, "channel", None)
        parent_id = getattr(channel, "id", None)
        if parent_id:
            # The pin list we already trusted is stale if this id is gone.
            _pins_scanned.discard(int(parent_id))
        raise
    fresh = edited if edited is not None else message
    # A holder of the old object, including this cache, must see the new text
    # even if the library did not write it back.
    for held in (fresh, message):
        if getattr(held, "content", None) != content:
            try:
                held.content = content
            except Exception:
                pass
    channel = getattr(fresh, "channel", None) or getattr(message, "channel", None)
    parent_id = getattr(channel, "id", None)
    if parent_id:
        forget_record(message)
        if fresh is not message:
            forget_record(fresh)
        remember_record(parent_id, fresh)


def home_guild(bot):
    return bot.get_guild(home_server_id_from_env())


def user_channel_name(user_id) -> str:
    return f"{USER_CHANNEL_PREFIX}{int(user_id)}"


def parse_user_channel_name(name: str) -> int | None:
    raw = (name or "").strip().lower()
    prefix = USER_CHANNEL_PREFIX
    if not raw.startswith(prefix):
        return None
    tail = raw[len(prefix) :]
    if not tail.isdigit() or len(tail) < 15:
        return None
    return int(tail)


def find_category(guild) -> discord.CategoryChannel | None:
    if guild is None:
        return None
    for category in list(getattr(guild, "categories", None) or []):
        if (category.name or "").strip().lower() == CATEGORY_NAME:
            return category
    return None


def find_named_channel(parent, name: str):
    """Text channel `name` inside a category, or None."""
    if parent is None:
        return None
    want = name.lower()
    for channel in list(getattr(parent, "text_channels", None) or getattr(parent, "channels", None) or []):
        if (getattr(channel, "name", "") or "").lower() == want:
            return channel
    return None


def user_channel(guild, user_id):
    category = find_category(guild)
    if category is None:
        return None
    return find_named_channel(category, user_channel_name(user_id))


def bot_channel(guild):
    category = find_category(guild)
    if category is None:
        return None
    return find_named_channel(category, BOT_CHANNEL_NAME)


def list_user_channels(guild) -> list[tuple[int, discord.TextChannel]]:
    category = find_category(guild)
    if category is None:
        return []
    found = []
    for channel in list(getattr(category, "text_channels", None) or []):
        user_id = parse_user_channel_name(getattr(channel, "name", "") or "")
        if user_id is not None:
            found.append((user_id, channel))
    return found


def list_user_ids(guild) -> set[int]:
    return {user_id for user_id, _channel in list_user_channels(guild)}


async def list_pins(parent) -> list:
    if parent is None:
        return []
    try:
        pins = await parent.pins()
    except Exception as exc:
        dprint(f"[discord_store] pins failed: {exc}")
        return []
    return list(pins or [])


async def find_record(parent, header: str, allow_history: bool = True):
    """Message whose content starts with `header`.

    The pin list for a thread is read once per process. A message is not
    pinned here. Pinning happens only when the record is created.
    `allow_history` is off for a prompt read, so a missing pin does not
    walk the channel before the reply. The later save still searches.
    """
    if parent is None or not header:
        return None
    parent_id = int(parent.id)
    if (parent_id, header) in _missing:
        return None
    hit = cached_record(parent_id, header)
    if hit is not None:
        return hit
    found = None
    if parent_id not in _pins_scanned:
        try:
            listed = await parent.pins()
        except Exception as exc:
            # An unread pin list is not the same as an empty one. Remembering
            # the miss would hide the real message and create a second copy.
            dprint(f"[discord_store] pins failed: {exc}")
            return None
        for message in list(listed or []):
            remember_record(parent_id, message)
            if found is None and (message.content or "").startswith(header):
                found = message
        _pins_scanned.add(parent_id)
        if found is not None:
            return found
        found = cached_record(parent_id, header)
        if found is not None:
            return found
    if not allow_history:
        return None
    try:
        async for message in parent.history(limit=40):
            if (message.content or "").startswith(header):
                remember_record(parent_id, message)
                return message
    except Exception as exc:
        dprint(f"[discord_store] history scan failed: {exc}")
        return None
    _missing.add((parent_id, header))
    return None


async def _bot_overwrites(guild) -> dict:
    """Hide the category from everyone else and let the bot manage it.

    A deny on @everyone with no allow for the bot would lock the bot out,
    so a missing bot member means no overwrites at all.
    """
    me = getattr(guild, "me", None)
    if me is None:
        state_user = getattr(getattr(guild, "_state", None), "user", None)
        if state_user is not None:
            try:
                me = await guild.fetch_member(state_user.id)
            except Exception:
                me = None
    if me is None:
        dprint("[discord_store] bot member missing; leaving the category visible")
        return {}
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            manage_channels=True,
            create_public_threads=True,
            send_messages_in_threads=True,
            manage_threads=True,
        ),
    }


async def _ensure_category_locked(guild):
    category = find_category(guild)
    if category is not None:
        return category
    try:
        category = await guild.create_category(
            CATEGORY_NAME,
            overwrites=await _bot_overwrites(guild),
            reason="Eche bot memory",
        )
    except Exception as exc:
        dprint(f"[discord_store] could not create category: {exc}")
        return None
    dprint(f"[discord_store] created category {CATEGORY_NAME}")
    return category


async def _ensure_text_channel_locked(category, name: str, reason: str):
    channel = find_named_channel(category, name)
    if channel is not None:
        return channel
    try:
        overwrites = await _bot_overwrites(category.guild)
        channel = await category.create_text_channel(
            name,
            reason=reason,
            **({"overwrites": overwrites} if overwrites else {}),
        )
    except Exception as exc:
        dprint(f"[discord_store] could not create #{name}: {exc}")
        return None
    dprint(f"[discord_store] created #{name}")
    return channel


async def find_thread(channel, name: str):
    if channel is None or not name:
        return None
    want = name.lower()

    def _match(threads, *, require_parent: bool):
        for thread in threads or []:
            parent_id = getattr(thread, "parent_id", None)
            if require_parent and parent_id != channel.id:
                continue
            if parent_id not in (None, channel.id):
                continue
            if (thread.name or "").lower() == want:
                return thread
        return None

    found = _match(getattr(channel, "threads", None), require_parent=False)
    if found is not None:
        return found

    guild = getattr(channel, "guild", None)
    if guild is not None:
        try:
            active = await guild.active_threads()
        except Exception:
            active = list(getattr(guild, "threads", None) or [])
        found = _match(active, require_parent=True)
        if found is not None:
            return found

    try:
        async for thread in channel.archived_threads(limit=100):
            if (thread.name or "").lower() == want:
                return thread
    except Exception as exc:
        dprint(f"[discord_store] archived threads failed: {exc}")
    return None


async def _open_thread(thread):
    if thread is None:
        return None
    if getattr(thread, "archived", False):
        try:
            await thread.edit(archived=False)
        except Exception as exc:
            dprint(f"[discord_store] could not unarchive {thread.name}: {exc}")
    return thread


async def _ensure_thread_locked(channel, name: str):
    if channel is None:
        return None
    key = (int(channel.id), name.lower())
    cached = _threads.get(key)
    if cached is not None:
        return await _open_thread(cached)
    thread = await _open_thread(await find_thread(channel, name))
    if thread is not None:
        _threads[key] = thread
        return thread
    last_error = None
    for minutes in (10080, 4320, 1440, 60):
        try:
            thread = await channel.create_thread(
                name=name,
                type=discord.ChannelType.public_thread,
                auto_archive_duration=minutes,
                reason=f"Eche {name}",
            )
            dprint(f"[discord_store] created thread {name} in #{channel.name}")
            _threads[(int(channel.id), name.lower())] = thread
            return thread
        except Exception as exc:
            last_error = exc
    dprint(f"[discord_store] could not create thread {name}: {last_error}")
    return None


async def _ensure_record_locked(parent, header: str, initial: str):
    found = await find_record(parent, header)
    if found is not None:
        return found
    # `_missing` is set only after a pin read and a history read both finished
    # without the message. A failed read leaves it unset so we do not create
    # a second copy of a message we could not see.
    if parent is None or (int(parent.id), header) not in _missing:
        return None
    try:
        message = await parent.send(initial)
    except Exception as exc:
        dprint(f"[discord_store] could not write {header!r}: {exc}")
        return None
    try:
        await message.pin()
    except Exception as exc:
        dprint(f"[discord_store] could not pin {header!r}: {exc}")
    remember_record(parent.id, message)
    _missing.discard((int(parent.id), header))
    return message


async def _rewrite_bot_title(message, header: str) -> None:
    content = getattr(message, "content", "") or ""
    new_text = renamed_bot_pin(header, content)
    if new_text != content:
        await edit_record(message, new_text)


async def _adopt_bot_record_locked(parent, label: str):
    """The bot pin. An older title is renamed on that same message."""
    if parent is None:
        return None
    header = bot_memory_header(label)
    # Look up the old title first. A miss on the new title is remembered,
    # and the next ensure would create a second pin beside the old one.
    old = await find_record(parent, OLD_BOT_CONTEXT_HEADER)
    if old is not None:
        try:
            await _rewrite_bot_title(old, header)
        except Exception as exc:
            dprint(f"[discord_store] bot pin rename failed: {exc}")
        return old
    return await _ensure_record_locked(parent, BOT_CONTEXT_HEADER, bot_memory_initial(label))


async def adopt_bot_record(parent, label: str):
    if parent is None:
        return None
    async with _layout_lock:
        return await _adopt_bot_record_locked(parent, label)


async def find_bot_record(parent):
    """The bot pin, old title or new. Does not create one."""
    if parent is None:
        return None
    async with _layout_lock:
        current = await find_record(parent, BOT_CONTEXT_HEADER)
        if current is not None:
            return current
        # A miss is stored. Drop it so a later create can still run.
        _missing.discard((int(parent.id), BOT_CONTEXT_HEADER))
        return await find_record(parent, OLD_BOT_CONTEXT_HEADER)


async def ensure_category(guild):
    if guild is None:
        return None
    async with _layout_lock:
        return await _ensure_category_locked(guild)


async def ensure_bot_channel(guild):
    if guild is None:
        return None
    async with _layout_lock:
        category = await _ensure_category_locked(guild)
        if category is None:
            return None
        return await _ensure_text_channel_locked(category, BOT_CHANNEL_NAME, "Eche shared memory")


async def find_bot_thread(bot, name: str):
    """Existing thread on the bot channel, or None. Does not create it."""
    channel = bot_channel(home_guild(bot))
    if channel is None:
        return None
    return await _open_thread(await find_thread(channel, name))


async def ensure_bot_thread(bot, name: str):
    guild = home_guild(bot)
    channel = await ensure_bot_channel(guild)
    if channel is None:
        return None
    async with _layout_lock:
        return await _ensure_thread_locked(channel, name)


async def ensure_user_channel(guild, user_id, username: str | None = None):
    if guild is None:
        return None
    async with _layout_lock:
        return await _ensure_user_channel_locked(guild, user_id, username)


async def _ensure_user_channel_locked(guild, user_id, username: str | None = None):
    category = await _ensure_category_locked(guild)
    if category is None:
        return None
    name = user_channel_name(user_id)
    channel = await _ensure_text_channel_locked(category, name, f"Eche memory for {user_id}")
    if channel is None:
        return None
    if username and not (channel.topic or "").strip():
        try:
            await channel.edit(topic=f"Memory for {username} ({int(user_id)})")
        except Exception:
            pass
    if int(channel.id) in _ready_channels:
        return channel
    # Every user channel carries the same four threads. Context gets both
    # pins here, once. Later turns reuse the cached message ids.
    label = username or str(int(user_id))
    context_thread = None
    threads = []
    for thread_name in USER_THREADS:
        thread = await _ensure_thread_locked(channel, thread_name)
        threads.append(thread)
        if thread_name == THREAD_CONTEXT:
            context_thread = thread
    if context_thread is not None:
        user_record = await _ensure_record_locked(
            context_thread,
            USER_CONTEXT_HEADER,
            f"Context for {label}:\n",
        )
        bot_record = await _adopt_bot_record_locked(context_thread, label)
        if user_record is not None and bot_record is not None and all(threads):
            _ready_channels.add(int(channel.id))
    return channel


async def ensure_user_thread(guild, user_id, name: str, username: str | None = None):
    if guild is None:
        return None
    async with _layout_lock:
        channel = await _ensure_user_channel_locked(guild, user_id, username)
        if channel is None:
            return None
        return await _ensure_thread_locked(channel, name)


async def lookup_record(parent, header: str, allow_history: bool = False):
    """Find a stored message. Does not create one and does not pin."""
    if parent is None or not header:
        return None
    async with _layout_lock:
        return await find_record(parent, header, allow_history=allow_history)


async def ensure_record(parent, header: str, initial: str):
    if parent is None:
        return None
    async with _layout_lock:
        return await _ensure_record_locked(parent, header, initial)


async def delete_user_channel(guild, user_id) -> bool:
    channel = user_channel(guild, user_id)
    if channel is None:
        return False
    try:
        await channel.delete(reason=f"Eche opt-out {user_id}")
        _forget_channel_tree(channel.id)
    except Exception as exc:
        dprint(f"[discord_store] could not delete user channel {user_id}: {exc}")
        return False
    return True
