# core/blocklist.py
# Hard block on anything involving minors. This is the one thing Eche refuses
# at the input and output checks. (The LLM safety footer may list other
# categories; those are model instructions only and are not enforced here.)
#
# Checked on: the incoming chat message before any model call, the model's
# reply before it is sent, tool arguments and tool replies, thread plans
# and every thread post, the desktop chat, and the assembled LLM request
# (memory, character cards, persona, and recent history) at the send choke
# point. A match gets REFUSAL and nothing else.
#
# Whole words only, so "kidney", "kidding", and "Kidd" do not match. The
# Settings field "Blocked words" adds more words or phrases to this list.
# Users, personas, character cards, memory, and settings cannot remove or
# edit the built-in list. Settings can only add extra words.
#
# Always blocked, in any context:
#   kid / kids / kiddo / kiddie, child / children, minor / minors, underage,
#   teen / teenager / teenage, preteen, toddler, infant, schoolgirl / schoolboy,
#   little girl / little boy, loli / shota / lolicon / shotacon, jailbait,
#   csam / csem, grooming / groomer, pedo / pedophile / paedophile,
#   child exploitation, age-play / ageplay, and explicit ages under 18
#   (N years old, N yo, age N, aged N, age of N).
#
# Blocked only in a sexual or RP-character context, or when anchored to a
# person ("she's …", "in high school"):
#   baby / babies (already sexual-only),
#   "in high school", "high school student", "high-schooler",
#   "middle school", "elementary school", "grade school", "junior high",
#   "looks young" / "young-looking" / "looks small" / "small-looking",
#   "child-like" / "childlike",
#   a pronoun or "I am" plus an age under 18 without "years old"
#   ("she's 17 but").
#   If the same text clearly states an adult age (18+ / 18 years old) and
#   does not also state an under-18 age, these context-only terms are allowed.
#
# Known false positives to watch:
#   "little boy band", some "little girl/boy" idioms;
#   "looks young" in a character card about an adult, unless 18+ is stated.
#   "high school diploma" and "graduated high school" are allowed (no
#   person-anchor, sexual, or character-card context by themselves).
#   "she's 17 minutes late" and similar unit phrases are allowed.
#   Bare "thirteen" / "seventeen" without age phrasing stays allowed.
#   "kidney", "kidding", "childhood", "childish", "minority", "canteen",
#   "teeny", "baby shark", and stated 18+ ages stay allowed.

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata

REFUSAL = "Nope. Not going there."

# Whole words, case-insensitive. A space in an entry matches any spacing or a
# hyphen ("pre teen", "pre-teen", "preteen" are all listed or covered).
BLOCKED_WORDS = (
    "kid", "kids", "kiddo", "kiddos", "kiddie", "kiddies",
    "child", "children", "childs",
    "minor", "minors",
    "underage", "under age", "underaged",
    "teen", "teens", "teenager", "teenagers", "teenage", "teenaged",
    "teen age", "teen aged",
    "preteen", "preteens", "pre teen", "pre teens",
    "toddler", "toddlers",
    "infant", "infants",
    "schoolgirl", "schoolgirls", "school girl", "school girls",
    "schoolboy", "schoolboys", "school boy", "school boys",
    "loli", "lolis", "lolicon",
    "shota", "shotas", "shotacon",
    "jailbait",
    "csam", "csem",
    "grooming", "groomer", "groomers",
    "pedo", "pedos",
    "pedophile", "pedophiles", "pedophilia",
    "paedophile", "paedophiles", "paedophilia",
    "child exploitation", "child sexual exploitation",
    "age play", "ageplay", "age-play",
    "little girl", "little girls", "little boy", "little boys",
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

# Phrases that are only blocked in a sexual or RP-character context, or
# when they clearly describe a person. See the module header.
CONTEXT_PHRASES = (
    "in high school", "high school student", "high schooler", "high-schooler",
    "in middle school", "middle school", "middle schooler", "middle-schooler",
    "elementary school", "grade school", "junior high",
    "looks young", "looks so young", "young looking", "young-looking",
    "looks small", "small looking", "small-looking",
    "child-like", "childlike", "child like",
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
# 0-17 as digits or words, next to year-old / years old / years of age /
# yo / y/o / y.o., or after aged / age / age of.
_AGE_NUM = r"(?:1[0-7]|[0-9]|" + "|".join(_NUMBER_WORDS) + r")"
_AGE_RE = re.compile(
    r"(?<![\w.])" + _AGE_NUM + r"[\s\-_]*"
    r"(?:(?:years?|yrs?)[\s\-_]*(?:olds?|of[\s\-_]*age)|y\s*/\s*o|y\.o\.?|yo)"
    r"(?![\w/])"
    r"|(?<![\w])(?:aged|age)[\s\-_]*:?[\s\-_]*(?:of[\s\-_]*)?" + _AGE_NUM + r"(?![\w])",
    re.IGNORECASE,
)

# "5 months old" / "3 weeks old" are infants. "17 months ago" is not.
_AGE_YOUNG_UNIT_RE = re.compile(
    r"(?<![\w.])" + _AGE_NUM + r"[\s\-]*"
    r"(?:months?|weeks?|days?)[\s\-]*olds?"
    r"(?![\w])",
    re.IGNORECASE,
)

# Units that mean the number is not a person's age.
_NOT_AGE_UNITS = (
    "minutes?", "hours?", "seconds?", "days?", "weeks?",
    "percent", r"%", "dollars?", "bucks", "times?", "pounds?", "lbs",
    "kg", "kilos?", "miles?", "km", "meters?", "metres?", "feet", "ft",
    "inches", "cm", r"degrees?",
    r"years?\s+ago", r"months?\s+ago", r"weeks?\s+ago", r"days?\s+ago",
)
_NOT_AGE_UNIT_RE = re.compile(
    r"^(?:[\s,.;:\-]*(?:'s)?[\s,.;:\-]*)?(?:" + "|".join(_NOT_AGE_UNITS) + r")\b",
    re.IGNORECASE,
)

_PRONOUN_AGE_RE = re.compile(
    r"(?<![\w])(?:she'?s|he'?s|they'?re|they\s+are|i'?m|i\s+am|you'?re|you\s+are|"
    r"we'?re|we\s+are|who'?s|who\s+is|that'?s|that\s+is|"
    r"named\s+\w+\s+is)\s+"
    r"(?:just\s+|only\s+)?"
    + _AGE_NUM
    + r"(?!\d)",
    re.IGNORECASE,
)

_ADULT_AGE_NUM = r"(?:1[89]|[2-9][0-9]|1[0-9]{2}|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
_ADULT_AGE_RE = re.compile(
    r"(?<![\w.])(?:18\s*\+|eighteen\s*\+)(?![\w])"
    r"|(?<![\w.])" + _ADULT_AGE_NUM + r"[\s\-_]*"
    r"(?:(?:years?|yrs?)[\s\-_]*(?:olds?|of[\s\-_]*age)|y\s*/\s*o|y\.o\.?|yo)"
    r"(?![\w/])"
    r"|(?<![\w])(?:aged|age)[\s\-_]*:?[\s\-_]*(?:of[\s\-_]*)?" + _ADULT_AGE_NUM + r"(?![\w])",
    re.IGNORECASE,
)

_CHARACTER_RE = re.compile(
    r"(?<![\w])(?:character\s+card|char\s+card|character\s+sheet|"
    r"you\s+are\s+playing|play\s+as|roleplay\s+as|role-?play\s+as|"
    r"your\s+name\s+is)\b"
    r"|\|\s*age\s*\|"
    r"|(?<![\w])age\s*:",
    re.IGNORECASE,
)

_CONTEXT_RE = _words_re(CONTEXT_PHRASES)
if _CONTEXT_RE is not None:
    _PERSON_ANCHOR_RE = re.compile(
        r"(?<![\w])(?:she'?s|he'?s|they'?re|they\s+are|i'?m|i\s+am|you'?re|you\s+are|"
        r"who'?s|who\s+is|she|he|they|i)\s+(?:just\s+|only\s+)?(?:is\s+|was\s+)?"
        r".{0,40}(?:" + _CONTEXT_RE.pattern + r")",
        re.IGNORECASE,
    )
else:
    _PERSON_ANCHOR_RE = None


def normalize(text: str) -> str:
    raw = unicodedata.normalize("NFKC", text or "")
    raw = _INVISIBLE.sub("", raw)
    return _DASHES.sub("-", raw)


def _folded(text: str) -> str:
    return text.lower().translate(_LEET)


def _join_spaced_letters(text: str) -> str:
    """Join k i d / k.i.d / l o l i without merging ordinary words."""
    return re.sub(
        r"(?<![a-z0-9])(?:[a-z0-9](?:[\s\-_.~*]+)(?=[a-z0-9])){1,}[a-z0-9](?![a-z0-9])",
        lambda m: re.sub(r"[\s\-_.~*]+", "", m.group(0)),
        text,
        flags=re.IGNORECASE,
    )


def _collapse_repeats(text: str) -> str:
    """kiiid -> kid. Leaves ordinary doubles (school, kiddie) alone."""
    return re.sub(r"(.)\1{2,}", r"\1", text)


def _variants(text: str) -> list[str]:
    """Unicode fold, leetspeak, spaced letters, then collapsed repeats."""
    raw = normalize(text)
    folded = _folded(raw)
    squeezed = _join_spaced_letters(folded)
    collapsed = _collapse_repeats(squeezed)
    out: list[str] = []
    for item in (raw, folded, squeezed, collapsed):
        if item and item not in out:
            out.append(item)
    return out


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


def _has_sexual(candidates) -> bool:
    return any(_SEXUAL_RE.search(item) for item in candidates)


def _has_character(candidates) -> bool:
    return any(_CHARACTER_RE.search(item) for item in candidates)


def _has_adult_age(candidates) -> bool:
    return any(_ADULT_AGE_RE.search(item) for item in candidates)


def _underage_age_hit(candidates) -> str:
    for item in candidates:
        hit = _AGE_RE.search(item) or _AGE_YOUNG_UNIT_RE.search(item)
        if hit:
            return hit.group(0)
    return ""


def _pronoun_age_hit(candidates) -> str:
    for item in candidates:
        for hit in _PRONOUN_AGE_RE.finditer(item):
            after = item[hit.end():]
            if _NOT_AGE_UNIT_RE.match(after):
                continue
            return hit.group(0)
    return ""


def _context_applies(candidates) -> bool:
    """Sexual language, a character card / RP setup, or already decided by the caller."""
    return _has_sexual(candidates) or _has_character(candidates)


def match_info(text: str) -> tuple[str, str]:
    """(term, check) for the first hit, or ("", "") when the text is fine.

    check is one of: always, age, context, extra.
    """
    raw = normalize(text)
    if not raw.strip():
        return "", ""
    candidates = _variants(text)
    for candidate in candidates:
        hit = _BLOCKED_RE.search(candidate)
        if hit:
            return hit.group(0), "always"
    age = _underage_age_hit(candidates)
    if age:
        return age, "age"
    # baby is context-only (sexual). Keep the historical check name via context.
    if _has_sexual(candidates):
        for candidate in candidates:
            baby = _BABY_RE.search(candidate)
            if baby:
                return baby.group(0), "context"
    allow_context = not (_has_adult_age(candidates) and not age)
    if allow_context:
        person_age = _pronoun_age_hit(candidates)
        if person_age:
            return person_age, "context"
        if _CONTEXT_RE is not None:
            # Person-anchored school / looks-young always counts as context.
            anchored = bool(
                _PERSON_ANCHOR_RE is not None
                and any(_PERSON_ANCHOR_RE.search(item) for item in candidates)
            )
            if anchored or _context_applies(candidates):
                for candidate in candidates:
                    hit = _CONTEXT_RE.search(candidate)
                    if hit:
                        return hit.group(0), "context"
    extra = _extra_re()
    if extra is not None:
        for candidate in candidates:
            hit = extra.search(candidate)
            if hit:
                return hit.group(0), "extra"
    return "", ""


def match(text: str) -> str:
    """The first blocked term found, or "" when the text is fine."""
    term, _check = match_info(text)
    return term


def note(where: str) -> None:
    """One log line. The blocked text itself is not logged. Includes the footer hash."""
    digest = ""
    try:
        from core.safety import footer_sha256

        digest = footer_sha256()
    except Exception:
        digest = ""
    extra = f", footer sha256={digest}" if digest else ""
    line = f"Blocked ({where}){extra}."
    try:
        from core.gui_bridge import log

        log(line, channel="chat")
    except Exception:
        print(f"[blocklist] {line}", flush=True)


def scan_texts(*texts) -> str:
    """The check name if any text is blocked, else ""."""
    for text in texts:
        raw = str(text or "")
        if not raw:
            continue
        _term, check = match_info(raw)
        if check:
            return f"llm-input-{check}"
    return ""


def scan_messages(messages) -> str:
    """Scan assembled LLM messages, skipping the hardcoded safety footer."""
    from core.safety import request_texts

    blobs = request_texts(messages)
    if not blobs:
        return ""
    # Scan the whole expanded context together so a card in one message
    # and a school phrase in another still count as character context.
    joined = "\n".join(blobs)
    _term, check = match_info(joined)
    if check:
        return f"llm-input-{check}"
    return ""

# ---------------------------------------------------------- sticky refusals
# After a minors block, close rephrasings from the same user in the same
# channel are also refused for a short window, so swapping one word
# ("kids" → "people", "toddler" → "pouts") does not get through.

BLOCK_WINDOW = 10 * 60
_OVERLAP = 0.6
_MIN_TOKENS = 4
_STOP = frozenset("""
    a an the to of and or but while like in on at for with from by as is it
    this that these those any anything some somehow so then than too very
    just be been being was were are am do did does doing have has had
    not no yes if when what who whom whose which how why
    i you he she we they me him her us them my your his their
    video image picture photo pic make create generate of
""".split())

_recent_lock = threading.Lock()
# (user_id, channel_id) -> list[(expires_at, tokens)]
_recent: dict[tuple[object, object], list[tuple[float, frozenset[str]]]] = {}


def _tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", normalize(text).casefold())
    return frozenset(w for w in words if len(w) > 1 and w not in _STOP)


def clear_recent_blocks() -> None:
    """Tests only."""
    with _recent_lock:
        _recent.clear()


def remember_block(user_id, channel_id, text: str) -> None:
    """Keep a refused prompt so a close rewrite can be caught."""
    tokens = _tokens(text)
    if user_id is None or len(tokens) < _MIN_TOKENS:
        return
    key = (user_id, channel_id)
    expires = time.monotonic() + BLOCK_WINDOW
    with _recent_lock:
        rows = [row for row in _recent.get(key, []) if row[0] > time.monotonic()]
        rows.append((expires, tokens))
        _recent[key] = rows[-8:]


def close_variant(user_id, channel_id, text: str) -> bool:
    """True when `text` is a close rewrite of a recent refusal in this place."""
    if user_id is None:
        return False
    tokens = _tokens(text)
    if len(tokens) < 2:
        return False
    key = (user_id, channel_id)
    now = time.monotonic()
    with _recent_lock:
        rows = [row for row in _recent.get(key, []) if row[0] > now]
        if not rows:
            _recent.pop(key, None)
            return False
        _recent[key] = rows
        for _expires, prior in rows:
            if not prior:
                continue
            if len(tokens & prior) / len(prior) >= _OVERLAP:
                return True
    return False


def is_blocked(*texts, user_id=None, channel_id=None) -> bool:
    """True when any text hits the minors list, or is a close rewrite of one."""
    found = False
    for text in texts:
        raw = str(text or "")
        if not raw:
            continue
        if match(raw):
            remember_block(user_id, channel_id, raw)
            found = True
        elif close_variant(user_id, channel_id, raw):
            remember_block(user_id, channel_id, raw)
            found = True
    return found
