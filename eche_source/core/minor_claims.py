"""Self-declared minors on an 18+ bot: ban, no lecture.

Owner request 2026-11-18 21:42 ET: when 18+ content is enabled, a user who
says they are a minor is banned through the admin ban path (bot permission,
owner and role checks). No public lecture, only a short neutral line. Each
ban attempt writes a safety audit row first (user, server, reason category;
no message text); if it can't be written, nothing is done (fail closed).
The bot owner is never banned. When 18+ is off, nothing here runs.
"""
from __future__ import annotations

import re

CATEGORY = "self-declared-minor"
NEUTRAL_LINE = "A member was removed under this server's 18+ rules."

_I = r"(?<![\w'])(?:i\s*'?\s*m|i\s+am|im|ima|i'?m\s+only|i\s+am\s+only|im\s+only|i'?m\s+just|im\s+just|i\s+am\s+just)"
_NOT = r"(?!\s*(?:not|never|no\s+longer|nearly|almost)\b)"
_WORD_MINOR = re.compile(
    _I + _NOT + r"\s+(?:a\s+)?(?:minor|underage|under\s*-?\s*age|under\s*18|under\s+eighteen)\b",
    re.I,
)
_AGE_UNITS = re.compile(
    _I + _NOT + r"\s+(\d{1,2})\s*(?:years?\s*old|yrs?\s*old|yrs?|y\s*/?\s*o)\b",
    re.I,
)
# "I'm 15." / "im 16 lol" / "i'm 14!" — a bare 10-17 that ends the clause.
_AGE_BARE = re.compile(
    _I + _NOT + r"\s+(1[0-7])(?=\s*(?:$|[.!?,]|\s+(?:lol|lmao|haha|tho|btw|and|so|rn|now)\b))",
    re.I,
)
_MY_AGE = re.compile(r"\bmy\s+age\s+is\s+(\d{1,2})\b|\bi\s+(?:just\s+)?turned\s+(\d{1,2})\b", re.I)
_QUOTED = re.compile(r"[\"“”][^\"“”]*[\"“”]|`[^`]*`|^>.*$", re.M)


def says_minor(text: str) -> bool:
    """True when the author states in the first person that they are under 18."""
    body = _QUOTED.sub(" ", text or "")
    if not body.strip():
        return False
    if _WORD_MINOR.search(body):
        return True
    for rx in (_AGE_UNITS, _AGE_BARE):
        for m in rx.finditer(body):
            if 1 <= int(m.group(1)) < 18:
                return True
    for m in _MY_AGE.finditer(body):
        age = m.group(1) or m.group(2)
        if age and 1 <= int(age) < 18:
            return True
    return False


async def enforce(bot, message) -> bool:
    """Ban a self-declared minor when 18+ is on. True when the message was handled."""
    guild = getattr(message, "guild", None)
    author = getattr(message, "author", None)
    if guild is None or author is None or getattr(author, "bot", False):
        return False
    from core.safety import adult_content_enabled

    if not adult_content_enabled():
        return False
    if not says_minor(getattr(message, "content", "") or ""):
        return False
    from core.tools_admin import ban_declared_minor

    return await ban_declared_minor(bot, message)
