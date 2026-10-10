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
    """What eche said. This is the bot pin's New: line."""
    said = _said(reply_text)
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
        return f"eche used {used} and said {said}"
    if used:
        return f"eche used {used}"
    if said:
        return f"eche said {said}"
    return "eche answered"
