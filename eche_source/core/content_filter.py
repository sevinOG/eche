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


_mask_cache: tuple[str, float, bool] = ("", -1.0, True)


def masking_enabled() -> bool:
    """Settings → Context & Memory: "Mask slurs in memory". Blank stays on."""
    global _mask_cache
    try:
        import os

        from core.secrets import public_value, settings_path

        path = settings_path()
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
        if _mask_cache[0] == path and _mask_cache[1] == mtime:
            return _mask_cache[2]
        stored = public_value("memory_mask_slurs").strip().lower()
    except Exception:
        return True
    enabled = (not stored) or stored in ("1", "true", "yes", "on")
    _mask_cache = (path, mtime, enabled)
    return enabled


def clean_memory_text(text: str) -> str:
    """What may go on a memory pin or into a memory prompt.

    Mentions and ids always go. Slurs are masked while the toggle is on.
    """
    if not text:
        return text or ""
    cleaned = strip_mentions(text)
    if masking_enabled():
        cleaned = mask_slurs(cleaned)
    from core.guardrails import drop_filter_evasion

    cleaned = drop_filter_evasion(cleaned)
    return _tidy_spacing(cleaned)


# The summarizer's "No slurs" rule follows the same toggle.
_SLUR_RULES = (
    ("No slurs, ids, or @mentions.", "No ids or @mentions."),
    ("No slurs, no Discord ids or long id numbers, and no @mentions.",
     "No Discord ids or long id numbers, and no @mentions."),
)


def slur_rule(prompt: str) -> str:
    """The prompt as is while masking is on. Off: the "No slurs" part is dropped."""
    text = prompt or ""
    if masking_enabled():
        return text
    for with_slurs, without in _SLUR_RULES:
        text = text.replace(with_slurs, without)
    return text


def clean_lines(lines) -> list[str]:
    out: list[str] = []
    for line in lines or []:
        cleaned = clean_memory_text(str(line)).strip()
        if cleaned:
            out.append(cleaned)
    return out
