# core/content_filter.py
# One place for what must never reach a memory pin, a memory prompt, or an
# Imagine request: Discord mentions, raw Discord ids, and slurs.
#
# The slur list is small on purpose and maintained here. Add a word to
# _ANYWHERE when it never shows up inside an innocent word, or to _WORDS when
# it needs word boundaries ("spic" inside "spice" is fine). Each letter may
# repeat ("niiigger") and common digit/symbol swaps are read as letters.

from __future__ import annotations

import re

# Matched inside longer words too ("sandn...", plurals, compounds).
_ANYWHERE = (
    "nigger",
    "nigga",
    "faggot",
    "kike",
    "tranny",
    "trannie",
    "wetback",
    "raghead",
    "towelhead",
    "beaner",
    "chinaman",
)

# Matched as whole words, with an optional plural or -ed/-ing ending.
_WORDS = (
    "fag",
    "fags",
    "faggy",
    "retard",
    "retarded",
    "spic",
    "spick",
    "chink",
    "gook",
    "coon",
    "jigaboo",
    "porchmonkey",
    "sambo",
    "dago",
    "wop",
    "gyppo",
    "paki",
    "shemale",
)

_ENDINGS = r"(?:s|es|z|ed|ing|y)?"

# Same length in and out, so a match position is also a position in the original.
_LEET = str.maketrans({
    "0": "o",
    "1": "i",
    "!": "i",
    "|": "i",
    "3": "e",
    "4": "a",
    "@": "a",
    "5": "s",
    "$": "s",
    "7": "t",
    "9": "g",
    "6": "g",
})


def _letters(word: str) -> str:
    """Each letter one or more times: n+i+g+g+e+r+. "Niger" does not match."""
    return "".join(f"{re.escape(ch)}+" for ch in word)


_SLUR_RE = re.compile(
    "|".join(
        [f"(?:{_letters(word)})" for word in _ANYWHERE]
        + [rf"(?<![a-z])(?:{_letters(word)}){_ENDINGS}(?![a-z])" for word in _WORDS]
    )
)

# <@123>, <@!123>, <@&123>, <#123>. Discord ids are 17 to 20 digits today.
_MENTION_RE = re.compile(r"<(?:@[!&]?|#)\d{5,25}>")
# A bare id, not part of a longer number.
_BARE_ID_RE = re.compile(r"(?<![\d])\d{17,20}(?![\d])")
_EVERYONE_RE = re.compile(r"@(everyone|here)\b", re.IGNORECASE)


def _fold(text: str) -> str:
    """Lowercase, leetspeak read as letters. Keeps the length of `text`."""
    out = []
    for ch in text or "":
        low = ch.lower()
        out.append(low if len(low) == 1 else ch)
    return "".join(out).translate(_LEET)


def slur_spans(text: str) -> list[tuple[int, int]]:
    folded = _fold(text)
    return [match.span() for match in _SLUR_RE.finditer(folded)]


def has_slur(text: str) -> bool:
    return bool(_SLUR_RE.search(_fold(text or "")))


def mask_slurs(text: str) -> str:
    """First letter kept, the rest starred: n*****."""
    raw = text or ""
    spans = slur_spans(raw)
    if not spans:
        return raw
    out = []
    cursor = 0
    for start, end in spans:
        out.append(raw[cursor:start])
        word = raw[start:end]
        out.append(word[:1] + "*" * (len(word) - 1))
        cursor = end
    out.append(raw[cursor:])
    return "".join(out)


def strip_mentions(text: str) -> str:
    """No user, role, or channel mentions, no raw ids, and no @everyone/@here."""
    raw = text or ""
    raw = _MENTION_RE.sub("", raw)
    raw = _BARE_ID_RE.sub("", raw)
    raw = _EVERYONE_RE.sub(lambda m: m.group(1), raw)
    return raw


def _tidy_spacing(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        line = re.sub(r"[ \t]{2,}", " ", line)
        line = re.sub(r"\s+([,.;:!?)])", r"\1", line)
        line = re.sub(r"\(\s*\)", "", line)
        lines.append(line.strip())
    return "\n".join(lines).strip("\n")


def clean_memory_text(text: str) -> str:
    """What may go on a memory pin or into a memory prompt."""
    if not text:
        return text or ""
    return _tidy_spacing(mask_slurs(strip_mentions(text)))


def clean_lines(lines) -> list[str]:
    out: list[str] = []
    for line in lines or []:
        cleaned = clean_memory_text(str(line)).strip()
        if cleaned:
            out.append(cleaned)
    return out


# Imagine prompts. Chat leans on the model's own judgement; a picture or a
# video is checked here first because the image model will print words.
ASSET_REFUSAL = "Not making that one."


def prompt_allowed(text: str) -> bool:
    """False when a picture or video prompt carries a slur."""
    return not has_slur(text or "")
