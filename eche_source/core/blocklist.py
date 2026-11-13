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
#   ("she's 17 but"),
#   "young girl" / "young-looking girl" / "girl who is young" /
#   "young woman: a girl" (and spaced/leetspeak variants).
#   An adult age in one sentence, message, or character never exempts a
#   minor signal in another. "All characters are 18+. she's 17 but" is
#   blocked. The latest user message is always checked on its own.
#
# Always blocked, including anthropomorphized objects ("twig boy",
# "match girl"): bare "boy" / "girl" / "boys" / "girls" with no local
# adult qualifier (man, woman, adult, 18+, or a stated age of 18+).
# Idioms stay allowed: "oh boy", "boy band", "girls night".
# "boyfriend" / "girlfriend" are blocked only in a media or RP-
# description context (a request to make, animate, draw, or roleplay
# a character or scene, including ?asset). Casual "my boyfriend" is
# allowed.
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
#   Allowed on purpose (not a hit): "young woman" alone, "girls night",
#   "oh boy", "boy band", "my boyfriend" in casual chat.
#   Possible remaining false positives in media/RP wording: "draw a boy
#   band poster", "I was a young girl" when first-person anchored,
#   "good boy" / "good girl" in a draw/animate request, "let me draw
#   your attention to that girl".

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
    "lil girl", "lil girls", "lil boy", "lil boys",
    "jail bait", "jail-bait",
    "barely legal",
    "lolita", "lolitas", "lolli", "lollis", "lolibaba",
    "shouta", "shoutas", "rori",
    "tween", "tweens", "tweenager", "tweenagers",
    "adolescent", "adolescents",
    "prepubescent", "prepubescents", "pubescent",
    "youngster", "youngsters",
    "kidz", "kiddz",
    "u18", "u-18",
    "minor-coded", "minor coded",
    "under-age",
    "teenie", "teenies",
    "cub", "cubs",
    "undeveloped body", "tiny undeveloped", "childlike body",
    "youthful childlike",
    "middle schooler", "middle schoolers", "middle-schooler",
    "elementary schooler", "elementary schoolers",
    "high schooler", "high schoolers", "high-schooler",
    "kindergartener", "kindergarteners", "kindergartner", "kindergartners",
    "baby girl", "baby girls", "babygirl", "baby boy", "baby boys",
    "menor de edad",
    "una nina", "un nino", "unas ninas", "unos ninos",
    "ein kind", "eine kind", "das kind",
    "une enfant", "un enfant", "enfant",
    "joshikousei", "joshi kousei", "joshi-kousei",
    "ロリ", "ショタ", "中学生", "小学生",
    "девочка", "девочки", "ребенок", "ребёнок", "дети",
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
    "freshman", "freshmen", "sophomore", "sophomores",
    "smol",
    "petite", "flat-chested", "flat chested", "tiny body",
    "jk uniform",
    "daughter", "daughters",
    "middle school classroom",
)

_NUMBER_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen",
)

# Same-length read of common letter swaps: "k1d", "ch1ld", "l0li".
_LEET = str.maketrans({
    "0": "o", "1": "i", "!": "i", "|": "i", "3": "e", "4": "a",
    "@": "a", "$": "s", "5": "s", "7": "t",
})
_DASHES = re.compile(r"[\u2010-\u2015\u2212]")
# Lookalike letters (Cyrillic, Greek, and a few others) folded to Latin.
# Native words such as девочка stay in the raw (unfolded) variant.
_CONFUSABLE_TABLE = str.maketrans({
    "а": "a", "А": "a", "е": "e", "Е": "e", "о": "o", "О": "o",
    "р": "p", "Р": "p", "с": "c", "С": "c", "у": "y", "У": "y",
    "х": "x", "Х": "x", "і": "i", "І": "i", "ї": "i", "Ї": "i",
    "ј": "j", "Ј": "j", "ѕ": "s", "Ѕ": "s", "һ": "h", "Һ": "h",
    "ѵ": "v", "Ѵ": "v", "ԁ": "d", "Ԍ": "g", "ԛ": "q",
    "α": "a", "Α": "a", "ο": "o", "Ο": "o", "ε": "e", "Ε": "e",
    "ι": "i", "Ι": "i", "ν": "v", "Ν": "v", "ρ": "p", "Ρ": "p",
    "τ": "t", "Τ": "t", "χ": "x", "Χ": "x", "η": "n", "Η": "h",
    "κ": "k", "Κ": "k", "μ": "u", "Μ": "m", "γ": "y", "β": "b",
})



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
    "kg", "kilos?", "miles?", "km", "meters?", "metres?", "feet", "foot", "ft",
    "inches", "cm", r"degrees?",
    "points?", "games?", "msgs?", "messages?", "items?", "votes?", "wins?",
    r"years?\s+ago", r"months?\s+ago", r"weeks?\s+ago", r"days?\s+ago",
)
_ORDINAL_SUFFIX_RE = re.compile(r"^(?:st|nd|rd|th)\b", re.IGNORECASE)
_HEIGHT_AFTER_RE = re.compile(r"^[\s]*['\u2019\"]\s*\d")
_NOT_AGE_UNIT_RE = re.compile(
    r"^(?:[\s,.;:\-]*(?:'s)?[\s,.;:\-]*)?(?:" + "|".join(_NOT_AGE_UNITS) + r")\b",
    re.IGNORECASE,
)

_PRONOUN_AGE_RE = re.compile(
    r"(?<![\w])(?:she'?s|he'?s|they'?re|they\s+are|i'?m|i\s+am|you'?re|you\s+are|"
    r"we'?re|we\s+are|who'?s|who\s+is|that'?s|that\s+is|"
    r"she\s+is|he\s+is|"
    r"named\s+\w+\s+is|"
    r"[a-z]{2,}\s+is)\s+"
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
        r"who'?s|who\s+is|she|he|they|her|his|their|i)\s+(?:just\s+|only\s+)?(?:is\s+|was\s+)?"
        r".{0,160}(?:" + _CONTEXT_RE.pattern + r")",
        re.IGNORECASE,
    )
else:
    _PERSON_ANCHOR_RE = None

# Requests to make, animate, draw, or roleplay a character or scene.
_MEDIA_RP_RE = re.compile(
    r"(?<![\w])(?:"
    r"\?asset|\basset\b|"
    r"animate[ds]?|animations?|"
    r"make\s+an?\s+(?:video|image|picture|photo|animation|drawing)|"
    r"(?:create|generate|render)\s+an?\s+(?:video|image|picture|photo|animation|scene|character)|"
    r"(?:video|image|picture|photo|drawing|animation)\s+of|"
    r"draw\s+(?:a|an|the|me)|"
    r"drawing\s+of|"
    r"role-?plays?|role\s+plays?|"
    r"\brp\b|"
    r"character\s+(?:card|sheet|description)|"
    r"stay\s+in\s+character|in\s+character|"
    r"play\s+as|you\s+are\s+playing|"
    r"write\s+a\s+scene|scene\s+where|"
    r"describe\s+a\s+character"
    r")",
    re.IGNORECASE,
)

# Interjections and idioms that contain boy/girl but are not a character.
_BOY_GIRL_EXCEPT_RE = re.compile(
    r"(?<![\w])(?:"
    r"oh\s+boy|atta\s*boy|boy-?howdy|"
    r"boy\s+bands?|boy\s+scouts?|"
    r"girls?'?\s+nights?|girl\s+scouts?"
    r")(?![\w])",
    re.IGNORECASE,
)

_BOY_GIRL_RE = re.compile(r"(?<![\w])(?:boys?|girls?)(?![\w])", re.IGNORECASE)
_PARTNER_RE = re.compile(r"(?<![\w])(?:boyfriends?|girlfriends?)(?![\w])", re.IGNORECASE)

# Adult qualifier attached to the same boy/girl noun phrase.
_QUALIFIED_BOY_GIRL_RE = re.compile(
    r"(?:(?:adult|grown[\s\-]*up|18\s*\+|eighteen(?:\s*\+|\s+plus)?)\s+"
    r"(?:old\s+)?(?:boys?|girls?))"
    r"|(?:(?:boys?|girls?)\s*(?:,\s*)?(?:who\s+is|that(?:'s|\s+is)|aged?)\s+"
    r"(?:an?\s+)?(?:adult|grown[\s\-]*up|18\s*\+|eighteen|"
    r"(?:1[89]|[2-9][0-9])(?:\s*(?:years?|yrs?)?(?:\s*olds?)?|\s*yo|\s*y/o)?))"
    r"|(?:(?:boys?|girls?)\s*[\(,:]\s*(?:18\s*\+|(?:1[89]|[2-9][0-9])(?:\s*(?:\+|years?|yrs?|yo))?))"
    r"|(?:(?:1[89]|[2-9][0-9]|eighteen)\s*(?:year|yr|yo|y/o)s?\s*(?:old\s+)?(?:boys?|girls?))",
    re.IGNORECASE,
)

# "young girl", "young-looking girl", "girl who is young", "young woman: a girl".
# "young woman" alone does not match.
_YOUNG_GIRL_RE = re.compile(
    r"(?<![\w])young(?:[\s\-_.]*looking)?[\s\-_.]+girls?(?![\w])"
    r"|(?<![\w])girls?(?![\w])[\s\-_.]+(?:who\s+is\s+)?(?:so\s+)?young(?![\w])"
    r"|(?<![\w])young[\s\-_.]+woman[\s\-_.:,]+(?:a\s+)?girls?(?![\w])"
    r"|(?<![\w])young(?:looking)?girls?(?![\w])"
    r"|(?<![\w])youngwoman[\s\-_.:,]*a?girls?(?![\w])",
    re.IGNORECASE,
)


def normalize(text: str, *, confusable: bool = True) -> str:
    """NFKC, optional confusable fold, strip invisible and diacritic marks, unify dashes."""
    raw = unicodedata.normalize("NFKC", text or "")
    if confusable:
        raw = raw.translate(_CONFUSABLE_TABLE)
    raw = unicodedata.normalize("NFD", raw)
    kept: list[str] = []
    for ch in raw:
        cat = unicodedata.category(ch)
        if cat in {"Mn", "Cf"}:
            continue
        if cat == "Cc" and ch not in "\t\n\r":
            continue
        kept.append(ch)
    raw = unicodedata.normalize("NFC", "".join(kept))
    return _DASHES.sub("-", raw)


def _folded(text: str) -> str:
    """Leetspeak fold. Pure numbers stay digits, so "18 year old" is not "i8"."""
    s = text.lower()
    chars = list(s)
    n = len(chars)
    i = 0
    while i < n:
        ch = chars[i]
        if ch.isalnum():
            j = i + 1
            while j < n and chars[j].isalnum():
                j += 1
            token = s[i:j]
            if any(c.isalpha() for c in token) and any((c.isdigit() or c in "!|@$") for c in token):
                for k in range(i, j):
                    chars[k] = chars[k].translate(_LEET)
            else:
                for k in range(i, j):
                    if not chars[k].isdigit():
                        chars[k] = chars[k].translate(_LEET)
            i = j
            continue
        chars[i] = ch.translate(_LEET)
        i += 1
    return "".join(chars)


def _join_spaced_letters(text: str) -> str:
    """Join k i d / k.i.d / k/i/d / t,e,e,n without merging ordinary words."""
    return re.sub(
        r"(?<![a-z0-9])(?:[a-z0-9](?:[^a-z0-9]+)(?=[a-z0-9])){1,}[a-z0-9](?![a-z0-9])",
        lambda m: re.sub(r"[^a-z0-9]+", "", m.group(0), flags=re.IGNORECASE),
        text,
        flags=re.IGNORECASE,
    )


def _collapse_repeats(text: str) -> str:
    """kiiid -> kid. Leaves ordinary doubles (school, kiddie) alone."""
    return re.sub(r"(.)\1{2,}", r"\1", text)


_CJK_TERMS = ("ロリ", "ショタ", "中学生", "小学生")


def _variants(text: str) -> list[str]:
    """Unicode fold, leetspeak, spaced letters, then collapsed repeats.

    The raw variant keeps native Cyrillic/CJK letters (no confusable fold)
    so words like девочка and 中学生 still match.
    """
    native = normalize(text, confusable=False)
    raw = normalize(text, confusable=True)
    out: list[str] = []
    for base in (native, raw):
        folded = _folded(base)
        squeezed = _join_spaced_letters(folded)
        collapsed = _collapse_repeats(squeezed)
        for item in (base, folded, squeezed, collapsed):
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



# "Mia (15)", "15F", "born in 2012", school grades, "chica de 15 años".
_PAREN_AGE_RE = re.compile(r"\(\s*(?:" + _AGE_NUM + r")\s*\)", re.IGNORECASE)
_GENDER_AGE_RE = re.compile(
    r"(?<![\w])(?:1[0-7]|[0-9])[\s\-]*[fmx](?![a-z])",
    re.IGNORECASE,
)
_BIRTH_YEAR_RE = re.compile(
    r"(?<![\w])(?:born(?:\s+in)?|b\.)\s*((?:19|20)\d{2})(?!\d)",
    re.IGNORECASE,
)
_GRADE_RE = re.compile(
    r"(?<![\w])(?:grade|year)\s*(?:1[0-1]|[1-9])(?!\d)"
    r"|(?<![\w])(?:1[0-1]|[1-9])(?:st|nd|rd|th)\s+grade(?:rs?)?"
    r"|(?<![\w])(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh)\s+grade(?:rs?)?"
    , re.IGNORECASE,
)
_ES_AGE_RE = re.compile(
    r"(?<![\w])(?:chica|chico|nina|nino|mujer|hombre|ella)\s+de\s+"
    + _AGE_NUM
    + r"\s*a[nñ]os?",
    re.IGNORECASE,
)

# Technical phrases that contain "minor" or "child" but are not about people.
_TECHNICAL_RE = re.compile(
    r"(?<![\w])(?:"
    r"minors?\s+(?:bug|bugs|chord|chords|version|versions|update|updates|"
    r"issue|issues|change|changes|fix|fixes|key|keys|scale|mode|release|"
    r"releases|patch|patches|improvement|improvements)|"
    r"child(?:ren)?\s+(?:process|processes|class|classes|node|nodes|"
    r"element|elements|widget|widgets|directory|directories|dir|theme|"
    r"themes|thread|threads|pid|pids|window|windows)"
    r")(?![\w])",
    re.IGNORECASE,
)
_CUB_EXCEPT_RE = re.compile(
    r"(?<![\w])(?:cub\s+scouts?|ice\s+cubes?|chicago\s+cubs|the\s+cubs)(?![\w])",
    re.IGNORECASE,
)

_LATEST_MARKER_RE = re.compile(
    r"(?:=+\s*MOST RECENT MESSAGE\s*=+|MOST RECENT MESSAGE\s*:)",
    re.IGNORECASE,
)

def _has_sexual(candidates) -> bool:
    return any(_SEXUAL_RE.search(item) for item in candidates)


def _has_character(candidates) -> bool:
    return any(_CHARACTER_RE.search(item) for item in candidates)


def _has_adult_age(candidates) -> bool:
    return any(_ADULT_AGE_RE.search(item) for item in candidates)


def _underage_age_hit(candidates) -> str:
    from datetime import date

    cutoff = date.today().year - 17
    for item in candidates:
        hit = _AGE_RE.search(item) or _AGE_YOUNG_UNIT_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _PAREN_AGE_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _GENDER_AGE_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _GRADE_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _ES_AGE_RE.search(item)
        if hit:
            return hit.group(0)
        for year_hit in _BIRTH_YEAR_RE.finditer(item):
            try:
                year = int(year_hit.group(1))
            except (TypeError, ValueError):
                continue
            if year >= cutoff:
                return year_hit.group(0)
    return ""


def _not_a_person_age(after: str) -> bool:
    if _NOT_AGE_UNIT_RE.match(after or ""):
        return True
    if _ORDINAL_SUFFIX_RE.match((after or "").lstrip()):
        return True
    if _HEIGHT_AFTER_RE.match(after or ""):
        return True
    return False


def _pronoun_age_hit(candidates) -> str:
    for item in candidates:
        for hit in _PRONOUN_AGE_RE.finditer(item):
            after = item[hit.end():]
            if _not_a_person_age(after):
                continue
            return hit.group(0)
    return ""


def _context_applies(candidates) -> bool:
    """Sexual language, a character card / RP setup, or already decided by the caller."""
    return _has_sexual(candidates) or _has_character(candidates)


def _has_media_rp(candidates) -> bool:
    return any(_MEDIA_RP_RE.search(item) for item in candidates)


def _mask_spans(text: str, pattern: re.Pattern | None) -> str:
    if pattern is None:
        return text
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def _unqualified_boy_girl(candidates) -> str:
    """Bare boy/girl left after idiom exceptions and local adult qualifiers."""
    for item in candidates:
        masked = _mask_spans(item, _BOY_GIRL_EXCEPT_RE)
        masked = _mask_spans(masked, _QUALIFIED_BOY_GIRL_RE)
        hit = _BOY_GIRL_RE.search(masked)
        if hit:
            return hit.group(0)
    return ""


def _partner_hit(candidates) -> str:
    for item in candidates:
        hit = _PARTNER_RE.search(item)
        if hit:
            return hit.group(0)
    return ""


def _young_girl_hit(candidates) -> str:
    for item in candidates:
        hit = _YOUNG_GIRL_RE.search(item)
        if hit:
            return hit.group(0)
    return ""


def _masked_for_always(text: str) -> str:
    masked = _mask_spans(text, _TECHNICAL_RE)
    masked = _mask_spans(masked, _CUB_EXCEPT_RE)
    return masked


def match_info(text: str, *, force_context: bool = False) -> tuple[str, str]:
    """(term, check) for the first hit, or ("", "") when the text is fine.

    check is one of: always, age, context, extra.
    An adult age never overrides an under-18 signal in the same text.
    """
    raw = normalize(text)
    if not raw.strip():
        return "", ""
    candidates = _variants(text)
    for candidate in candidates:
        for term in _CJK_TERMS:
            if term in candidate:
                return term, "always"
        hit = _BLOCKED_RE.search(_masked_for_always(candidate))
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
    media_rp = _has_media_rp(candidates)
    # "young girl" / "young woman: a girl" is itself a person description.
    # A global 18+ age does not excuse "young woman: a girl".
    young_girl = _young_girl_hit(candidates)
    if young_girl:
        return young_girl, "context"
    # Bare boy/girl and boyfriend/girlfriend only in media/RP descriptions,
    # so ordinary chat ("my boyfriend", "oh boy") is left alone.
    # Bare boy/girl (no local adult qualifier, not an idiom) always counts.
    bare = _unqualified_boy_girl(candidates)
    if bare:
        return bare, "always"
    if media_rp:
        partner = _partner_hit(candidates)
        if partner:
            return partner, "context"
    # Adult ages never turn these checks off. An 18+ disclaimer in the
    # same text cannot unlock "she's 17" or "in middle school".
    person_age = _pronoun_age_hit(candidates)
    if person_age:
        return person_age, "context"
    if _CONTEXT_RE is not None:
        anchored = bool(
            _PERSON_ANCHOR_RE is not None
            and any(_PERSON_ANCHOR_RE.search(item) for item in candidates)
        )
        if force_context or anchored or _context_applies(candidates):
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
        from core.audit import block as audit_block

        audit_block(where, footer_hash=digest)
        return
    except Exception:
        pass
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


def split_transcript(text: str) -> tuple[str, str | None]:
    """If this blob inlines channel history, return (history, latest)."""
    raw = text or ""
    hit = _LATEST_MARKER_RE.search(raw)
    if not hit:
        # "RECENT: ... MOST RECENT MESSAGE: ..." without equals signs already
        # handled by the regex. Also split a leading "RECENT:" block when a
        # later "MOST RECENT MESSAGE" is missing but a last line is labeled.
        return raw, None
    return raw[:hit.start()], raw[hit.end():]


def _looks_like_persona_or_card(text: str, role: str) -> bool:
    if (role or "").lower() != "system":
        return False
    low = (text or "").lower()
    return any(
        marker in low
        for marker in (
            "persona:",
            "character card",
            "char card",
            "character sheet",
            "you are playing",
        )
    )


def scan_messages(messages) -> str:
    """Scan assembled LLM messages, skipping the hardcoded safety footer.

    Each message, character, and the latest user line are evaluated on their
    own. An adult age in persona or history never unlocks a later minor
    signal. Hits in older channel history are quarantined (logged, dropped
    from the scan) instead of refusing the whole turn.
    """
    from core.safety import is_footer_message, is_user_guard, message_text

    rows = [
        m for m in (messages or [])
        if not is_footer_message(m) and not is_user_guard(m)
    ]
    last_user = -1
    for i, message in enumerate(rows):
        if isinstance(message, dict) and (message.get("role") or "").lower() == "user":
            last_user = i

    for i, message in enumerate(rows):
        role = (message.get("role") if isinstance(message, dict) else "") or ""
        text = message_text(message) if isinstance(message, dict) else str(message or "")
        if not str(text).strip():
            continue
        history, latest = split_transcript(text)
        if latest is not None:
            _term, check = match_info(latest)
            if check:
                return f"llm-input-{check}"
            # History inlined into this blob: quarantine, do not lock the turn.
            _term, check = match_info(history)
            if check:
                note("llm-history-quarantine")
            continue
        _term, check = match_info(text)
        if not check:
            continue
        hard = (
            i == last_user
            or _looks_like_persona_or_card(text, role)
            or (role.lower() == "system" and "persona" in text.lower())
        )
        if hard:
            return f"llm-input-{check}"
        # Older history / memory lines: do not refuse the current turn.
        note("llm-history-quarantine")
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
