# memory_lines.py
# One short line per message for the New: section.
# The long-term block is a cloud of these lines, not a copy of them.

from __future__ import annotations


def stored_line(text: str, limit: int = 240) -> str:
    """One New: line, short enough that the pin edit can still succeed."""
    line = " ".join((text or "").split())
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def _said(text: str, limit: int = 120) -> str:
    """The reply as one line. A trailing question mark stays."""
    line = " ".join((text or "").split()).strip().strip("\"'`")
    if not line or line in ("(no text)", "(no content)"):
        return ""
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def user_memory_line(message_text: str, *, picture: bool = False) -> str:
    """What the user said. This is the user pin's New: line."""
    said = _said(message_text, 160)
    if not said:
        if picture:
            return "user sent a picture"
        return "user sent a message with no text"
    return f"user said {said}"


def bot_memory_line(reply_text: str, tools: list[str] | None = None) -> str:
    """What eche said, in eche's own voice. The bot pin's New: line.

    The bot pin is eche's record of its own outputs, so the line is first
    person and never starts with "user".
    """
    said = _said(reply_text, 240)
    names: list[str] = []
    for tool in tools or []:
        name = " ".join(str(tool or "").split()).lower()
        if name and name not in names:
            names.append(name)
    if len(names) == 1:
        used = names[0]
    elif len(names) == 2:
        used = f"{names[0]} and {names[1]}"
    elif names:
        used = ", ".join(names[:-1]) + f", and {names[-1]}"
    else:
        used = ""
    if used and said:
        return f"I used {used} and said {said}"
    if used:
        return f"I used {used}"
    if said:
        return f"I said {said}"
    return "I answered"


# --- The bot's own record ---------------------------------------------------
# The bot buffer and pin are eche's memory of its own outputs. A turn that ran
# a tool keeps what the tool did and what eche posted, not only one of them.

_ACTION_ARG_LIMIT = 160


def _arg(arguments, key: str, limit: int = _ACTION_ARG_LIMIT) -> str:
    if not isinstance(arguments, dict):
        return ""
    value = " ".join(str(arguments.get(key) or "").split()).strip().strip("\"'`")
    if len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value


def tool_action(name: str, arguments: dict | None = None, posted: str = "") -> str:
    """One bracketed note of what eche did with a tool, in eche's own voice.

    posted is text a quiet tool already put on Discord itself (an asset
    caption). It is kept so the record says what came out, not only the call.
    """
    tool = " ".join(str(name or "").split()).lower() or "a tool"
    detail = ""
    if tool == "asset":
        kind = _arg(arguments, "kind", 10).lower()
        prompt = _arg(arguments, "prompt")
        what = f"a {kind}" if kind in ("image", "video") else "an asset"
        detail = f"made {what}" + (f' of "{prompt}"' if prompt else "")
    elif tool == "duckduckgo":
        query = _arg(arguments, "query")
        detail = f'searched "{query}"' if query else "searched the web"
    elif tool == "thread":
        title = _arg(arguments, "title", 80)
        detail = f'opened a thread "{title}"' if title else "opened a thread"
    head = f"I used {tool}"
    if detail:
        head = f"{head}: {detail}"
    shown = " ".join(str(posted or "").split()).strip()
    if shown:
        if len(shown) > _ACTION_ARG_LIMIT:
            shown = shown[: _ACTION_ARG_LIMIT - 1].rstrip() + "…"
        head = f'{head}; posted "{shown}"'
    return f"[{head}]"


def bot_output_record(
    reply_text: str = "",
    said: list[str] | None = None,
    actions: list[str] | None = None,
) -> str:
    """What eche actually output this turn, as one stored message.

    A plain reply is stored as written. When tools ran, the bracketed action
    notes come first, then every message eche posted (tool replies). The
    model's draft reply is only used when nothing was posted, because the
    draft is not sent on a tool turn.
    """
    notes = [str(a).strip() for a in (actions or []) if str(a or "").strip()]
    spoken = [" ".join(str(s).split()) for s in (said or []) if str(s or "").strip()]
    if not notes and not spoken:
        return str(reply_text or "").strip()
    parts = notes + spoken
    return " ".join(parts).strip()
