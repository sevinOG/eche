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
        "Pictures or videos they asked to put in that thread are posted there. "
        "Trying a search again is not a thread."
    ),
    "context_raw": "context_raw: only when they ask to see their own context.",
    "asset": (
        "asset: they asked for a picture, a video, or a change to one they attached. "
        "prompt is what to make. kind is image or video when they named one. "
        "A thread of those files is the thread tool."
    ),
    "kick": "kick: only when an owner explicitly asks to kick one member.",
    "ban": "ban: only when an owner explicitly asks to ban one member.",
    "timeout": "timeout: only when an owner gives a duration, such as 10m or 1h.",
    "mute": "mute: only when an owner asks to server-mute someone in voice.",
}

_CLOSER = "When one of these fits, call it. That call is the reply."
# Models that cannot use the tool API still type this. The chat path reads it back out.
_JSON_FALLBACK = (
    'If you cannot call a tool the normal way, include {"name":"TOOL","arguments":{...}}.'
)

_ANSWER = "Answer the message under MOST RECENT MESSAGE."
_PING = "A question mark or a ping on its own is about the last reply."
_CHANNEL_SHAPE = "The channel reply is two or three sentences, one paragraph. Stop there."
_THREAD_OFFER = (
    "When the answer needs more and they did not ask for a thread, ask once to open one."
)
_PLAIN_SHAPE = "The reply is two or three sentences, one paragraph. Stop there."
_LOOKUP_SHAPE = (
    "Two or three sentences. Follow the one instruction under Lookup notes. "
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
    if job == "channel":
        catalog = tool_catalog(tools)
        if catalog:
            parts.append(catalog)
        extra = (admin or "").strip()
        if extra:
            parts.append(extra)
    return "\n\n".join(parts).strip()
