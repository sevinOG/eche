# core/command_lines.py
# Where a registered ?command starts in a message, if anywhere.
#
# A message is a command message when any line starts (after spaces) with
# "?" and a registered command name, e.g. a first line "how" and then
# "?somecommand ...". Such a message is handled by that command only: the
# bot's prefix points discord.py at that line, and the chat path and the
# chat model's tools never act on it.

from __future__ import annotations

COMMAND_PREFIX = "?"


def command_start(content: str | None, has_command) -> int | None:
    """Index of the "?" that starts the first registered command line, or None.

    has_command(name) says whether name is a registered command or alias.
    Unknown words after "?" ("?? what", "?lol") are not commands.
    """
    text = content or ""
    pos = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(COMMAND_PREFIX):
            rest = stripped[len(COMMAND_PREFIX):]
            if rest and not rest[0].isspace():
                name = rest.split(None, 1)[0]
                try:
                    known = bool(has_command(name))
                except Exception:
                    known = False
                if known:
                    return pos + (len(line) - len(stripped))
        pos += len(line)
    return None


def bot_has_command(bot):
    """has_command for a discord.py bot (names and aliases, case rules included)."""

    def _has(name: str) -> bool:
        getter = getattr(bot, "get_command", None)
        return bool(getter and getter(name) is not None)

    return _has


def is_command_message(bot, message) -> bool:
    content = getattr(message, "content", None) or ""
    return command_start(content, bot_has_command(bot)) is not None


def command_prefix_for(bot, message) -> list[str]:
    """The prefix discord.py should strip: everything up to and including the "?"."""
    content = getattr(message, "content", None) or ""
    idx = command_start(content, bot_has_command(bot))
    if idx is None:
        return []
    return [content[: idx + len(COMMAND_PREFIX)]]
