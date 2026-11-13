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

from pathlib import Path

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
    "mãdchen", "madchen", "mädchen", "kinder",
    "fillette", "fillettes", "gamine", "gamines",
    "menina", "meninas", "crianca", "criança", "criancas", "crianças",
    "школьница", "школьницы", "малолетка", "малолетки",
    "여중생", "어린이", "미성년자", "로리",
    "طفلة", "قاصر", "طفل",
    "bambina", "bambine", "bambino", "bambini",
    "dziewczynka", "dziewczynki",
    "cocuk", "çocuk", "çocuklar",
    "बच्ची", "बच्चा", "बच्चे",
    "meisje", "meisjes",
    "幼女", "萝莉", "未成年", "児童", "女子小学生",
    "elfling", "elflings",
    "randoseru",
    "cherub", "cherubs", "cherubim",
    # Swahili / Indonesian (Latin script, whole words)
    "mtoto", "watoto", "msichana", "msichana mdogo",
    "anak kecil", "bocil",
    "anak perempuan", "binti mdogo",
    "be gai",
    "batang babae", "batang lalaki",
    "kislany", "kislány",
    "holcicka", "holčička",
    "tytto", "tyttö",
    "flicka", "flickor",
    "fetita", "fetiță",
    "sweet sixteen", "sweet 16",
    "quinceanera", "quinceañera",
    "bat mitzvah", "bar mitzvah",
    "too young to drive",
    "first year at hogwarts", "first-year at hogwarts",
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
    "spicy", "steamy", "intimate", "sensual", "bedroom",
    "bath", "shower", "bubble bath",
    "describe her body", "describe her",
    "flirt", "flirting", "flirted",
    "hot date", "get physical",
    "18+", "18 plus", "eighteen+",
    "kiss", "kissing", "kissed", "kisses",
    "passionate", "passionately",
    "sultry", "wearing nothing", "make it hot", "cuddle up",
    "nibble her neck",
    "rule 34", "r34", "lemon fic", "h-scene", "hscene", "moans",
    "seggs", "ero", "rated r", "nsfw-ish", "nsfwish",
    "choke me", "choke me daddy",
    "bdsm", "pegging", "bend over",
    "ride me", "nothing underneath", "sit on my face",
    "wet and ready", "daddy",
    "netflix and chill", "netflix & chill",
    "under the covers", "under the blanket", "under blanket",
    "between her thighs", "her body", "lick",
    "play with me", "let's rp", "lets rp",
    "do not stop", "don't stop",
    "make him beg", "lights off", "bathtime",
    "leash play", "on my lap", "uwu", "wink", "sexxxy",
)

# Phrases that are only blocked in a sexual or RP-character context, or
# when they clearly describe a person. See the module header.
CONTEXT_PHRASES = (
    "in high school", "high school student", "high schooler", "high-schooler",
    "in middle school", "middle school", "middle schooler", "middle-schooler",
    "elementary school", "in elementary", "grade school", "junior high",
    "primary school", "in primary",
    "lil cutie",
    "looks young", "looks so young", "young looking", "young-looking",
    "looks small", "small looking", "small-looking",
    "child-like", "childlike", "child like",
    "freshman", "freshmen", "sophomore", "sophomores",
    "smol",
    "petite", "flat-chested", "flat chested", "tiny body",
    "jk uniform",
    "daughter", "daughters",
    "middle school classroom",
    "just started high school", "started high school",
    "gcse", "gcses",
    "too young for prom", "young for prom",
    "too young to drive", "too young to vote",
    "years away from voting", "years from voting",
    "youthful", "youthful features",
    "pigtails and braces", "pigtails",
    "school uniform", "sailor fuku", "sailor uniform",
    "pupil", "pupils",
    "chibi",
    "in a child's body", "in a childs body", "child's body",
    "looks about", "looks like a",
    "step-sister", "step sister", "stepsister",
    "babysitter",
)

_NUMBER_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen",
)
_NUMBER_WORD_VALUES = {word: i for i, word in enumerate(_NUMBER_WORDS)}

# Same-length read of common letter swaps: "k1d", "ch1ld", "l0li".
_LEET = str.maketrans({
    "0": "o", "1": "i", "!": "i", "|": "i", "3": "e", "4": "a",
    "@": "a", "$": "s", "5": "s", "7": "t",
})
_DASHES = re.compile(r"[\u2010-\u2015\u2212]")
# Lookalike letters (Cyrillic, Greek, and a few others) folded to Latin.
# Native words such as девочка stay in the raw (unfolded) variant.
# Extra mappings live in core/confusables.txt and are merged in below.
_CONFUSABLE_MAP = {
    "а": "a", "А": "a", "е": "e", "Е": "e", "о": "o", "О": "o",
    "р": "p", "Р": "p", "с": "c", "С": "c", "у": "y", "У": "y",
    "х": "x", "Х": "x", "і": "i", "І": "i", "ї": "i", "Ї": "i",
    "ј": "j", "Ј": "j", "ѕ": "s", "Ѕ": "s", "һ": "h", "Һ": "h",
    "ѵ": "v", "Ѵ": "v", "ԁ": "d", "Ԍ": "g", "ԛ": "q",
    "т": "t", "п": "n", "н": "h", "и": "n", "к": "k",
    "α": "a", "Α": "a", "ο": "o", "Ο": "o", "ε": "e", "Ε": "e",
    "ι": "i", "Ι": "i", "ν": "v", "Ν": "v", "ρ": "p", "Ρ": "p",
    "τ": "t", "Τ": "t", "χ": "x", "Χ": "x", "η": "n", "Η": "h",
    "κ": "k", "Κ": "k", "μ": "u", "Μ": "m", "γ": "y", "β": "b",
    "δ": "d",
    "օ": "o", "Օ": "o", "ɩ": "i", "ı": "i", "ꮶ": "k",
    "Ꮮ": "l", "Ꮎ": "o", "Ꮖ": "i", "Ꭷ": "o", "Ꭲ": "i",
    "Ꮢ": "r", "Ꭼ": "e", "Ꭺ": "a", "Ꮂ": "b",
    "ⲕ": "k", "Ⲕ": "k", "ⲓ": "i", "Ⲓ": "i",
    "ⲇ": "d", "Ⲇ": "d", "ⲟ": "o", "Ⲟ": "o",
    "ⲥ": "c", "Ⲥ": "c", "ⲁ": "a", "Ⲁ": "a",
    "ⲉ": "e", "Ⲉ": "e", "ⲣ": "p", "Ⲣ": "p",
    "ⲛ": "n", "Ⲛ": "n", "ⲧ": "t", "Ⲧ": "t",
    "ⲏ": "h", "Ⲏ": "h",
}

def _load_confusables() -> dict[str, str]:
    mapping = dict(_CONFUSABLE_MAP)
    path = Path(__file__).with_name("confusables.txt")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return mapping
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("	")
        if len(parts) >= 2 and parts[0] and len(parts[0]) == 1 and parts[1] is not None:
            mapping[parts[0]] = parts[1]
    return mapping


_CONFUSABLE_TABLE = str.maketrans(_load_confusables())



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
    r"act\s+(?:like|as)|"
    r"be\s+\w[\w'\-]*(?:\s+\w[\w'\-]*){0,3}\s+for\s+me|"
    r"as\s+\w[\w'\-]*(?:\s+\w[\w'\-]*){0,2}\s*,|"
    r"pretend\s+you(?:'re|\s+are)|"
    r"you\s+are\s+\w+\s+now|"
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


# Hangul filler and Braille blank are used as hidden separators (not Cf/Zs).
_SEPARATOR_STRIP = frozenset({"\u3164", "\u115f", "\u1160", "\u2800"})


def normalize(text: str, *, confusable: bool = True) -> str:
    """NFKC, optional confusable fold, strip invisible and diacritic marks, unify dashes."""
    raw = unicodedata.normalize("NFKC", text or "")
    if confusable:
        raw = raw.translate(_CONFUSABLE_TABLE)
    raw = unicodedata.normalize("NFD", raw)
    kept: list[str] = []
    for ch in raw:
        if ch in _SEPARATOR_STRIP:
            continue
        cat = unicodedata.category(ch)
        if cat in {"Mn", "Cf"}:
            continue
        if cat == "Zs" and ch != " ":
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


def _join_hyphenated(text: str) -> str:
    """Join hyphenated or split syllables: Ali-ce, Won-der-land."""
    return re.sub(
        r"(?<![A-Za-z])(?:[A-Za-z]{1,8}[-])+[A-Za-z]{1,8}(?![A-Za-z])",
        lambda m: m.group(0).replace("-", ""),
        text,
    )


def _collapse_repeats(text: str) -> str:
    """kiiid -> kid. Leaves ordinary doubles (school, kiddie) alone."""
    return re.sub(r"(.)\1{2,}", r"\1", text)


_CJK_TERMS = (
    "ロリ", "ショタ", "中学生", "小学生", "女子小学生", "幼女", "児童",
    "萝莉", "未成年",
)


_FLIP_UNUSUAL = {
    "ɐ": "a", "ɔ": "c", "ə": "e", "ɟ": "f", "ƃ": "g",
    "ɥ": "h", "ı": "i", "ɾ": "j", "ʞ": "k", "ɯ": "m", "ʌ": "v", "ʍ": "w",
    "ʎ": "y", "ʇ": "t", "ɹ": "r",
}
# Regular p/d/b/q/n/u are only flipped when the text already uses an
# unusual upside-down letter, so "process" is not rewritten as "drocess".
_FLIP_LATIN = {"p": "d", "d": "p", "q": "b", "b": "q", "n": "u", "u": "n"}
_FLIP_UNUSUAL_TABLE = str.maketrans(_FLIP_UNUSUAL)
_FLIP_FULL_TABLE = str.maketrans({**_FLIP_UNUSUAL, **_FLIP_LATIN})

_EMOJI_CHILD_RE = re.compile(r"[👧🧒👶👦]")

# Roman numerals 1-17.
_ROMAN_VALUES = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
    "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13,
    "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17,
}
_ROMAN_AGE_RE = re.compile(
    r"(?<![\w])(?:is|age|aged|years?|yo|turned)\s*"
    r"(i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|xiii|xiv|xv|xvi|xvii)"
    r"(?:\s*(?:years?|yrs?|yo|years?\s*old))?(?![\w])"
    r"|(?<![\w])(i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|xiii|xiv|xv|xvi|xvii)"
    r"\s*(?:years?\s*old|yrs?\s*old|yo)(?![\w])",
    re.IGNORECASE,
)
_CLASS_OF_RE = re.compile(r"(?<![\w])class\s+of\s+((?:19|20)\d{2})(?!\d)", re.IGNORECASE)
_LOOKS_AGE_RE = re.compile(
    r"(?<![\w])looks?(?:\s+about|\s+like(?:\s+a|\s+she(?:['’]s| is)|\s+he(?:['’]s| is))?|\s+around)?\s*"
    r"(?:1[0-7]|[0-9]|" + "|".join(_NUMBER_WORDS) + r")(?![\w])",
    re.IGNORECASE,
)
_INTL_AGE_RE = re.compile(
    r"(?<![\w])(?:ist|hat|hatte|a|tem|tiene|ей|їй|ей)\s+"
    r"(?:1[0-7]|[0-9]|" + "|".join(_NUMBER_WORDS) + r")"
    r"\s*(?:jahre|ans|anos|años|лет|jaar|let|tahun|tuổi|roków|років|ετών|χρόνια|år)(?:\s*alt)?"
    r"|(?<![\w])(?:1[0-7]|[0-9]|" + "|".join(_NUMBER_WORDS) + r")"
    r"\s*(?:jahre|ans|anos|años|лет|jaar|let|tahun|tuổi|roków|років|ετών|χρόνια|år)(?:\s*alt)?(?![\w])"
    r"|(?<![\w])(?:1[0-7]|[0-9])\s*-?\s*vuotias"
    r"|(?:อายุ)\s*(?:1[0-7]|[0-9])(?!\d)"
    r"|(?<![\w])(?:בת|בן)\s*(?:1[0-7]|[0-9])(?!\d)",
    re.IGNORECASE,
)
_JUST_TURNED_RE = re.compile(
    r"(?<![\w])just\s+turned\s+(?:1[0-7]|[0-9]|" + "|".join(_NUMBER_WORDS) + r")(?![\w])",
    re.IGNORECASE,
)
_MATH_AGE_RE = re.compile(
    r"(?<![\w])(?:is|age|aged|she(?:'s| is)|he(?:'s| is)|they(?:'re| are)|her age is|his age is|age is)\s+"
    r"(?:"
    r"(\d+)\s*\*\s*(\d+)"
    r"|(\d+)\s*\+\s*(\d+)"
    r"|0x([0-9a-f]+)"
    r"|twenty\s+minus\s+(?:five|5|four|4|three|3|two|2|one|1|six|6|seven|7|eight|8|nine|9|ten|10|eleven|11|twelve|12)"
    r"|half\s+of\s+(?:thirty|30|twenty|20|twenty[- ]two|twenty[- ]four|twenty[- ]six|twenty[- ]eight|32|28|26|24|22)"
    r")",
    re.IGNORECASE,
)
_ORDINAL_NUM = (
    r"(?:1[0-7]|[0-9]|first|second|third|fourth|fifth|sixth|seventh|"
    r"eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|"
    r"fifteenth|sixteenth|seventeenth)"
)
_ORDINAL_BIRTHDAY_RE = re.compile(
    r"(?<![\w])(?:1[0-7]|[0-9])(?:st|nd|rd|th)\s+(?:birthday|bday)"
    r"|(?<![\w])(?:first|second|third|fourth|fifth|sixth|seventh|"
    r"eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|"
    r"fifteenth|sixteenth|seventeenth)\s+(?:birthday|bday)",
    re.IGNORECASE,
)
_YEARS_FROM_VOTING_RE = re.compile(
    r"(?<![\w])(?:\d+|" + "|".join(_NUMBER_WORDS) + r")\s+years?\s+"
    r"(?:away\s+from|from|until|before)\s+(?:voting|being\s+able\s+to\s+vote|the\s+vote)",
    re.IGNORECASE,
)
_BORN_YEAR_WORDS = {
    "nine": 2009, "ten": 2010, "eleven": 2011, "twelve": 2012,
    "thirteen": 2013, "fourteen": 2014, "fifteen": 2015,
    "sixteen": 2016, "seventeen": 2017, "eighteen": 2018,
    "nineteen": 2019, "twenty": 2020, "twenty one": 2021,
    "twenty-one": 2021, "twenty two": 2022, "twenty-two": 2022,
    "twenty three": 2023, "twenty-three": 2023, "twenty four": 2024,
    "twenty-four": 2024, "twenty five": 2025, "twenty-five": 2025,
    "twenty six": 2026, "twenty-six": 2026,
}
_BORN_YEAR_WORDS_RE = re.compile(
    r"(?<![\w])(?:born(?:\s+in)?)\s+two\s+thousand\s+(?:and\s+)?("
    + "|".join(sorted((_BORN_YEAR_WORDS), key=len, reverse=True))
    + r")(?![\w])",
    re.IGNORECASE,
)
_TURNS_LEGAL_IN_RE = re.compile(
    r"(?<![\w])(?:turns|turning|turn)\s+(?:18|eighteen)\s+in\s+"
    r"(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)"
    r"\s+years?",
    re.IGNORECASE,
)
_FROM_LEGAL_RE = re.compile(
    r"(?<![\w])(?:\d+|" + "|".join(_NUMBER_WORDS) + r")\s+years?\s+"
    r"(?:from|until|before|to)\s+(?:being\s+)?(?:legal|18|eighteen)",
    re.IGNORECASE,
)
_BIRTH_DATE_RE = re.compile(
    r"(?<![\w])(?:born(?:\s+on)?|dob|d\.o\.b\.?|date\s+of\s+birth)\s*:?\s*"
    r"(?:"
    r"(?P<m>\d{1,2})[/\-.](?P<d>\d{1,2})[/\-.](?P<y>(?:19|20)\d{2})"
    r"|(?P<y2>(?:19|20)\d{2})[/\-.](?P<m2>\d{1,2})[/\-.](?P<d2>\d{1,2})"
    r")",
    re.IGNORECASE,
)
_STORYBOOK_RE = re.compile(
    r"(?<![\w])(?:"
    r"gretel|goldilocks|goldl[oö]ckchen|goldi(?:lox|l[oö]ckchen)?|"
    r"little\s+red\s+riding\s+hood|red\s+riding\s+hood|riding\s+hood|"
    r"rotk[aä]ppchen|"
    r"caperucita\s+roja|"
    r"(?:le\s+)?petit\s+chaperon\s+rouge|"
    r"boucles?\s+d['’]?or|"
    r"ricitos\s+de\s+oro|"
    r"cappuccetto\s+rosso|"
    r"chapeuzinho\s+vermelho|"
    r"roodkapje|"
    r"czerwony\s+kapturek|"
    r"riccioli\s+d['’]?oro|"
    r"cachinhos\s+dourados|"
    r"z[łl]otow[łl]osa|"
    r"赤ずきん|小红帽|빨간\s*망토|"
    r"красная\s+шапочка|"
    r"wendy\s+darling|wendy\s+moira(?:\s+angela)?\s+darling|"
    r"peter\s+pan|питер\s+пэн|ピーター[・･]?\s*パン|彼得潘|"
    r"matilda(?:\s+w(?:ormwood|\.)?)?|mathilda|matilde|matylda|tilly\s+wormwood|"
    r"h[aä]nsel(?:\s+and\s+gretel|\s+und\s+gretel|\s+en\s+grietje)?"
    r"|hans\s+en\s+grietje"
    r"|heidi(?:\s+the\s+alpine(?:\s+girl)?|\s+from\s+the\s+alps)?"
    r"|ハイジ|хайди"
    r"|pippi(?:\s+l(?:ongstocking|ångstrump|angstrump|calzaslargas|calzelunghe))?"
    r"|pipi\s+calzaslargas|pippi\s+calzelunghe|пеппи"
    r"|christopher\s+robin|christopher\s+r\.|chris\s+robin|c\.\s*robin"
    r"|tiny\s+tim|tim\s+cratchit|(?<![\w])cratchit"
    r"|lilo(?:\s+(?:and|&)\s+stitch|\s+from\s+lilo|\s+pelekai)?|(?<![\w])pelekai"
    r"|anne\s+of\s+green\s+gables|anne\s+shirley"
    r"|(?:dorothy|dorthy|dorothey|dorothie|doroty)(?:\s+from)?(?:\s+the)?\s+(?:wizard\s+of\s+)?oz|"
    r"(?:dorothy|dorthy|dorothey|dorothie|doroty)\s+gale|"
    r"dot(?:tie)?\s+from\s+kansas|"
    r"(?:dorothy|dottie|dot)\s+(?:\w+\s+){0,4}(?:of\s+)?kansas|"
    r"kansas\s+with\s+toto|"
    r"neverland|lost\s+boys|"
    r"boy\s+who\s+never\s+grows\s+up|"
    r"darling\s+girl|"
    r"girl\s+with\s+the\s+red\s+hood|"
    r"girl\s+who\s+ate\s+the\s+three\s+bears(?:['’]s?\s+porridge)?"
    r"|wolf['’]?s\s+girl"
    r"|hundred\s+acre\s+wood"
    r"|lucy\s+pevensie|(?<![\w])pevensie"
    r"|coraline"
    r"|scout\s+finch"
    r"|laura\s+ingalls"
    r"|violet\s+beauregarde"
    r"|madeline(?:\s+from\s+the\s+paris(?:\s+school)?)?"
    r"|caperucita(?:\s+roja)?"
    r"|chaperon\s+rouge"
    r"|rotk[aä]ppi(?:n)?"
    r"|kapturek"
    r"|r[øöo]dhette"
    r"|punahilkka"
    r"|[čc]erven[aáä]\s+karkulka"
    r"|piroska"
    r"|k[iı]rm[iı]z[iı]\s+ba[sş]l[iı]kl[iı]\s+k[iı]z"
    r"|златовласка"
    r"|gold(?:ie|y)\s+locks"
    r"|gouldilocks"
    r"|petr\s+pan|p\.\s*pan|peter\s*pan"
    r"|w\.\s*darling"
    r"|mathilde(?:\s+wormwood)?"
    r"|matty\s+from\s+roald\s+dahl|roald\s+dahl"
    r"|heidy"
    r"|pippy(?:\s+l(?:ongstocking|ångstrump|angstrump))?"
    r"|pipi\s+l[åa]ngstrump"
    r"|anne\s+with\s+an\s+e|anne\s+of\s+avonlea"
    r"|laura\s+from\s+little\s+house"
    r"|jean\s+louise\s+finch"
    r"|lucy\s+from\s+narnia|(?<![\w])narnia"
    r"|charlie\s+bucket"
    r"|veruca\s+salt"
    r"|mowgli"
    r"|eloise(?:\s+at\s+the(?:\s+plaza)?)?"
    r"|ramona\s+quimby"
    r"|junie\s+b\.?\s*jones"
    r"|tinker\s*bell"
    r"|pinocchio|pinocio|pinochio|pinoccio"
    r"|(?<![\w])pipi(?![\w])"
    r"|porridge\s+thief"
    r"|gingerbread\s+house\s+siblings"
    r")(?![\w])",
    re.IGNORECASE,
)

# Short Red Riding Hood forms: only in sexual or RP context, so
# "Little Red Corvette" stays allowed.
_STORYBOOK_CONTEXT_RE = re.compile(
    r"(?i)(?<![\w])(?:little\s+red|lrrh)(?![\w])",
)

# Reviewed benign uses of a storybook name. Word-bounded on both sides so
# "Peter Pan collar" does not eat "Peter Pan collarbone". Applied only
# when the message has no sexual or RP wording; with such wording the
# original text is always checked.
_STORYBOOK_ALLOW_RE = re.compile(
    r"(?i)(?<![\w])(?:"
    r"peter\s+pan\s+(?:collar|complex|syndrome)|"
    r"heidi\s+klum|"
    r"goldilocks\s+(?:economy|zone|principle)|"
    r"dorothy\s+parker|"
    r"tiny\s+tim(?:\s+the)?\s+ukulele|"
    r"gretel\s+killeen|"
    r"alice\s+from\s+accounting|"
    r"matilda(?:['’]?s)?\s+(?:the\s+)?musical|"
    r"pinocchio\s+(?:movie|film|book)\s+review|"
    r"narnia\s+books|"
    r"roald\s+dahl\s+wrote\s+matilda|"
    r"matilda\s+by\s+roald\s+dahl|"
    r"christopher\s+robin\s+milne(?:\s+biography)?"
    r")(?![\w])"
)

# Wonderland / Carroll cues that, with sexual or RP wording, block even
# when the name Alice is absent.
_WONDERLAND_RE = re.compile(
    r"(?is)(?:wonder[\s-]*land|wundirland|wunderland|"
    r"land\s+of\s+wonders|"
    r"pa[ií]s\s+de\s+las\s+maravillas|"
    r"pays\s+des\s+merveilles|"
    r"paese\s+delle\s+meraviglie|"
    r"pa[ií]s\s+das\s+maravilhas|"
    r"стран[аеыу]\s+чудес|strane\s+chudes|"
    r"이상한\s*나라|"
    r"不思議の[国國]|梦游仙境|fushigi\s+no\s+kuni|"
    r"krainie\s+czar[oó]w|underlandet|"
    r"χώρα\s+των\s+θαυμάτων|"
    r"spiegelland|"
    r"carroll['’]?s\s+heroine|dodgson['’]?s\s+muse|"
    r"dodgson['’]?s\s+(?:heroine|child\s+friend)|dodgson|dodgsonian|"
    r"liddell|"
    r"looking[\s-]*glass|"
    r"through\s+the\s+mirror|behind\s+the\s+looking[\s-]*glass|"
    r"looking\s+glass\s+land|"
    r"carrollian|carollian|"
    r"l\.\s*carroll|"
    r"lewis\s+c(?:['’.]s|\s*carroll)|"
    r"lewis['’]s|"
    r"(?<![\w])carroll(?![\w])|"
    r"(?<![\w])hatter|"
    r"blue\s+frock|"
    r"alice\s+im\s+wunderland|"
    r"tweedle|"
    r"pinafore|dinah|tenniel|white\s+apron|"
    r"curiouser\s+and\s+curiouser|"
    r"jabberwocky|"
    r"(?:girl|heroine|child).{0,40}['’\"]?eat\s+me['’\"]?"
    r"|['’\"]?eat\s+me['’\"]?.{0,40}(?:girl|heroine|child)|"
    r"rabbit[\s-]*hole|white\s+rabbit|"
    r"(?<![\w])hatter|mad\s+hatter|march\s+hare|tea\s+party|"
    r"queen\s+of\s+hearts|cheshire|"
    r"wndrlnd)"
)

_HOGWARTS_FIRST_YEAR_RE = re.compile(
    r"(?i)first[\s-]+year\s+at\s+hogwarts|hogwarts\s+first[\s-]+year|"
    r"hermione(?:\s+granger)?(?:\s*,)?(?:\s+\w+){0,4}?\s+first[\s-]*year|"
    r"first[\s-]+year\s+hermione"
)
_AGE_TAGGED_CANON_RE = re.compile(
    r"(?i)(?<![\w])(?:hermione(?:\s+granger)?|harry\s+potter|"
    r"lucy\s+pevensie|coraline|scout\s+finch|wendy(?:\s+darling)?|"
    r"matilda|pippi|heidi|alice|"
    r"christopher\s+robin(?:\s+milne)?)"
    r"(?:\s+\w+){0,4}?\s+(?:at\s+(?:age\s+)?|age\s+)"
    r"(?:1[0-7]|[1-9]|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)"
    r"(?![\w])"
)
_ALICE_NAME_RE = re.compile(
    r"(?i)(?<![\w])(?:alice|alicia|alyce|ally|alicja|alisa|arisu)(?![\w])"
    r"|алиса|アリス|爱丽丝|앨리스|αλίκη"
)
# Bare Alice/Alicia/Алиса/アリス, like Matilda: sexual or RP only. Ally/Alyce
# are story-cue names and are not blocked bare.
_ALICE_BARE_RE = re.compile(
    r"(?i)(?<![\w])(?:alice|alicia|alicja|alisa|arisu)(?![\w])"
    r"|алиса|アリス|爱丽丝|앨리스|αλίκη"
)
_WENDY_BARE_RE = re.compile(
    r"(?i)(?<![\w])wendy(?!['’]s)(?!\s+williams)(?![\w])"
)
_WENDY_SEXUAL_RE = re.compile(
    r"(?i)(?<![\w])wendy(?![\w])"
)
_ALICE_ALLOW_RE = re.compile(
    r"(?i)(?<![\w])(?:"
    r"alice\s+from\s+accounting|"
    r"alicia\s+keys|alice\s+cooper|alice\s+in\s+chains|"
    r"alice\s+walker|alice\s+and\s+bob"
    r")(?![\w])"
)
_ALICE_CUED_NAMES_RE = re.compile(
    r"(?i)(?<![\w])(?:allie|ali|alicent|aliss|alysse?|allisse|alys|"
    r"alisa|arisu|alis|elise|aleece|allyce|"
    r"alise|ailce|aliec|alcie|alisu|alysa|lissie?|liss)(?![\w])"
)
_ALICE_CUES_RE = re.compile(
    r"(?is)(?:wonder[\s-]*land|wundirland|wunderland|"
    r"land\s+of\s+wonders|"
    r"pa[ií]s\s+de\s+las\s+maravillas|maravillas|"
    r"pays\s+des\s+merveilles|merveilles|"
    r"paese\s+delle\s+meraviglie|meraviglie|"
    r"pa[ií]s\s+das\s+maravilhas|maravilhas|"
    r"стран[аеыу]\s+чудес|strane\s+chudes|"
    r"不思議の国|"
    r"carroll['’]?s\s+heroine|carroll|liddell|dodgson|dodgsonian|"
    r"이상한\s*나라|"
    r"不思議の[国國]|梦游仙境|fushigi\s+no\s+kuni|"
    r"krainie\s+czar[oó]w|underlandet|"
    r"χώρα\s+των\s+θαυμάτων|"
    r"spiegelland|"
    r"blue\s+dress|white\s+apron|"
    r"rabbit\s*hole|white\s+rabbit|"
    r"looking[\s-]*glass|"
    r"through\s+the\s+mirror|behind\s+the\s+looking[\s-]*glass|"
    r"looking\s+glass\s+land|"
    r"carrollian|carollian|l\.\s*carroll|lewis['’]s|tweedle|"
    r"curiouser\s+and\s+curiouser|jabberwocky|"
    r"(?<![\w])hatter|mad\s+hatter|march\s+hare|tea\s+party|"
    r"queen\s+of\s+hearts|cheshire|"
    r"pinafore|dinah|tenniel|mcgee|"
    r"disney|kingdom\s+hearts|"
    r"wndrlnd|dodgson['’]?s\s+child\s+friend)"
)

_SEXUAL_EMOJI_RE = re.compile(r"[😏🍆💦🍑👅🥵🔥😈🫦🍒💋🔞🥴🍌🥒🌮🌶]|🌶️")

_HEX_TOKEN_RE = re.compile(r"(?<![\w])(?:0x)?([0-9a-f]{6,})(?![\w])", re.IGNORECASE)
_B64_TOKEN_RE = re.compile(r"(?<![\w])([A-Za-z0-9+/]{8,}={0,2})(?![\w])")
_ASCII_CODES_RE = re.compile(r"(?<!\d)((?:1[0-2]\d|12[0-6]|[3-9]\d)(?:\s+(?:1[0-2]\d|12[0-6]|[3-9]\d)){2,})(?!\d)")
_DECODE_HINT_RE = re.compile(
    r"(?i)(?:decode|decod(?:e|ed|ing)|rot13|base64|hex|backwards|reversed|read backwards|then (?:write|roleplay|act))"
)


def _flip_and_reverse(text: str) -> list[str]:
    out = []
    table = _FLIP_FULL_TABLE if any(ch in _FLIP_UNUSUAL for ch in text) else _FLIP_UNUSUAL_TABLE
    flipped = text.translate(table)
    if flipped != text:
        out.append(flipped)
        out.append(flipped[::-1])
    unusual = any(ch in _FLIP_UNUSUAL for ch in text)
    hinted = bool(_DECODE_HINT_RE.search(text))
    # Do not reverse digit strings by default: "21 y/o" backwards is "12 yo".
    if unusual or hinted or not re.search(r"\d", text):
        out.append(text[::-1])
        words = re.findall(r"\w+", text)
        if words:
            out.append(" ".join(w[::-1] for w in words))
    else:
        words = [w for w in re.findall(r"[A-Za-z]+", text) if len(w) >= 3]
        for w in words:
            out.append(w[::-1])
    return out


def _leet_alt(text: str) -> str:
    """Second leet pass where 1 and | also read as L, and |< as K.

    Purely numeric tokens stay digits, so "18 year old girl" is not
    rewritten as "l8 year old girl".
    """
    s = re.sub(r"\|<", "k", text)
    def _token(match: re.Match) -> str:
        token = match.group(0)
        if token.isdigit():
            return token
        return token.replace("1", "l").replace("|", "l")
    return re.sub(r"[A-Za-z0-9|]+", _token, s)


def _decode_candidates(text: str) -> list[str]:
    """Strings obtained by decoding encodings or following a decode-and-act request."""
    import base64
    import binascii
    import codecs

    raw = text or ""
    found: list[str] = []

    def add(value: str) -> None:
        value = (value or "").strip()
        if value and value not in found and value != raw:
            found.append(value)

    hinted = bool(_DECODE_HINT_RE.search(raw))
    # rot13 of the whole text (and hinted payloads).
    try:
        add(codecs.decode(raw, "rot_13"))
    except Exception:
        pass
    for match in _HEX_TOKEN_RE.finditer(raw.replace(" ", "")):
        token = match.group(1)
        if len(token) % 2:
            continue
        try:
            decoded = binascii.unhexlify(token).decode("utf-8", "ignore")
        except (binascii.Error, ValueError):
            continue
        if decoded and any(ch.isalpha() for ch in decoded):
            add(decoded)
    # Also hex with spaces stripped from original tokens
    compact = re.sub(r"\s+", "", raw)
    if re.fullmatch(r"(?:0x)?[0-9a-fA-F]{6,}", compact or ""):
        token = compact[2:] if compact.lower().startswith("0x") else compact
        if len(token) % 2 == 0:
            try:
                add(binascii.unhexlify(token).decode("utf-8", "ignore"))
            except (binascii.Error, ValueError):
                pass
    for match in _B64_TOKEN_RE.finditer(raw):
        token = match.group(1)
        pad = "=" * (-len(token) % 4)
        try:
            decoded = base64.b64decode(token + pad, validate=False).decode("utf-8", "ignore")
        except Exception:
            continue
        if decoded and any(ch.isalpha() for ch in decoded):
            # Skip if it looks like random binary noise
            if sum(ch.isprintable() for ch in decoded) >= max(3, int(len(decoded) * 0.8)):
                add(decoded)
    for match in _ASCII_CODES_RE.finditer(raw):
        try:
            add("".join(chr(int(n)) for n in match.group(1).split()))
        except ValueError:
            pass
    # "read backwards:" remainder
    back = re.search(r"(?i)(?:read backwards|backwards|reversed)\s*:\s*(.+)$", raw)
    if back:
        add(back.group(1)[::-1])
        add(re.sub(r"\s+", " ", back.group(1)[::-1]))
    if hinted:
        for item in list(found):
            try:
                add(codecs.decode(item, "rot_13"))
            except Exception:
                pass
    return found


_HONORIFIC_RE = re.compile(
    r"(?i)(?<=\w)[-_](?:chan|kun|tan|san|sama|dono|senpai|sensei|cchi|chin|nee|nii)\b"
)


def _strip_honorifics(text: str) -> str:
    return _HONORIFIC_RE.sub("", text or "")


def _variants(text: str) -> list[str]:
    """Unicode fold, leetspeak, spaced letters, then collapsed repeats.

    The raw variant keeps native Cyrillic/CJK letters (no confusable fold)
    so words like девочка and 中学生 still match.
    """
    native = normalize(text, confusable=False)
    raw = normalize(text, confusable=True)
    seeds = [native, raw]
    for seed in list(seeds):
        hon = _strip_honorifics(seed)
        if hon and hon not in seeds:
            seeds.append(hon)
    # Emoji children are themselves a minor signal later; also strip emoji
    # so l🍭o🍭l🍭i collapses.
    stripped = re.sub(r"[🌀-🫿✀-➿]", " ", raw)
    if stripped not in seeds:
        seeds.append(stripped)
    out: list[str] = []
    bases = list(seeds)
    for seed in list(seeds):
        bases.extend(_flip_and_reverse(seed))
        alt = _leet_alt(seed)
        if alt not in bases:
            bases.append(alt)
        for decoded in _decode_candidates(seed):
            if decoded not in bases:
                bases.append(decoded)
    for base in bases:
        if not base:
            continue
        folded = _folded(base)
        squeezed = _join_spaced_letters(folded)
        joined = _join_hyphenated(squeezed)
        collapsed = _collapse_repeats(joined)
        leet_l = _folded(_leet_alt(base))
        leet_joined = _join_hyphenated(_join_spaced_letters(leet_l))
        for item in (base, folded, squeezed, joined, collapsed, leet_l, leet_joined):
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

_SEXUAL_NATIVE = (
    "エロ",
    "エッチ",
    "色情",
    "야한",
    "секси",
    "секс",
    "seksowna",
    "sexig",
    "σέξι",
    "nackt",
    "naakt",
    "ecchi",
    "seksikäs",
    "szexi",
    "seksi",
)


def _has_sexual(candidates) -> bool:
    if any(_SEXUAL_RE.search(item) for item in candidates):
        return True
    if any(_SEXUAL_EMOJI_RE.search(item) for item in candidates):
        return True
    for item in candidates:
        for term in _SEXUAL_NATIVE:
            if term and term in item:
                return True
    return False


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
        hit = _ROMAN_AGE_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _CLASS_OF_RE.search(item)
        if hit:
            try:
                year = int(hit.group(1))
            except (TypeError, ValueError):
                year = 0
            if year >= date.today().year:
                return hit.group(0)
        hit = _JUST_TURNED_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _LOOKS_AGE_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _INTL_AGE_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _MATH_AGE_RE.search(item)
        if hit:
            if hit.group(1) and hit.group(2):
                try:
                    product = int(hit.group(1)) * int(hit.group(2))
                except ValueError:
                    product = 0
                if 1 <= product <= 17:
                    return hit.group(0)
            elif hit.group(3) and hit.group(4):
                try:
                    total = int(hit.group(3)) + int(hit.group(4))
                except ValueError:
                    total = 0
                if 1 <= total <= 17:
                    return hit.group(0)
            elif hit.group(5):
                try:
                    value = int(hit.group(5), 16)
                except ValueError:
                    value = 0
                if 1 <= value <= 17:
                    return hit.group(0)
            else:
                return hit.group(0)
        hit = _ORDINAL_BIRTHDAY_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _TURNS_LEGAL_IN_RE.search(item)
        if hit:
            word = (hit.group("n") or "").lower()
            years = _NUMBER_WORD_VALUES.get(word)
            if years is None:
                try:
                    years = int(word)
                except ValueError:
                    years = 0
            if years >= 1:
                return hit.group(0)
        hit = _FROM_LEGAL_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _YEARS_FROM_VOTING_RE.search(item)
        if hit:
            return hit.group(0)
        hit = _BORN_YEAR_WORDS_RE.search(item)
        if hit:
            word = " ".join((hit.group(1) or "").lower().split())
            year = _BORN_YEAR_WORDS.get(word, 0)
            if year >= cutoff:
                return hit.group(0)
        hit = _BIRTH_DATE_RE.search(item)
        if hit:
            year_s = hit.group("y") or hit.group("y2")
            month_s = hit.group("m") or hit.group("m2")
            day_s = hit.group("d") or hit.group("d2")
            try:
                year, month, day = int(year_s), int(month_s), int(day_s)
                if month > 12 and day <= 12:
                    month, day = day, month
                born = date(year, month, day)
                today = date.today()
                age = today.year - born.year - (
                    (today.month, today.day) < (born.month, born.day)
                )
                if 0 <= age < 18:
                    return hit.group(0)
            except (TypeError, ValueError):
                try:
                    year = int(year_s or 0)
                except (TypeError, ValueError):
                    year = 0
                if year >= cutoff:
                    return hit.group(0)
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



_NATIVE_TERMS = (
    "बच्ची", "बच्चा", "बच्चे",
    "طفلة", "قاصر", "طفل",
    "여중생", "어린이", "미성년자", "로리",
    "幼女", "萝莉", "未成年", "児童", "女子小学生",
    "школьница", "малолетка",
    "Mädchen", "mädchen",
    # Vietnamese
    "trẻ em", "bé gái", "nữ sinh cấp 2", "học sinh cấp 2", "nữ sinh cấp 1",
    "học sinh cấp 1",
    # Thai
    "เด็ก", "เด็กผู้หญิง", "นักเรียนหญิง", "สาวน้อย",
    # Hebrew
    "ילדה", "קטינה", "ילד", "קטין",
    # Greek
    "παιδί", "κοριτσάκι", "ανήλικη", "ανήλικος", "παιδι",
    # Ukrainian
    "дитина", "дівчинка", "школярка", "дітей",
    # Farsi, Bengali, Urdu
    "کودک", "کودکان", "دختر بچه",
    "শিশু", "বাচ্চা",
    "بچی", "بچہ", "بچے",
)

def _native_term_hit(text: str) -> str:
    raw = text or ""
    for term in _NATIVE_TERMS:
        if term and term in raw:
            return term
    return ""


def _edit_distance_at_most(a: str, b: str, limit: int = 1) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > limit:
        return False
    # Wagner-Fischer, abort when the row exceeds limit.
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        min_row = i
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (ca != cb)
            val = min(ins, delete, sub)
            cur.append(val)
            if val < min_row:
                min_row = val
        if min_row > limit:
            return False
        prev = cur
    return prev[-1] <= limit


_ALICE_FUZZY_BASES = ("alice", "alicia")
_ALICE_FUZZY_STOP = frozenset({
    "slice", "alive", "alike", "malice", "police", "spice",
})
_ALICE_TOKEN_RE = re.compile(r"(?<![a-z0-9])[a-z][a-z0-9]{3,7}(?![a-z0-9])", re.I)


def _fuzzy_alice_name(blob: str) -> bool:
    """Edit-distance 1 from Alice/Alicia, excluding common-word false hits.

    Only used together with a Wonderland or Carroll cue, never for a bare
    name in sexual context.
    """
    for token in _ALICE_TOKEN_RE.findall(blob or ""):
        word = token.lower()
        if word in _ALICE_FUZZY_STOP:
            continue
        if any(_edit_distance_at_most(word, base, 1) for base in _ALICE_FUZZY_BASES):
            return True
    return False


def _strip_allow(pattern: re.Pattern, text: str) -> str:
    return pattern.sub(" ", text or "")


_SPICY_FOOD_RE = re.compile(
    r"(?i)spicy\s+(?:chai|nuggets|pizza|wings|taco|sauce|soup|"
    r"ramen|curry|pepper|fries|food|dish|beans)"
)
_INNUENDO_NEAR_RE = re.compile(
    r"(?i)\(\s*wink\s*\)|😉|🫦|🍒|💋|🔞|🥴|👉👌"
)
_INNUENDO_RE = re.compile(
    r"(?i)(?:👉👌|"
    r"corn\s*\(\s*not\s+food|"
    r"(?<![\d\w])69(?![\d]|[\s-]*(?:years?|yrs?|yo|y\s*/\s*o))|"
    r"(?<![\w])breed(?:s|ing|\s+me)?(?!\s+of)|"
    r"\*moans\*|"
    r"d0\s+n0t\s+st0p|"
    r"pl@y\s+with\s+me)"
)


def _strip_spicy_food(text: str) -> str:
    """Drop food uses of spicy, but not innuendo such as '(wink)' nearby."""
    def repl(m):
        start, end = m.span()
        window = (text or "")[max(0, start - 24):end + 24]
        if _INNUENDO_NEAR_RE.search(window):
            return m.group(0)
        return " "
    return _SPICY_FOOD_RE.sub(repl, text or "")


def _has_innuendo(candidates) -> bool:
    return any(_INNUENDO_RE.search(item or "") or _INNUENDO_NEAR_RE.search(item or "") for item in candidates)


def _sexual_or_rp(candidates, force_context: bool = False) -> bool:
    """Sexual or RP intent, ignoring plain food uses of 'spicy'."""
    if force_context:
        return True
    cleaned = [_strip_spicy_food(item or "") for item in candidates]
    return (
        _has_sexual(cleaned)
        or _has_innuendo(candidates)
        or _has_innuendo(cleaned)
        or _has_character(cleaned)
        or _has_media_rp(candidates)
    )


def _flex_re(pattern: re.Pattern) -> re.Pattern:
    """Same pattern, but spaces in the text may have been removed."""
    flexed = re.sub(r"\\s\+", r"\\s*", pattern.pattern)
    return re.compile(flexed, pattern.flags)


_STORYBOOK_FLEX_RE = _flex_re(_STORYBOOK_RE)
_STORYBOOK_CONTEXT_FLEX_RE = _flex_re(_STORYBOOK_CONTEXT_RE)
_WONDERLAND_FLEX_RE = _flex_re(_WONDERLAND_RE)
_ALICE_CUES_FLEX_RE = _flex_re(_ALICE_CUES_RE)
_ALICE_NAME_FLEX_RE = _flex_re(_ALICE_NAME_RE)
_ALICE_BARE_FLEX_RE = _flex_re(_ALICE_BARE_RE)
_ALICE_CUED_FLEX_RE = _flex_re(_ALICE_CUED_NAMES_RE)


def _search_flex(regex: re.Pattern, flex: re.Pattern, text: str):
    if not text:
        return None
    hit = regex.search(text)
    if hit:
        return hit
    compact = re.sub(r"\s+", "", text)
    if compact and compact != text:
        return flex.search(compact)
    return None


_STORYBOOK_FUZZY_BASES = (
    "goldilocks", "gretel", "heidi", "pippi", "matilda",
    "mathilda", "matilde", "matylda", "coraline",
    "mowgli", "pinocchio", "pevensie", "narnia", "quimby",
    "wormwood", "beauregarde", "ingalls",
)
_STORYBOOK_CONTEXT_FUZZY_BASES = (
    "dorothy",
)
_STORYBOOK_FUZZY_STOP = frozenset({
    "windy", "bendy", "weedy", "trendy", "newly",
    "peppy", "poppy", "hippy", "hippi", "pippa",
    "grater", "gentle", "greece",
    "caroline", "corinne", "corinna", "coreline",
    "media", "ready", "heady",
    "peters", "meter", "better",
})
_STORYBOOK_FUZZY_TOKEN_RE = re.compile(r"(?<![a-z0-9])[a-z][a-z0-9]{4,}(?![a-z0-9])", re.I)


def _fuzzy_storybook_name_in(blob: str, bases) -> str:
    """Edit-distance 1 from the given storybook names, tokens of 5+ letters."""
    for token in _STORYBOOK_FUZZY_TOKEN_RE.findall(blob or ""):
        word = token.lower()
        if word in _STORYBOOK_FUZZY_STOP:
            continue
        for base in bases:
            if _edit_distance_at_most(word, base, 1):
                return token
    return ""


def _fuzzy_storybook_name(blob: str) -> str:
    """Edit-distance 1 from a storybook name, tokens of 5+ letters, with a stoplist."""
    return _fuzzy_storybook_name_in(blob, _STORYBOOK_FUZZY_BASES)


_ALLOW_MAX_REMAINDER_WORDS = 6
_BODY_CUE_RE = re.compile(
    r"(?i)(?<![\w])(?:bod(?:y|ies)|figure|waist|chest|breasts?|hips?|"
    r"thighs?|skin|lips?|cleavage|nude|naked)(?![\w])"
)
_IMEAN_RE = re.compile(r"(?i)\bi\s+mean\b|\bthe\s+\w+\s+one\b")
_CHILD_CUE_RE = re.compile(
    r"(?i)(?<![\w])(?:kids?|child(?:ren)?|minors?|boy|girl|infant|toddler|"
    r"bab(?:y|ies)|sisters?|brothers?|alpine|bathtime)(?![\w])"
)


def _remainder_word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or "", flags=re.UNICODE))


def _allow_list_ok(original: str, remainder: str, candidates) -> bool:
    """Allow-list only when the rest of the message is short and otherwise benign."""
    inspect = [original or "", remainder or "", *(candidates or [])]
    if _sexual_or_rp(inspect):
        return False
    if _underage_age_hit(inspect):
        return False
    if any(_CHILD_CUE_RE.search(x or "") for x in inspect):
        return False
    if any(_BODY_CUE_RE.search(x or "") for x in inspect):
        return False
    if any(_IMEAN_RE.search(x or "") for x in inspect):
        return False
    if _remainder_word_count(remainder) > _ALLOW_MAX_REMAINDER_WORDS:
        return False
    return True


def _story_texts(candidate: str, *, allow_ok: bool) -> list[str]:
    """Texts to run the storybook / cue check on.

    Allow-list phrases are applied only when the remainder is short and
    has no sexual, age, or child cue. Otherwise the original text is
    always checked. Remainder and whitespace-collapsed remainder are
    still searched so an allowed phrase cannot split a name.
    """
    remainder = _strip_allow(_STORYBOOK_ALLOW_RE, candidate)
    compact = re.sub(r"\s+", "", remainder)
    if allow_ok:
        out = [remainder]
    else:
        out = [candidate, remainder]
    if compact and compact not in out:
        out.append(compact)
    return out


def _alice_texts(candidate: str, *, allow_ok: bool) -> list[str]:
    remainder = _strip_allow(_ALICE_ALLOW_RE, candidate)
    compact = re.sub(r"\s+", "", remainder)
    if allow_ok:
        out = [remainder]
    else:
        out = [candidate, remainder]
    if compact and compact not in out:
        out.append(compact)
    return out


def match_info(text: str, *, force_context: bool = False) -> tuple[str, str]:
    """(term, check) for the first hit, or ("", "") when the text is fine.

    check is one of: always, age, context, extra.
    An adult age never overrides an under-18 signal in the same text.
    """
    raw = normalize(text)
    if not raw.strip():
        return "", ""
    candidates = _variants(text)
    # Check the raw string too so combining marks in Hindi/Arabic/etc. stay.
    if _native_term_hit(text):
        return _native_term_hit(text), "always"
    if _EMOJI_CHILD_RE.search(text or "") or any(_EMOJI_CHILD_RE.search(c) for c in candidates):
        return "child-emoji", "always"
    for candidate in candidates:
        for term in _CJK_TERMS:
            if term in candidate:
                return term, "always"
        hit = _BLOCKED_RE.search(_masked_for_always(candidate))
        if hit:
            # "a white rabbit for my kids' pet? no, my wife's" is a denial.
            if hit.group(0).lower() in {"kid", "kids"} and re.search(
                r"kids['’]?\s+pet\s*\?\s*no\b", candidate, re.I
            ):
                continue
            return hit.group(0), "always"
    age = _underage_age_hit(candidates)
    if age:
        return age, "age"
    # baby is context-only (sexual). Keep the historical check name via context.
    if _has_sexual(candidates):
        for candidate in candidates:
            baby = _BABY_RE.search(candidate)
            if baby:
                # "kissed the baby? no, kissed his wife" is a denial, not a request.
                if re.search(r"baby\s*\?\s*no\b", candidate, re.I):
                    continue
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
    # Alice in a story context (Wonderland, Carroll, tea party, …),
    # including cues that span lines. Allow-lists are not applied when
    # the message has sexual or RP wording; the original text is checked.
    sexual = _sexual_or_rp(candidates, force_context)
    alice_sources = list(candidates) + [text or ""]
    story_remainder = _strip_allow(_STORYBOOK_ALLOW_RE, text or "")
    alice_remainder = _strip_allow(_ALICE_ALLOW_RE, text or "")
    story_allow_ok = _allow_list_ok(text or "", story_remainder, candidates)
    alice_allow_ok = _allow_list_ok(text or "", alice_remainder, candidates)
    alice_checked: list[str] = []
    for candidate in alice_sources:
        alice_checked.extend(_alice_texts(candidate, allow_ok=alice_allow_ok))
    def _alice_name_hit(blob: str) -> bool:
        return bool(
            _search_flex(_ALICE_NAME_RE, _ALICE_NAME_FLEX_RE, blob)
            or _search_flex(_ALICE_CUED_NAMES_RE, _ALICE_CUED_FLEX_RE, blob)
            or _fuzzy_alice_name(blob)
        )
    def _alice_cue_hit(blob: str) -> bool:
        return bool(
            _search_flex(_ALICE_CUES_RE, _ALICE_CUES_FLEX_RE, blob)
            or _search_flex(_WONDERLAND_RE, _WONDERLAND_FLEX_RE, blob)
        )
    if any(_alice_name_hit(blob) and _alice_cue_hit(blob) for blob in alice_checked):
        return "alice-story", "always"
    if sexual:
        # Fuzzy matching is cue-gated only (above). Bare sexual context
        # uses the exact name list on the original text, never an allow-list.
        if any(
            _search_flex(_ALICE_BARE_RE, _ALICE_BARE_FLEX_RE, blob)
            for blob in alice_checked
        ):
            return "alice", "context"
        if any(_WENDY_SEXUAL_RE.search(c) for c in alice_sources):
            return "wendy", "context"
        if any(
            re.search(r"(?i)(?<![\w])(?:dorothy|dottie)(?![\w])", c or "")
            or _fuzzy_storybook_name_in(c, _STORYBOOK_CONTEXT_FUZZY_BASES)
            for c in alice_sources
        ):
            return "dorothy", "context"
        if any(
            re.search(
                r"(?i)(?<![\w])(?:alis|aliss|alise|ailce|aliec|alcie|alisu)(?![\w])",
                c or "",
            )
            for c in alice_checked
        ):
            return "alice", "context"
    for candidate in list(candidates) + [text or ""]:
        hit = _HOGWARTS_FIRST_YEAR_RE.search(candidate) or _AGE_TAGGED_CANON_RE.search(candidate)
        if hit:
            return hit.group(0), "always"
    # Named fictional minors are blocked by default. Allow-list phrases
    # are word-bounded and applied only when there is no sexual or RP
    # wording. Remainder is also checked with spaces removed.
    story_blob = list(candidates) + [text or ""]
    for candidate in story_blob:
        for snippet in _story_texts(candidate, allow_ok=story_allow_ok):
            hit = _search_flex(_STORYBOOK_RE, _STORYBOOK_FLEX_RE, snippet)
            if hit:
                return hit.group(0), "always"
            fuzzy = _fuzzy_storybook_name(snippet)
            if fuzzy:
                return fuzzy, "always"
    # Short Red Riding Hood forms, and Wonderland / Looking-Glass /
    # Carroll / Alice cues, plus sexual or RP wording — even without a name.
    if sexual:
        for candidate in story_blob:
            for snippet in _story_texts(candidate, allow_ok=False):
                hit = _search_flex(_STORYBOOK_CONTEXT_RE, _STORYBOOK_CONTEXT_FLEX_RE, snippet)
                if hit:
                    return hit.group(0), "context"
        for candidate in alice_sources:
            for snippet in _alice_texts(candidate, allow_ok=False):
                if _search_flex(_WONDERLAND_RE, _WONDERLAND_FLEX_RE, snippet):
                    # Standalone Wonderland / Carroll / listed Alice cues
                    # plus sexual or RP. Do not use the full Alice-cue list
                    # (disney / kingdom hearts are too broad without a name).
                    return "wonderland", "context"
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


def _drop_blocked_lines(text: str) -> tuple[str, bool]:
    """Remove line-level matcher hits from an inlined history blob."""
    lines = (text or "").splitlines()
    kept: list[str] = []
    dropped = False
    for line in lines:
        if match_info(line)[1]:
            dropped = True
            continue
        kept.append(line)
    cleaned = "\n".join(kept)
    # If the whole remaining blob still matches (no clean line split), drop it.
    if cleaned.strip() and match_info(cleaned)[1]:
        return "", True
    return cleaned, dropped


def _is_hard_message(role: str, text: str, index: int, last_user: int) -> bool:
    role_l = (role or "").lower()
    return (
        index == last_user
        or _looks_like_persona_or_card(text, role)
        or (role_l == "system" and "persona" in (text or "").lower())
    )


def sanitize_messages(messages) -> tuple[list, str]:
    """Return (messages to send, refuse reason).

    Latest turn, persona, and character cards are hard checks. Hits in
    older history are dropped from the request instead of locking the
    channel. The hardcoded safety footer is left untouched.
    """
    from core.safety import is_footer_message, is_user_guard, message_text

    original = list(messages or [])
    rows = []
    for i, message in enumerate(original):
        if is_footer_message(message) or is_user_guard(message):
            rows.append(("keep", i, message, "", ""))
            continue
        role = (message.get("role") if isinstance(message, dict) else "") or ""
        text = message_text(message) if isinstance(message, dict) else str(message or "")
        rows.append(("scan", i, message, role, text))

    scan_indices = [n for n, row in enumerate(rows) if row[0] == "scan"]
    last_user_row = -1
    for n in scan_indices:
        role = rows[n][3]
        if role.lower() == "user":
            last_user_row = n

    cleaned: list = []
    quarantined = False
    for n, (kind, _i, message, role, text) in enumerate(rows):
        if kind == "keep":
            cleaned.append(message)
            continue
        if not str(text).strip():
            cleaned.append(message)
            continue
        history, latest = split_transcript(text)
        if latest is not None:
            _term, check = match_info(latest)
            if check:
                return original, f"llm-input-{check}"
            new_history, dropped = _drop_blocked_lines(history)
            if dropped:
                note("llm-history-quarantine")
                quarantined = True
            if isinstance(message, dict):
                marker = "=== MOST RECENT MESSAGE ==="
                rebuilt = (new_history.rstrip() + "\n" + marker + "\n" + latest.lstrip()).strip()
                message = {**message, "content": rebuilt}
            cleaned.append(message)
            continue
        _term, check = match_info(text)
        if not check:
            cleaned.append(message)
            continue
        if _is_hard_message(role, text, n, last_user_row):
            return original, f"llm-input-{check}"
        note("llm-history-quarantine")
        quarantined = True
        # Drop the offending history message from the outgoing request.
    return cleaned, ""


def scan_messages(messages) -> str:
    """Refuse reason if the latest/persona hit; also used after sanitize."""
    _cleaned, reason = sanitize_messages(messages)
    return reason


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
