# context_summarizer.py
# The one long-term memory update path, for both memories kept per person:
#   * side "user": what Eche knows about the person ("Context for ..." pin)
#   * side "bot":  Eche's record of its own outputs with them ("Eche with ...")
#
# Both live as structured JSON (core/memory_store.py). After memory_fold_every
# of the person's messages, or once the chat has been quiet for
# 30 minutes (FOLD_IDLE_MINUTES), both sides of the waiting conversation go to the
# summarizer once per memory. It replies with JSON edits; the code applies
# them, then the Discord pin is rewritten as a rendered copy.
#
# A person with no JSON yet is migrated once: the old prose on their pin is
# sorted into sections under the same rules, and kept as a legacy backup.
# A failed or unreadable reply changes nothing and keeps the waiting messages.

from __future__ import annotations

import asyncio
import re
import time

from core.client import call_groq_raw, drop_safety_preamble
from core.summary_safety import scrub_summary, with_summary_rule
from core.summarizer_prompt import SUMMARIZER_TEMPERATURE, get_summarizer_model

# Room for the JSON edits in one reply.
OPS_CHARS = 2400
# Newest part of the waiting conversation that the summarizer reads.
CONVERSATION_CHARS = 7000
_LINE_CHARS = 600
_LOCKS: dict[int, asyncio.Lock] = {}

# Reject API failures and instruction-echo so they never become "memory"
_BAD_SUMMARY_MARKERS = (
    "rate limit",
    "http 429",
    "http 401",
    "http 404",
    "http 500",
    "groq sdk error",
    "groq (cloud) error",
    "package is not installed",
    "model not found",
    "your job:",
    "conversation to summarize",
    "now write a single",
    "material to compress",
    "output rules",
    "do not mention that this is a summary",
    "existing summary:",
    "existing memory:",
    "write the condensed summary",
    "do not retell",
    "one short line per subject",
    "do not drop a subject",
    "reply with the updated memory only",
    "reply with the shortened memory only",
    "reply with the updated summary only",
    "reply with the shortened summary only",
    "shorter is better",
    "drop: greetings",
    "no headings, no intro",
)



def _is_bad_llm_output(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    try:
        from core.blocklist import REFUSAL
        from core.guardrails import is_refusal_text

        if t == REFUSAL or is_refusal_text(t):
            return True
    except Exception:
        if t == "Nope. Not going there.":
            return True
    if t.upper().startswith("ERROR"):
        return True
    low = t.lower()
    if any(m in low for m in _BAD_SUMMARY_MARKERS):
        return True
    # Mostly a copy of the prompt job list
    if low.count("- ") >= 5 and "preserve" in low and "ignore" in low:
        return True
    return False


def _clean_summary_text(text: str) -> str:
    t = drop_safety_preamble(text or "")
    # Drop accidental fences / labels
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = re.sub(r"^(summary|memory|condensed summary)\s*:\s*", "", t, flags=re.I)
    return scrub_summary(t.strip())



# A bullet that opens on the person. The bot pin is never written this way.
_USER_LEAD = re.compile(r"^(?:-\s*)?(?:the\s+)?users?\b", re.I)


def user_centric_bullets(text: str) -> list[str]:
    """Bullets on a bot fold that are about the person, not eche's own output."""
    out = []
    for line in (text or "").splitlines():
        body = line.strip()
        if body and _USER_LEAD.match(body):
            out.append(body)
    return out


def bot_voice_only(text: str) -> str:
    """Drop bullets that open on the person. The rest of the fold stays."""
    kept = [
        line
        for line in (text or "").splitlines()
        if line.strip() and not _USER_LEAD.match(line.strip())
    ]
    return "\n".join(kept).strip()


def _lock(user_id: int) -> asyncio.Lock:
    lock = _LOCKS.get(int(user_id))
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[int(user_id)] = lock
    return lock


async def _memory_completion(prompt: str, limit: int) -> str:
    """One summarizer call: safety rule first, fixed model, low temperature."""
    from core.context_caps import summary_tokens

    raw = await call_groq_raw(
        with_summary_rule(prompt),
        model=get_summarizer_model(),
        max_completion_tokens=summary_tokens(limit),
        temperature=SUMMARIZER_TEMPERATURE,
    )
    return drop_safety_preamble(raw or "")


def parse_ops(raw: str) -> dict | None:
    """The JSON edits, or None for an error, a refusal, or anything unreadable."""
    from core.episodic import _parse_json

    data = _parse_json(raw)
    if data is None:
        return None
    known = {"add", "update", "remove", "touch", "kept"}
    # {} (or only empty lists) is a valid "nothing to change".
    if not data:
        return {}
    if not any(key in data for key in known):
        return None
    return {key: data[key] for key in known if isinstance(data.get(key), list)}


async def _ask_ops(prompt: str) -> dict | None:
    try:
        raw = await _memory_completion(prompt, OPS_CHARS)
    except Exception as exc:
        print(f"[context_summarizer] memory call failed: {type(exc).__name__}", flush=True)
        return None
    ops = parse_ops(raw)
    if ops is None:
        print("[context_summarizer] memory reply was not usable JSON; nothing changed.", flush=True)
    return ops


def conversation_text(user_rows, bot_rows, limit: int = CONVERSATION_CHARS) -> str:
    """Both sides, oldest first. Lines from an older pin come first."""
    from core.content_filter import clean_memory_text

    rows: list[tuple[float, int, str]] = []
    for order, (at, text) in enumerate(user_rows or []):
        label = "Them" if at else "Earlier note"
        rows.append((float(at or 0), order, f"{label}: {text}"))
    for order, (at, text) in enumerate(bot_rows or []):
        label = "Eche" if at else "Earlier note (Eche)"
        rows.append((float(at or 0), 10_000 + order, f"{label}: {text}"))
    rows.sort(key=lambda row: (row[0], row[1]))
    lines: list[str] = []
    for _at, _order, line in rows:
        try:
            from core.standing import hide_mentions
            line = hide_mentions(line)
        except Exception:
            pass
        body = clean_memory_text(" ".join(line.split()))
        if len(body) > _LINE_CHARS:
            body = body[: _LINE_CHARS - 1].rstrip() + "…"
        if body:
            lines.append(body)
    out: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) + 1 > limit:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(reversed(out))


def _names_of(*things) -> list[str]:
    names: list[str] = []
    for thing in things:
        if thing is None:
            continue
        if isinstance(thing, str):
            names.append(thing)
            continue
        for attr in ("name", "display_name", "global_name", "nick"):
            value = getattr(thing, attr, None)
            if value:
                names.append(str(value))
    return names


def _own_names(bot, guild, user_id: int, username) -> list[str]:
    member = guild.get_member(user_id) if guild is not None and hasattr(guild, "get_member") else None
    user = None
    if bot is not None and hasattr(bot, "get_user"):
        try:
            user = bot.get_user(user_id)
        except Exception:
            user = None
    return _names_of(username, member, user)


def _known_names(bot) -> list[str]:
    from core.episodic import _known_names as names

    return names(bot)


def _legacy_summary(content: str, fallback: str) -> str:
    from core.context_manager import parse_pin_sections

    if not content:
        return ""
    _label, summary, _recent = parse_pin_sections(content, fallback)
    text = drop_safety_preamble((summary or "").strip())
    if not text or text in ("(none yet)", "(summary unavailable)", "none") or _is_bad_llm_output(text):
        return ""
    return text


async def migrate_profile(side: str, legacy: str, own_names=(), known_names=()) -> dict | None:
    """A first JSON memory from old pin prose. None when the call failed."""
    from core import memory_store
    from core.summarizer_prompt import build_migration_prompt

    profile = memory_store.empty(side)
    if not legacy:
        return profile
    ops = await _ask_ops(build_migration_prompt(side, legacy))
    if ops is None:
        return None
    memory_store.apply_ops(
        profile, {"add": ops.get("add") or []}, side, own_names=own_names, known_names=known_names
    )
    profile["legacy"] = {"text": legacy, "at": time.time()}
    return profile


async def _pins(bot, guild, uid: int, username):
    """(thread, live content, fallback header) per side. Missing pins are None."""
    from core.discord_store import (
        BOT_CONTEXT_HEADER,
        OLD_BOT_CONTEXT_HEADER,
        USER_CONTEXT_HEADER,
        bot_memory_header,
        read_pinned_first,
    )

    out: dict[str, tuple] = {}
    label = " ".join(str(username or "").split()) or str(uid)
    for side in ("user", "bot"):
        thread = None
        try:
            if side == "user" and guild is not None and bot is not None:
                from core.context_manager import ensure_context_channel

                thread, _pin = await ensure_context_channel(bot, guild, uid, username)
            elif side == "bot" and bot is not None:
                from core.bot_memory import ensure_bot_memory_channel

                thread, _pin = await ensure_bot_memory_channel(bot, uid, username)
        except Exception as exc:
            print(f"[context_summarizer] {side} pin lookup failed: {type(exc).__name__}", flush=True)
            thread = None
        headers = (BOT_CONTEXT_HEADER, OLD_BOT_CONTEXT_HEADER) if side == "bot" else (USER_CONTEXT_HEADER,)
        fallback = bot_memory_header(label) if side == "bot" else f"{USER_CONTEXT_HEADER}{label}:\n"
        # No thread means the old pin cannot be checked: a person with no JSON
        # memory yet is not started over without it.
        content, readable = "", False
        if thread is not None:
            live, ok = await read_pinned_first(thread, headers)
            readable = bool(ok)
            content = getattr(live, "content", "") or "" if live is not None else ""
        out[side] = (thread, content, fallback, readable)
    return out


async def write_memory_pin(thread, side: str, content: str, fallback: str, profile: dict) -> bool:
    """The Discord copy of the JSON memory. The JSON stays the source."""
    from core import memory_store
    from core.context_manager import parse_pin_sections, summary_only, summary_room, title_from_label
    from core.discord_store import (
        BOT_CONTEXT_HEADER,
        OLD_BOT_CONTEXT_HEADER,
        USER_CONTEXT_HEADER,
        same_discord_text,
        write_pinned,
    )

    if thread is None:
        return False
    label, _summary, _recent = parse_pin_sections(content or fallback, fallback)
    title = title_from_label(label)
    room = min(memory_store.RENDER_CAP, summary_room(title))
    text = memory_store.render(profile, room, side) or "(none yet)"
    body = summary_only(title, text)
    if (content or "").startswith(OLD_BOT_CONTEXT_HEADER):
        header = OLD_BOT_CONTEXT_HEADER
    elif side == "bot":
        header = BOT_CONTEXT_HEADER
    else:
        header = USER_CONTEXT_HEADER
    if content and same_discord_text(body, content):
        return True
    written, note = await write_pinned(thread, header, body)
    if written is None or note == "pin-failed":
        print(f"[context_summarizer] {side} pin copy was not written ({note}).", flush=True)
        return False
    return True


def _audit(user_id, guild) -> None:
    try:
        from core.audit import safe_activity

        safe_activity(
            "summarizer",
            outcome="success",
            user_id=user_id,
            guild_id=getattr(guild, "id", None),
        )
    except Exception:
        pass


async def fold_person(bot, guild, user_id, username=None, *, reason: str = "count", force: bool = False) -> bool:
    """Update both memories for one person from the waiting conversation.

    guild is the home server (where the pins live). True when both memories
    were updated; the waiting messages are then cleared. Anything else keeps
    them for the next try. A side that already saved this batch is not asked
    again on that retry. Failures back off (personal_recent.BACKOFF_AFTER)
    and the batch is dropped after personal_recent.DROP_AFTER.
    """
    from core import memory_store
    from core import personal_recent as recent
    from core.summarizer_prompt import build_self_fold_prompt, build_user_fold_prompt

    _audit(user_id, guild)
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    async with _lock(uid):
        state = recent.load_fold_state(uid)
        now = time.time()
        if not force and state["retry_at"] > now:
            return False
        user_rows = recent.pending_rows(uid, "user")
        bot_rows = recent.pending_rows(uid, "bot")
        user_pending = recent.pending_count(uid, "user")
        bot_pending = recent.pending_count(uid, "bot")
        if not user_rows and not bot_rows and not force:
            return False
        batch = [user_pending, bot_pending]
        conversation = conversation_text(user_rows, bot_rows)
        own = _own_names(bot, guild, uid, username)
        known = _known_names(bot)
        pins = await _pins(bot, guild, uid, username)
        print(
            f"[context_summarizer] Using model={get_summarizer_model()} for {uid} "
            f"({reason}: {len(user_rows)} from them, {len(bot_rows)} from me)",
            flush=True,
        )
        done = True
        for side in ("user", "bot"):
            if conversation and state["done"].get(side) == batch:
                # This side already saved this exact batch on an earlier try.
                continue
            thread, content, fallback, readable = pins[side]
            profile = memory_store.load(uid, side)
            if profile is None:
                if not readable:
                    # The old pin could not be read: do not start over without it.
                    done = False
                    continue
                profile = await migrate_profile(side, _legacy_summary(content, fallback), own, known)
                if profile is None:
                    done = False
                    continue
                memory_store.save(uid, side, profile)
                print(f"[memory] {side} memory for {uid} moved to the structured format", flush=True)
            if conversation:
                builder = build_self_fold_prompt if side == "bot" else build_user_fold_prompt
                ops = await _ask_ops(builder(memory_store.listing(profile), conversation))
                if ops is None:
                    done = False
                    continue
                mention_ids = []
                if side == "bot":
                    try:
                        from core.standing import mentioned_user_ids, sync_messages
                        for _at, raw in list(user_rows or []) + list(bot_rows or []):
                            mention_ids.extend(mentioned_user_ids(str(raw or "")))
                    except Exception:
                        mention_ids = []
                stats = memory_store.apply_ops(
                    profile, ops, side, own_names=own, known_names=known, mention_ids=mention_ids,
                )
                if side == "bot" and conversation:
                    try:
                        from core.standing import sync_messages
                        messages = [text for _at, text in (user_rows or [])]
                        sync_messages(profile, messages, mention_ids)
                    except Exception:
                        pass
                print(
                    f"[memory] {side} memory for {uid}: +{stats['added']} ~{stats['updated']} "
                    f"-{stats['removed']} merged {stats['merged']} dropped {len(stats['dropped'])}",
                    flush=True,
                )
            else:
                memory_store.maintain(profile, side)
            memory_store.save(uid, side, profile)
            if conversation:
                state["done"][side] = list(batch)
            try:
                await write_memory_pin(thread, side, content, fallback, profile)
            except Exception as exc:
                print(f"[context_summarizer] {side} pin copy failed: {type(exc).__name__}", flush=True)
        if done:
            recent.clear_folded(uid, "user", user_pending)
            recent.clear_folded(uid, "bot", bot_pending)
            recent.reset_fold_state(uid)
            print(f"[memory] summary stored for {uid}", flush=True)
            return True
        state["fails"] = int(state["fails"]) + 1
        if state["fails"] >= recent.DROP_AFTER:
            recent.clear_folded(uid, "user", user_pending)
            recent.clear_folded(uid, "bot", bot_pending)
            recent.reset_fold_state(uid)
            print(
                f"[memory] dropped the waiting batch for {uid} after {state['fails']} failed "
                "updates; the memory that did not update stays as it was",
                flush=True,
            )
            return False
        if state["fails"] >= recent.BACKOFF_AFTER:
            state["retry_at"] = now + recent.BACKOFF_SECONDS
            print(
                f"[memory] summary did not land for {uid} ({state['fails']} in a row); "
                f"next try in {recent.BACKOFF_SECONDS // 60} minutes",
                flush=True,
            )
        else:
            print(f"[memory] summary did not land; keeping the buffer for {uid}", flush=True)
        recent.save_fold_state(uid, state)
        return False


def fold_reason(user_id: int, now: float | None = None) -> str:
    """"count", "idle", or "" (not yet)."""
    from core.personal_recent import fold_every, fold_idle_seconds, has_stash, last_at, pending_count

    from core.personal_recent import backing_off

    now = time.time() if now is None else float(now)
    uid = int(user_id)
    if backing_off(uid, now):
        return ""
    user_p = pending_count(uid, "user")
    bot_p = pending_count(uid, "bot")
    every = fold_every()
    if user_p >= every or bot_p >= every * 2:
        return "count"
    if user_p + bot_p == 0 and not (has_stash(uid, "user") or has_stash(uid, "bot")):
        return ""
    last = max(last_at(uid, "user"), last_at(uid, "bot"))
    if last and now - last >= fold_idle_seconds():
        return "idle"
    return ""


async def replace_memory(bot, guild, user_id, username, text: str, side: str = "user") -> bool:
    """Owner command: start this memory over from the given text (or empty)."""
    from core import memory_store

    uid = int(user_id)
    async with _lock(uid):
        own = _own_names(bot, guild, uid, username)
        known = _known_names(bot)
        legacy = (text or "").strip()
        profile = await migrate_profile(side, legacy, own, known) if legacy else memory_store.empty(side)
        if profile is None:
            return False
        memory_store.save(uid, side, profile)
        pins = await _pins(bot, guild, uid, username)
        thread, content, fallback, _readable = pins[side]
        await write_memory_pin(thread, side, content, fallback, profile)
        return True
