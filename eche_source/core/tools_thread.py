# core/tools_thread.py
# Opens a public thread and writes the longer version there.
#
# The channel reply stays short. This runs when they ask for a thread, or
# agree to one that was offered. The thread is started on that message.
# Phase 1 names the thread and writes the plan. It does not search or post.
# Phase 2 looks facts up and writes the posts. It may search more than once.
# A line that is only DONE ends phase 2. That word is not posted.
# At the start, a direct ask uses that message only. A more-room follow-up
# ("about that", "yes", "I need more room to explain that") uses the previous message.

from __future__ import annotations

import re

import discord

from core.debuglog import dprint
from core.tools import (
    ACCESS_ANYONE,
    Tool,
    ToolContext,
    ToolResult,
    announcement,
    register,
)

# Phase 2 searches and posts. A bound so a missed DONE cannot run forever.
THREAD_STEPS = 8
_PLAN_TOKENS = 200
_NOTES_CAP = 2000
_FALLBACK_PLAN = "Cover the assignment in the subject. Stay on that breakdown."
_ARCHIVE_MINUTES = (10080, 4320, 1440, 60)


# Used only to skip a thread request when a vague title is filled from
# recent chat. It does not open a thread. The model has to call the tool.
_THREAD_ASK = re.compile(
    r"\b(?:make|open|start|create|need|want)\b(?:\s+\w+){0,6}\s+thread\b",
    re.IGNORECASE,
)
_THREAD_REFUSAL = re.compile(
    r"\b(?:don't|do not|stop)\b(?:\s+\w+){0,6}\s+thread\b",
    re.IGNORECASE,
)


def asks_for_thread(text: str) -> bool:
    """True when this message itself asks for a thread."""
    cleaned = " ".join((text or "").replace("\u2019", "'").split())
    if not cleaned or len(cleaned) > 400:
        return False
    if _THREAD_REFUSAL.search(cleaned):
        return False
    return _THREAD_ASK.search(cleaned) is not None


def thread_title(arguments: dict | None) -> str:
    raw = ""
    if isinstance(arguments, dict):
        raw = str(arguments.get("title") or "")
    title = " ".join(raw.split())
    if not title:
        title = "More"
    return title[:100]


# "make a thread for that" leaves the title as the word "that".
# These are not a subject. A real recent question replaces them.
_VAGUE_WORDS = {
    "that", "this", "it", "them", "those", "one", "more", "please",
    "yes", "yeah", "yep", "yah", "ok", "okay", "sure", "nah", "no",
    "true", "fair", "alright", "right", "go", "on", "k", "kk",
}
_TITLE_FILLER = _VAGUE_WORDS | {"a", "an", "the", "for", "about", "to", "me", "us", "of"}
_LEADING_AGREEMENT = re.compile(
    r"^(?:yeah|yes|yep|yah|ok|okay|sure|please|alright|right|true|fair|nah|no)\b[\s,!.]*",
    re.IGNORECASE,
)
_CONTINUATION = re.compile(
    r"^(?:"
    r"walk me through(?:\s+(?:it|that|this|the(?:\s+full)?(?:\s+process)?))?"
    r"|the full process"
    r"|full process"
    r"|explain (?:it|that|this)"
    r"|tell me more"
    r"|go on"
    r"|do that"
    r"|keep going"
    r")\s*$",
    re.IGNORECASE,
)


def _plain_line(text: str) -> str:
    raw = re.sub(r"<@!?\d+>|<@&\d+>", " ", text or "")
    return " ".join(raw.split())


def title_is_vague(title: str) -> bool:
    """True when the title is a pronoun, an agreement, or empty."""
    words = [word.strip(" .?!\"'`") for word in (title or "").split()]
    words = [word for word in words if word]
    if not words:
        return True
    return all(word.casefold() in _TITLE_FILLER for word in words)


_TOOL_ANNOUNCEMENT = re.compile(r"\beche used\b", re.IGNORECASE)
_SAFETY_LINE = re.compile(
    r"^(?:(?:user|response)\s+safety|safety\s+categories)\s*:",
    re.IGNORECASE,
)


def title_is_unusable(title: str) -> bool:
    """A pasted reply, a tool announcement, or a safety label is not a title."""
    text = " ".join((title or "").split())
    if title_is_vague(text):
        return True
    if asks_for_thread(text):
        return True
    if _TOOL_ANNOUNCEMENT.search(text) or _SAFETY_LINE.match(text):
        return True
    return len(text.split()) > 12


_THREAD_LEAD = re.compile(
    r"^(?:(?:yeah|yes|yep|yah|ok|okay|sure|please|alright|and)\b[\s,]*)?"
    r"(?:make|open|start|create|need|want)\b"
    r"(?:\s+\w+){0,6}\s+thread\b"
    r"(?:\s+(?:about|on|for|and)\b)?",
    re.IGNORECASE,
)
_VAGUE_TASK = re.compile(
    r"^(?:(?:do|give|write|make)\s+(?:us\s+|me\s+)?)?(?:a\s+|the\s+)?"
    r"(?:deep dive|breakdown|full version|long version|more|it|that|this)"
    r"\s*$",
    re.IGNORECASE,
)
_PRONOUN = re.compile(r"\b(?:it|its|it's|that|this|they|them|those)\b", re.IGNORECASE)
_ANNOUNCE_LINE = re.compile(r"^eche used \*?thread\*?[.!]?\s*$", re.IGNORECASE)
_ANNOUNCE_PREFIX = re.compile(
    r"^(?:eche used \*?[a-z0-9_]+\*?[.!]?\s*)+",
    re.IGNORECASE,
)
_MORE_ROOM = re.compile(
    r"\b(?:more room|longer version|full version|explain that|explain it|explain this)\b",
    re.IGNORECASE,
)
# A tool status is not a subject. "Try rewording" must not title itself with this.
_STATUS_LINE = re.compile(
    r"^(?:"
    r"duckduckgo blocked that lookup\.?"
    r"|i couldn't reach duckduckgo\.?"
    r"|i found the notes, but i couldn't answer from them\.?"
    r"|i couldn't turn that into an answer\.?"
    r"|sorry, i(?:'m| am) being rate limited\b.*"
    r"|sorry, i hit a backend error\b.*"
    r"|the provider returned an error\.?"
    r"|i opened the thread, but i had nothing more to add\.?"
    r"|i had nothing more to add\.?"
    r"|i couldn't open a thread\b.*"
    r")$",
    re.IGNORECASE,
)
_GENERIC_TOPIC = {
    "game", "games", "thing", "things", "stuff", "topic", "review", "reviews",
    "breakdown", "summary", "overview", "dive", "version", "detail", "details",
    "story", "people", "user", "users", "thread", "room", "explain",
    "explanation", "full", "long", "info", "information", "give", "write",
    "make", "want", "need", "please",
}
_TITLE_LINE = re.compile(r"^title\s*:\s*(.*)$", re.IGNORECASE)
_PLAN_MARK = re.compile(r"^plan\s*:\s*(.*)$", re.IGNORECASE)


def ask_remainder(text: str) -> str:
    """The subject after a thread request. Other lines stay as written."""
    raw = _plain_line(text)
    if not asks_for_thread(raw):
        return raw
    rest = _THREAD_LEAD.sub("", raw, count=1).strip(" .,!?:;")
    return _without_agreement(rest)


def names_subject(text: str) -> bool:
    """True when the line names a topic, not just 'deep dive' or 'that'."""
    rest = " ".join((text or "").split())
    if not rest or title_is_vague(rest) or _CONTINUATION.match(rest) or _VAGUE_TASK.match(rest):
        return False
    words = [word for word in rest.split() if word.casefold() not in _TITLE_FILLER]
    return len(words) >= 3


def _without_tool_announce(text: str) -> str:
    """Drop a leading 'Eche used *tool*!' so it cannot become the thread name."""
    return _ANNOUNCE_PREFIX.sub("", _plain_line(text)).strip()


def _concrete_words(text: str) -> list[str]:
    """Naming words. Pronouns and words like 'review' or 'breakdown' do not count."""
    found: list[str] = []
    for word in _plain_line(text).split():
        token = word.strip(".,!?;:\"'`()[]").casefold()
        if len(token) <= 2 or token in _TITLE_FILLER or token in _GENERIC_TOPIC:
            continue
        if _PRONOUN.fullmatch(token):
            continue
        found.append(token)
    return found


def direct_assignment(text: str) -> str:
    """Path 2. The subject in a direct thread ask. Empty means use the previous message.

    'its reviews' does not send a message that already names Squadron 42 back
    to the line above it.
    """
    raw = _plain_line(text)
    if not asks_for_thread(raw):
        return ""
    rest = ask_remainder(raw)
    if not rest or title_is_vague(rest) or _CONTINUATION.match(rest) or _VAGUE_TASK.match(rest):
        return ""
    if not _concrete_words(rest):
        return ""
    return rest


def usable_previous(text: str) -> str:
    """Path 1 source. A tool announcement or a more-room line is not the subject."""
    raw = _without_tool_announce(text)
    if not raw or title_is_vague(raw) or _CONTINUATION.match(raw):
        return ""
    if _STATUS_LINE.match(raw):
        return ""
    if _TOOL_ANNOUNCEMENT.search(raw):
        return ""
    if _MORE_ROOM.search(raw) and not _concrete_words(raw):
        return ""
    if asks_for_thread(raw) and not direct_assignment(raw):
        return ""
    return raw


def query_stays_on(query: str, source: str) -> bool:
    """A lookup has to name this assignment. A different subject is skipped."""
    words = _concrete_words(source)
    if not words:
        return True
    folded = (query or "").casefold()
    if not folded:
        return False
    for word in words:
        if word in folded or (len(word) >= 5 and word[:5] in folded):
            return True
    return False


def ask_names_its_subject(asking: str) -> bool:
    """The message itself names the thread. The previous message stays out."""
    return bool(direct_assignment(asking))


def topic_text(asking: str, earlier: list[str]) -> str:
    """Path 2 is this message's subject only. Path 1 is the nearest previous subject."""
    named = direct_assignment(asking)
    if named:
        return named
    for text in reversed(earlier):
        picked = usable_previous(text)
        if picked:
            return picked
    return _plain_line(asking)


def title_is_grounded(title: str, source: str) -> bool:
    """Keep a model title only when its words are in the assignment."""
    if title_is_unusable(title):
        return False
    words = []
    for word in (title or "").split():
        token = word.strip(" .?!\"'`").casefold()
        if token and token not in _TITLE_FILLER and len(token) > 2:
            words.append(token)
    if not words:
        return False
    body = (source or "").casefold()
    hits = sum(1 for word in words if word in body)
    return hits >= 1 and hits * 2 >= len(words)


def _without_agreement(text: str) -> str:
    rest = (text or "").strip()
    for _ in range(3):
        nxt = _LEADING_AGREEMENT.sub("", rest).strip()
        if nxt == rest:
            break
        rest = nxt
    return rest


def title_from_line(text: str) -> str:
    title = _plain_line(text).strip(" .?!\"'`")
    if asks_for_thread(title):
        title = ask_remainder(title).strip(" .?!\"'`") or title
    title = re.sub(r"^(?:the|a|an)\s+", "", title, count=1, flags=re.IGNORECASE).strip()
    if len(title) > 80:
        title = title[:80].rsplit(" ", 1)[0].strip() or title[:80].strip()
    return title or "More"


def reply_body(text: str) -> str:
    """Keep a stick-figure's line breaks. Mentions are dropped."""
    raw = re.sub(r"<@!?\d+>|<@&\d+>", " ", text or "")
    lines = [" ".join(line.split()) for line in raw.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]
    body = "\n".join(lines)
    if len(body) > 800:
        body = body[:799].rstrip() + "…"
    return body


def reply_subject(
    title: str,
    author: str,
    body: str,
    asking_name: str,
    asking: str,
) -> str:
    """The message they replied to is the whole subject."""
    from core.today import today_stamp

    label = " ".join((title or "").split()) or "More"
    quote = (body or "").strip() or "(no text)"
    ask = " ".join((asking or "").split()) or "(no text)"
    who = " ".join((author or "someone").split()) or "someone"
    asker = " ".join((asking_name or "user").split()) or "user"
    return (
        f"Today: {today_stamp()}\n"
        f"Thread: {label}\n"
        "They replied to this message. Write about this message only. "
        "Do not use older talk or memory.\n"
        f"{who}:\n{quote}\n"
        f"{asker}: {ask}"
    )


async def replied_target(message) -> tuple[str, str] | None:
    """Name and text of the message this one replies to, including a bot post."""
    from core.message_media import _referenced

    replied = await _referenced(message)
    if replied is None or getattr(replied, "id", None) == getattr(message, "id", None):
        return None
    text = reply_body(getattr(replied, "content", None) or "")
    if not text:
        return None
    author = getattr(replied, "author", None)
    name = getattr(author, "name", None) or "someone"
    return name, text


_DONE_LINE = re.compile(r"^done[.!]?\s*$", re.IGNORECASE)
_DONE_MARK = re.compile(r"(?:^|\s)DONE[.!]?(?:\s|$)")


def split_done(text: str) -> tuple[str, bool]:
    """Stop at DONE. That word and anything after it are not posted."""
    kept: list[str] = []
    for line in (text or "").strip().splitlines():
        if _DONE_LINE.match(line.strip()):
            return "\n".join(kept).strip(), True
        mark = _DONE_MARK.search(line)
        if mark:
            head = line[:mark.start()].rstrip()
            if head:
                kept.append(head)
            return "\n".join(kept).strip(), True
        kept.append(line)
    return "\n".join(kept).strip(), False


def usable_plan(text: str) -> str:
    """Drop a safety label. A label alone is not a plan."""
    kept = [
        line for line in (text or "").splitlines()
        if line.strip() and not _SAFETY_LINE.match(line.strip())
    ]
    cleaned = "\n".join(kept).strip()
    if len(" ".join(cleaned.split())) < 24:
        return ""
    return cleaned


def backend_error_text(text: str) -> bool:
    """A provider failure. It is not a thread post."""
    return (text or "").strip().lower().startswith("sorry, i hit a backend error")


def finish_thread_part(reply: str, cut: bool) -> tuple[str, bool]:
    """Text to post, and whether this part ends the thread.

    A token-cap cut is not an ending. A cut with no finished sentence
    posts nothing, so the next step can start that sentence.
    """
    from core.client import complete_sentences

    lines = (reply or "").splitlines()
    while lines and _ANNOUNCE_LINE.match(lines[0].strip()):
        lines.pop(0)
    body, done = split_done("\n".join(lines))
    if cut:
        done = False
        body = complete_sentences(body)
    if body in ("", "..."):
        return "", done
    return body, done


async def _create_thread(message, title: str):
    """Start the thread on the message that requested it."""
    try:
        existing = message.thread
    except Exception:
        existing = None
    if existing is not None:
        return existing
    last_error = None
    for minutes in _ARCHIVE_MINUTES:
        try:
            return await message.create_thread(
                name=title,
                auto_archive_duration=minutes,
                reason="Eche thread",
            )
        except Exception as exc:
            last_error = exc
    dprint(
        f"[tools] thread create failed on message {getattr(message, 'id', '?')}: {last_error}"
    )
    return None


def _duckduckgo_tools() -> list[dict]:
    from core.tools import specs_for

    return [
        spec
        for spec in specs_for(speaker_is_owner=False, admin_enabled=False)
        if spec.get("function", {}).get("name") == "duckduckgo"
    ]


def thread_subject(title: str, talk: list[tuple[str, str]]) -> str:
    """Recent lines only. Personal memory is not part of this subject."""
    from core.today import today_stamp

    label = " ".join((title or "").split()) or "More"
    rows: list[str] = []
    for name, text in talk:
        words = " ".join((text or "").split())
        if not words:
            continue
        if len(words) > 400:
            words = words[:399].rstrip() + "…"
        who = " ".join((name or "").split())
        rows.append(f"{who}: {words}" if who else words)
    body = "\n".join(rows) if rows else "(no text)"
    return (
        f"Today: {today_stamp()}\n"
        f"Thread: {label}\n"
        "Talk to write about:\n"
        f"{body}"
    )


def thread_log_detail(title: str, plan: str, body: str) -> str:
    """Log-pane text: the title, the plan, and what was posted."""
    label = " ".join((title or "").split()) or "More"
    planned = (plan or "").strip() or "(no plan)"
    posted = (body or "").strip() or "(nothing posted)"
    return f"title: {label}\n\nplan:\n{planned}\n\nposted:\n{posted}"


async def recent_user_lines(message, limit: int = 12, author_id: int | None = None) -> list[tuple[str, str]]:
    """User lines before this message, oldest first. Bot posts are skipped.

    `author_id` keeps another person's lines out of this thread's subject.
    """
    channel = getattr(message, "channel", None)
    if channel is None or not hasattr(channel, "history"):
        return []
    me_id = None
    try:
        me_id = getattr(getattr(getattr(message, "guild", None), "me", None), "id", None)
    except Exception:
        me_id = None
    found: list[tuple[str, str]] = []
    try:
        async for item in channel.history(limit=limit, before=message):
            author = getattr(item, "author", None)
            if getattr(author, "bot", False):
                continue
            if me_id is not None and getattr(author, "id", None) == me_id:
                continue
            if author_id is not None and getattr(author, "id", None) != author_id:
                continue
            text = _plain_line(getattr(item, "content", None) or "")
            if not text:
                continue
            name = getattr(author, "name", None) or "user"
            found.append((name, text))
    except Exception as exc:
        dprint(f"[tools] thread history failed: {exc}")
        return []
    found.reverse()
    return found


async def previous_topic(message) -> str:
    """Path 1. The previous message that actually names a subject."""
    reply = await replied_target(message)
    if reply is not None:
        text = usable_previous(reply[1])
        if text:
            return text
    channel = getattr(message, "channel", None)
    if channel is None or not hasattr(channel, "history"):
        return ""
    try:
        async for item in channel.history(limit=8, before=message):
            text = usable_previous(getattr(item, "content", None) or "")
            if text:
                return text
    except Exception as exc:
        dprint(f"[tools] thread previous failed: {exc}")
        return ""
    return ""


def resolve_title(parsed: str, source: str) -> str:
    """The thread name. A name that is not in the assignment falls back to it."""
    cleaned = _without_tool_announce(source)
    if title_is_grounded(parsed, cleaned or source):
        return thread_title({"title": parsed})
    label = title_from_line(cleaned or source)
    if (
        title_is_vague(label)
        or _TOOL_ANNOUNCEMENT.search(label)
        or _SAFETY_LINE.match(label)
    ):
        return "More"
    return thread_title({"title": label})


def parse_name_plan(text: str) -> tuple[str, str]:
    """Title and plan from a Title: / Plan: reply. Missing pieces stay empty."""
    title = ""
    plan_lines: list[str] = []
    seen_title = False
    seen_plan = False
    for line in (text or "").splitlines():
        if not seen_plan:
            cleaned = re.sub(r"[*`_]", "", line).strip()
            plan_mark = _PLAN_MARK.match(cleaned)
            if plan_mark:
                seen_plan = True
                extra = plan_mark.group(1).strip()
                if extra:
                    plan_lines.append(extra)
                continue
            title_mark = _TITLE_LINE.match(cleaned)
            if title_mark and not seen_title:
                seen_title = True
                title = title_mark.group(1).strip()
                continue
            if seen_title and line.strip():
                plan_lines.append(line.strip())
            continue
        plan_lines.append(line)
    return " ".join(title.split()), "\n".join(plan_lines).strip()


def name_plan_prompt(brief: str) -> str:
    """Phase 1. Name the thread and write the plan. No search and no posts."""
    return (
        f"{brief}\n\n"
        "Name this thread and write the plan. Reply in this shape only:\n"
        "Title: a few words from the assignment\n"
        "Plan:\n"
        "the points the posts will cover\n\n"
        "The assignment above is the only message. Name and plan that.\n"
        "Do not use an older message.\n"
        "This phase does not post and does not search.\n"
        "Do not announce a thread. Do not name anyone who is not in the assignment."
    )


def append_notes(notes: str, found: str) -> str:
    """Keep lookup text for later posts. The newest text stays when it is long."""
    piece = (found or "").strip()
    if not piece:
        return notes
    combined = f"{notes}\n\n{piece}".strip() if (notes or "").strip() else piece
    if len(combined) <= _NOTES_CAP:
        return combined
    return combined[-_NOTES_CAP:].lstrip()


def fill_prompt(subject: str, plan: str, already: str, notes: str, cut_note: str = "") -> str:
    """Phase 2. One writing step. Another search is still allowed. DONE ends it."""
    lookup = (notes or "").strip()
    return (
        f"Subject:\n{subject}\n\n"
        f"Plan. Stay on it:\n{plan}\n\n"
        f"{cut_note}"
        f"Already posted:\n{already}\n\n"
        f"Notes:\n{lookup or '(none)'}\n\n"
        "The reply is only the next post. Do not describe your steps or repeat these labels.\n"
        "Do not announce a thread. Do not name anyone who is not in the assignment.\n"
        "Call duckduckgo when the assignment needs a fact. You can call it more than once.\n"
        "Every search has to name the assignment. Ignore notes about a different subject.\n"
        "Do not invent reviews or public opinion that are not in the notes.\n"
        "A writing assignment is written. Do not replace it with a search or a list of links.\n"
        "If the notes are empty, still write the assignment.\n"
        "If the notes say the lookup was blocked or could not be reached, "
        "still write the assignment. Do not post that status as the reply. "
        "Do not invent the missing facts.\n"
        "If you cannot call the tool, reply with only "
        '{"name":"duckduckgo","arguments":{"query":"..."}} and stop.\n'
        "End on a complete sentence.\n"
        "When the plan is finished, add a last line that is only DONE. That line is not posted.\n"
    )


async def _name_and_plan(brief: str, source: str) -> tuple[str, str]:
    """Phase 1. One completion, no tools. Returns the thread name and the plan."""
    from core.client import call_groq_turn

    title = resolve_title("", source)
    plan = _FALLBACK_PLAN
    try:
        turn = await call_groq_turn(
            name_plan_prompt(brief),
            user_id=None,
            max_completion_tokens=_PLAN_TOKENS,
            tools=None,
            include_tool_note=False,
            job="plan",
        )
    except Exception as exc:
        dprint(f"[tools] thread name failed: {exc}")
        return title, plan
    if turn.quota or backend_error_text(turn.reply):
        return title, plan
    raw, _done = finish_thread_part(turn.reply or "", turn.cut)
    parsed_title, parsed_plan = parse_name_plan(raw)
    title = resolve_title(parsed_title, source)
    planned = usable_plan(parsed_plan)
    if planned:
        plan = planned
    return title, plan


async def _execute(ctx: ToolContext, thread, subject: str, plan: str, focus: str) -> str:
    """Phase 2. Search as needed, post the parts, and stop when the model emits DONE."""
    from core.client import (
        THREAD_COMPLETION_TOKENS,
        call_groq_turn,
        discord_chunks,
        last_sentence,
    )
    from core.tools import execute

    # No user id: the person's pin is not injected into the posts.
    posted: list[str] = []
    notes = ""
    resume_after = ""
    need_sentence = False
    search_tools = _duckduckgo_tools()

    for _ in range(THREAD_STEPS):
        already = "\n\n".join(posted) if posted else "(nothing yet)"
        cut_note = ""
        if resume_after:
            cut_note += (
                "The previous post was cut off. Continue after this sentence. "
                "Do not repeat it:\n"
                f"{resume_after}\n\n"
            )
        if need_sentence:
            cut_note += (
                "The previous attempt was cut off before a sentence finished. "
                "Start this part with a complete sentence.\n\n"
            )
        prompt = fill_prompt(subject, plan, already, notes, cut_note)
        try:
            turn = await call_groq_turn(
                prompt,
                user_id=None,
                max_completion_tokens=THREAD_COMPLETION_TOKENS,
                tools=search_tools,
                include_tool_note=False,
                job="thread",
            )
        except Exception as exc:
            dprint(f"[tools] thread fill failed: {exc}")
            break
        if turn.quota or backend_error_text(turn.reply):
            dprint(f"[tools] thread fill stopped: {(turn.reply or '')[:160]!r}")
            break
        if turn.tool_calls:
            for call in turn.tool_calls:
                if (call or {}).get("name") != "duckduckgo":
                    continue
                search_args = (call or {}).get("arguments") or {}
                query = " ".join(str(search_args.get("query") or "").split())
                if not query_stays_on(query, focus):
                    dprint(f"[tools] thread lookup skipped: {query!r}")
                    continue
                try:
                    result = await execute("duckduckgo", search_args, ctx)
                except Exception as exc:
                    dprint(f"[tools] thread lookup failed: {exc}")
                    continue
                notes = append_notes(notes, result.text or "")
            continue
        body, done = finish_thread_part(turn.reply or "", turn.cut)
        if not body or _STATUS_LINE.match(body.strip()):
            need_sentence = bool(turn.cut) and not body
            if done:
                break
            continue
        need_sentence = False
        stopped = False
        for chunk in discord_chunks(body):
            try:
                await thread.send(chunk)
            except Exception as exc:
                dprint(f"[tools] thread send failed: {exc}")
                stopped = True
                break
            posted.append(chunk)
        resume_after = last_sentence(body) if turn.cut else ""
        if done or stopped:
            break
    return "\n\n".join(posted)


async def open_thread(ctx: ToolContext, arguments: dict) -> ToolResult:
    message = ctx.message
    channel = getattr(message, "channel", None)
    arguments = arguments if isinstance(arguments, dict) else {}
    asking = _plain_line(getattr(message, "content", None) or "") or "(no text)"
    # Two-way check before any naming or research. A direct ask never sees
    # the previous message. A more-room follow-up uses that message only.
    from core.today import today_stamp

    named = direct_assignment(asking)
    if named:
        source = named
    else:
        source = await previous_topic(message) or asking
    brief = (
        f"Today: {today_stamp()}\n"
        "Assignment to name and plan. Use only this.\n"
        f"{source}"
    )
    title, plan = await _name_and_plan(brief, source)
    arguments["title"] = title
    subject = thread_subject(title, [("", source)])

    if isinstance(channel, discord.Thread):
        thread = channel
        opened = False
    else:
        thread = await _create_thread(message, title)
        opened = thread is not None
        if thread is None:
            return ToolResult(
                text="I couldn't open a thread in this channel.",
                fence=False,
            )

    try:
        await thread.send(announcement("thread"))
    except Exception as exc:
        dprint(f"[tools] thread announcement failed: {exc}")
        return ToolResult(
            text="I opened the thread, but I couldn't post in it.",
            fence=False,
        )

    body = await _execute(ctx, thread, subject, plan, source)
    detail = thread_log_detail(title, plan, body)
    if not body:
        note = "I opened the thread, but I had nothing more to add." if opened else "I had nothing more to add."
        try:
            await thread.send(note)
        except Exception as exc:
            dprint(f"[tools] thread empty note failed: {exc}")
        return ToolResult(text=note, fence=False, quiet=True, detail=detail)
    return ToolResult(text=body, fence=False, quiet=True, detail=detail)


def register_thread_tool() -> None:
    register(
        Tool(
            name="thread",
            description=(
                "Open a thread when they ask for one, or agree to one you offered. "
                "The tool names the thread from the assignment and writes it."
            ),
            handler=open_thread,
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Optional. Leave it out. The tool names the thread.",
                    },
                },
                "required": [],
            },
            access=ACCESS_ANYONE,
        )
    )


register_thread_tool()
