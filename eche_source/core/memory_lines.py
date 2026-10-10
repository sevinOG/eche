# memory_lines.py
# One short line per message for the New: section.
# The long-term block is a cloud of these lines, not a copy of them.

from __future__ import annotations


def _question(text: str, limit: int = 160) -> str:
    """The message as one line, without a trailing question mark."""
    line = " ".join((text or "").split()).strip().strip("\"'`")
    if line.endswith("?"):
        line = line[:-1].rstrip()
    if not line or line in ("(no text)", "(no content)"):
        return ""
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def stored_line(text: str, limit: int = 240) -> str:
    """One New: line, short enough that the pin edit can still succeed."""
    line = " ".join((text or "").split())
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def user_memory_line(message_text: str, *, picture: bool = False) -> str:
    """What the user asked, in their words. This is the user pin's New: line."""
    question = _question(message_text)
    if not question:
        if picture:
            return "user asked about a picture"
        return "user sent a message with no text"
    return f"user asked {question}"


def bot_memory_line(message_text: str, tools: list[str] | None = None) -> str:
    """What eche did with that message. This is the bot pin's New: line."""
    topic = _question(message_text, 120) or "that"
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
    if used:
        return f"eche used {used} to answer a question about {topic}"
    return f"eche answered a user question about {topic}"
