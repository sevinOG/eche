# core/blocklist.py
# Hard block on anything involving minors. This is the one thing Eche refuses.
#
# Checked on: the incoming chat message before any model call, the model's
# reply before it is sent, tool arguments and tool replies, every ?asset and
# image or video prompt before the API, thread plans and every thread post,
# and the desktop chat. A match gets REFUSAL and nothing else.
#
# Whole words only, so "kidney", "kidding", and "Kidd" do not match. The
# Settings field "Blocked words" adds more words or phrases to this list.

from __future__ import annotations

import os
import re
import unicodedata

REFUSAL = "Nope. Not going there."

# Whole words, case-insensitive. A space in an entry matches any spacing or a
# hyphen ("pre teen", "pre-teen", "preteen" are all listed or covered).
BLOCKED_WORDS = (
    "kid", "kids", "kiddo", "kiddos", "kiddie", "kiddies",
    "child", "children", "childs",
    "minor", "minors",
    "underage", "under age", "underaged",
    "preteen", "preteens", "pre teen", "pre teens",
    "toddler", "toddlers",
    "infant", "infants",
    "schoolgirl", "schoolgirls", "school girl", "school girls",
    "schoolboy", "schoolboys", "school boy", "school boys",
    "loli", "lolis", "lolicon",
    "shota", "shotas", "shotacon",
    "jailbait",
)

# "baby" is only blocked when the same message is sexual.
BABY_WORDS = ("baby", "babies", "babys")
SEXUAL_WORDS = (
    "sex", "sexy", "sexual", "sexually", "sexualize", "sexualized",
    "nude", "nudes", "naked", "nsfw", "porn", "porno", "pornographic",
    "horny", "lewd", "erotic", "hentai", "fetish", "kink", "kinky",
    "rape", "raped", "molest", "molested", "grope", "groped",
    "orgasm", "cum", "cumming", "boobs", "tits", "nipple", "nipples",
    "dick", "cock", "pussy", "penis", "vagina", "genitals",
    "xxx", "onlyfans", "seduce", "seductive", "aroused", "arousing",
    "undress", "undressed", "undressing", "strip", "stripping", "stripper",
    "lingerie", "fuck", "fucking", "fucked", "smut", "explicit",
)

_NUMBER_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen",
)

# Same-length read of common letter swaps: "k1d", "ch1ld", "l0li".
_LEET = str.maketrans({"0": "o", "1": "i", "!": "i", "|": "i", "3": "e", "4": "a", "@": "a", "$": "s"})
_INVISIBLE = re.compile(r"[\u00ad\u200b-\u200f\u2060\ufeff]")
_DASHES = re.compile(r"[\u2010-\u2015\u2212]")


def _phrase(entry: str) -> str:
    parts = [re.escape(part) for part in entry.split()]
    return r"[\s\-_.]*".join(parts)


def _words_re(entries) -> re.Pattern | None:
    parts = sorted({_phrase(e) for e in entries if e and e.strip()}, key=len, reverse=True)
    if not parts:
        return None
    return re.compile(r"(?<![\w])(?:" + "|".join(parts) + r")(?![\w])", re.IGNORECASE)


_BLOCKED_RE = _words_re(BLOCKED_WORDS)
_BABY_RE = _words_re(BABY_WORDS)
_SEXUAL_RE = _words_re(SEXUAL_WORDS)
# 0-17 as digits or words, next to year-old / years old / yo / y/o / y.o.
_AGE_RE = re.compile(
    r"(?<![\w.])(?:1[0-7]|[0-9]|" + "|".join(_NUMBER_WORDS) + r")"
    r"[\s\-_]*(?:(?:years?|yrs?)[\s\-_]*olds?|y\s*/\s*o|y\.o\.?|yo)(?![\w/])",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    raw = unicodedata.normalize("NFKC", text or "")
    raw = _INVISIBLE.sub("", raw)
    return _DASHES.sub("-", raw)


def _folded(text: str) -> str:
    return text.lower().translate(_LEET)


# ------------------------------------------------------------ Settings words

_extra_cache: tuple[str, float, re.Pattern | None] = ("", -1.0, None)


def parse_extra(raw: str) -> list[str]:
    """Settings text, one word or phrase per line or comma."""
    out: list[str] = []
    for piece in re.split(r"[\n,;]+", raw or ""):
        word = " ".join(piece.split())
        if word and word.casefold() not in (w.casefold() for w in out):
            out.append(word)
    return out


def _extra_re() -> re.Pattern | None:
    global _extra_cache
    try:
        from core.secrets import settings_path

        path = settings_path()
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    except Exception:
        return None
    if _extra_cache[0] == path and _extra_cache[1] == mtime:
        return _extra_cache[2]
    try:
        from core.secrets import public_value

        pattern = _words_re(parse_extra(public_value("blocked_words")))
    except Exception:
        pattern = None
    _extra_cache = (path, mtime, pattern)
    return pattern


def extra_words() -> list[str]:
    try:
        from core.secrets import public_value

        return parse_extra(public_value("blocked_words"))
    except Exception:
        return []


# ------------------------------------------------------------------ checks


def match(text: str) -> str:
    """The first blocked term found, or "" when the text is fine."""
    raw = normalize(text)
    if not raw.strip():
        return ""
    folded = _folded(raw)
    for candidate in (raw, folded):
        hit = _BLOCKED_RE.search(candidate)
        if hit:
            return hit.group(0)
    hit = _AGE_RE.search(raw)
    if hit:
        return hit.group(0)
    for candidate in (raw, folded):
        baby = _BABY_RE.search(candidate)
        if baby and _SEXUAL_RE.search(candidate):
            return baby.group(0)
    extra = _extra_re()
    if extra is not None:
        for candidate in (raw, folded):
            hit = extra.search(candidate)
            if hit:
                return hit.group(0)
    return ""


def is_blocked(*texts) -> bool:
    return any(match(str(text or "")) for text in texts if text)


def note(where: str) -> None:
    """One log line. The blocked text itself is not logged."""
    try:
        from core.gui_bridge import log

        log(f"Blocked ({where}).", channel="chat")
    except Exception:
        print(f"[blocklist] blocked ({where})", flush=True)
