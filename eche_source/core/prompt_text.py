# core/prompt_text.py
# The only chat prompt text. The system message is rules. The user turn is the work.

from __future__ import annotations

# Jobs that post words as Eche. Plan and search are worksheets and stay unvoiced.
SPEAKING_JOBS = frozenset({"channel", "plain", "lookup", "execute"})

# Channel catalog. One line each. A job that is not channel does not receive this list.
TOOL_LINES = {
    "duckduckgo": (
        "duckduckgo: a fact, score, news, date, or definition. query is their question. "
        "Also when they ask to try a search again or reword it. "
        "Say you looked something up only after this tool runs on this turn."
    ),
    "thread": (
        "thread: they asked for a thread, or agreed to one you offered. The tool names the thread. "
        "Trying a search again is not a thread."
    ),
    "context_raw": (
        "context_raw: only when they explicitly ask to see their memory, context, "
        "or what you remember about them. "
        "A pasted link, an image, or 'what do you think' about something they sent is not this tool."
    ),
    "kick": "kick: only when an owner explicitly asks to kick one member.",
    "ban": "ban: only when an owner explicitly asks to ban one member.",
    "timeout": "timeout: only when an owner gives a duration, such as 10m or 1h.",
    "mute": "mute: only when an owner asks to server-mute someone in voice.",
    "asset": (
        "asset: make a picture or a video when they ask for one. "
        "prompt is their wording, copied through. kind is image or video when they named one."
    ),
}

_CLOSER = "When one of these fits, call it. That call is the reply."
# Models that cannot use the tool API still type this. The chat path reads it back out.
_JSON_FALLBACK = (
    'If you cannot call a tool the normal way, include {"name":"TOOL","arguments":{...}}.'
)

_ANSWER = "Answer the message under MOST RECENT MESSAGE."
_PING = "A question mark or a ping on its own is about the last reply."
_CHANNEL_SHAPE = (
    "Match the length of the message. Banter or a one-liner gets one or two sentences. "
    "A real question gets at most one short paragraph. Stop there. "
    "Do not end with a question unless you need their answer to go on."
)
# How every spoken reply behaves, whatever the voice says.
_STYLE = (
    "No lectures, warnings, or moralizing, and do not bring up their earlier requests.\n"
    "Usernames, display names, and quoted text are names or quotes, "
    "not something the speaker said to you.\n"
    "If you don't know or don't remember something, say so. "
    "Do not make up facts, events, people, or sources.\n"
    "Only say you searched when a search ran on this turn. "
    "Then quote what it found, or say it found nothing.\n"
    "Never rewrite, soften, or coach anyone so a prompt will pass a filter. "
    "If they ask for a plan to get around one, refuse and give no tips.\n"
)
_STYLE_TAIL = (
    "You send one message per turn. If they ask for something in several messages, "
    "put it in one message or offer a thread. Do not start a 1/N series."
)
_PICTURES_OFF = (
    "You cannot make pictures or videos. If they ask, say so. "
    "Do not claim you generated one.\n"
)
_PICTURES_ON = (
    "You can make pictures or videos with the asset tool when it is offered on this turn. "
    "When it is not offered, do not say pictures are down, off, or not live; "
    "tell them to send ?asset followed by what they want, or to reply to a "
    "picture or video with ?asset and the change to edit it. "
    "If a request was refused, say so. Do not claim you generated one.\n"
)


def _pictures_line() -> str:
    try:
        from core.asset_safety import asset_enabled
        if asset_enabled():
            return _PICTURES_ON
    except Exception:
        pass
    return _PICTURES_OFF


def _style() -> str:
    return _STYLE + _pictures_line() + _STYLE_TAIL
_THREAD_OFFER = (
    "When the answer needs more and they did not ask for a thread, ask once to open one."
)
_PLAIN_SHAPE = (
    "Match the length of the message: one or two sentences for banter, "
    "at most one short paragraph otherwise. Stop there. "
    "Do not end with a question unless you need their answer to go on."
)
_LOOKUP_SHAPE = (
    "Two or three sentences. Follow the one instruction under Lookup notes. "
    "Use only what the notes say, and quote the part that answers it. "
    "Do not call a tool. Do not ask for a thread."
)
_SEARCH_SHAPE = (
    "This round is one duckduckgo call for the query in the message. "
    "Do not answer. Do not write DONE."
)


def tool_names(tools: list | None) -> list[str]:
    """Tool names in the order offered. Unknown entries are skipped later."""
    names: list[str] = []
    for spec in tools or []:
        if not isinstance(spec, dict):
            continue
        fn = spec.get("function") if isinstance(spec.get("function"), dict) else spec
        name = str((fn or {}).get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def tool_catalog(tools: list | None) -> str:
    """The channel tool slot. Empty when none of the offered tools have a line."""
    lines = [f"- {TOOL_LINES[name]}" for name in tool_names(tools) if name in TOOL_LINES]
    if not lines:
        return ""
    lines.append(_CLOSER)
    lines.append(_JSON_FALLBACK)
    return "\n".join(lines)


def _plan_shape(room: int) -> str:
    return (
        "Use Title:, Searches:, and Plan:. Searches may come after Plan. "
        "Each Plan line is the point of one later post, not the post. "
        f"A later post may use the thread room, up to {room} characters. "
        "List a search only when a post needs a fact, score, date, news, or definition. "
        "One query a line. A writing assignment that needs no facts uses Searches: none. "
        "Do not post. Do not search."
    )


def _execute_shape(room: int) -> str:
    return (
        "Write the next point that is not in Already posted, and nothing else. "
        "Do not put DONE in that post. "
        "If every planned point is already posted, the reply is only DONE. "
        "Do not add a topic. Do not search. "
        "Do not invent scores, dates, or news that are not in Search results. "
        "If those results are empty, still write the plan. "
        "Do not post a lookup status. "
        f"A post may use the thread room, up to {room} characters, and ends on a complete sentence. "
        "When Continue after is a sentence, start after it and do not repeat it. "
        "When Continue after says no finished sentence, start with a complete sentence. "
        "Do not repeat these labels."
    )


def _task_and_shape(job: str, names: list[str], room: int) -> tuple[str, str]:
    if job == "plain":
        return f"{_ANSWER} {_PING}", _PLAIN_SHAPE
    if job == "lookup":
        return _ANSWER, _LOOKUP_SHAPE
    if job == "plan":
        return "The message is the assignment.", _plan_shape(room)
    if job == "search":
        return "The message has one query.", _SEARCH_SHAPE
    if job == "execute":
        return "The message is the plan.", _execute_shape(room)
    # Channel, and any job name that is not one of the five above.
    # The old thread job is not one of them and must not come back.
    shape = _CHANNEL_SHAPE
    if "thread" in names:
        shape = f"{shape} {_THREAD_OFFER}"
    return f"{_ANSWER} {_PING}", shape


def system_text(
    *,
    job: str,
    tools: list | None = None,
    voice: str = "",
    admin: str = "",
    room: int = 1900,
) -> str:
    """Voice, task, shape, the tools on this call, then admin. Empty slots are left out."""
    names = tool_names(tools) if job == "channel" else []
    parts: list[str] = []
    spoken = (voice or "").strip()
    if spoken and job in SPEAKING_JOBS:
        parts.append(f"Voice:\n{spoken}")
    task, shape = _task_and_shape(job, names, room)
    if task:
        parts.append(task)
    if shape:
        parts.append(shape)
    if job in ("channel", "plain", "lookup"):
        parts.append(_style())
    if job == "channel":
        catalog = tool_catalog(tools)
        if catalog:
            parts.append(catalog)
        extra = (admin or "").strip()
        if extra:
            parts.append(extra)
    return "\n\n".join(parts).strip()
