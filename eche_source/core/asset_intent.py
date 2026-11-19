# core/asset_intent.py
# Does this message itself ask for a picture, a video, or an edit?
#
# The chat model sometimes called the asset tool on a reaction ("what the
# heck man") right after a block, with a prompt it made up. The tool only
# runs when the current message has request wording, and the tool prompt
# shares a meaningful word with that message, unless the message is a plain
# retry ("again", "redo", "try again") of the earlier request.

from __future__ import annotations

import re

_MENTION_RE = re.compile(r"<[@#][!&]?\d+>|<a?:\w+:\d+>")
_URL_RE = re.compile(r"https?://\S+", re.I)

# Words that ask for something to be made or changed.
_REQUEST_RE = re.compile(
    r"(?i)\b(?:make|makes|making|made|generate\w*|gen|create\w*|draw\w*|render\w*|"
    r"paint\w*|sketch\w*|design\w*|illustrat\w*|imagine|edit\w*|change\w*|turn|turns|"
    r"give|gimme|add|adds|remove\w*|put|replace\w*|swap\w*|convert\w*|transform\w*|"
    r"anthro\w*|anthromorph\w*|morph\w*|animate\w*|recolou?r\w*|colou?r|zoom\w*|crop\w*|"
    r"picture\w*|pics?|images?|img|photo\w*|video\w*|vids?|clip|gif|selfie|portrait|"
    r"wallpaper|meme|redo|retry|again|another|bigger|smaller|larger|longer|shorter|"
    r"darker|brighter|without|instead|version|variation|show\s+me|one\s+more|do\s+(?:it|one|that)|"
    r"try\s+(?:it\s+)?again)\b"
)
# A plain retry of the earlier request; the tool prompt may then reuse it.
_RETRY_RE = re.compile(
    r"(?i)\b(?:again|redo|re-do|retry|one\s+more|another\s+(?:one|try|go)|try\s+(?:it\s+)?again)\b"
)

_STOP = frozenset(
    """
    a an the and or but if of to in on at by for with from into onto over under as is are was were be been
    being it its it's this that these those there here then than so very too just also only even
    i me my mine you your yours he him his she her hers they them their we us our
    can could would should will shall may might must do does did doing done have has had having
    please pls plz now make made making generate create draw give gimme add put turn change edit
    picture pic pics image images photo video vid clip one some any more less much many really
    what who whom which when where why how not no yes ok okay eche bot like get got want wanna
    """.split()
)


def _clean(text: str) -> str:
    text = _MENTION_RE.sub(" ", str(text or ""))
    return _URL_RE.sub(" ", text)


def _words(text: str) -> set[str]:
    cleaned = _clean(text).casefold()
    # f-35 and f35 are the same word. The hyphen's parts count too, so
    # "t-shirt" and "t shirt" share "shirt" (live 2026-11-19 09:54 ET).
    folded = re.sub(r"(?<=\w)[-'’](?=\w)", "", cleaned)
    split = re.sub(r"(?<=\w)-(?=\w)", " ", cleaned)
    out: set[str] = set()
    for word in re.findall(r"[^\W_]+", folded) + re.findall(r"[^\W_]+", split):
        if len(word) < 3 and not any(ch.isdigit() for ch in word):
            continue
        if word in _STOP:
            continue
        out.add(_stem(word))
    return out


def _stem(word: str) -> str:
    for suffix in ("izing", "izes", "ized", "ize", "ing", "ies", "es", "ed", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def has_request_wording(text: str) -> bool:
    return bool(_REQUEST_RE.search(_clean(text)))


def is_retry(text: str) -> bool:
    return bool(_RETRY_RE.search(_clean(text)))


def shares_meaningful_word(prompt: str, text: str) -> bool:
    return bool(_words(prompt) & _words(text))


# An open request leaves the subject to Eche, so the prompt need not overlap.
_OPEN_RE = re.compile(
    r"(?i)\b(?:something|anything|surprise\s+me|whatever|random|your\s+choice|you\s+pick|up\s+to\s+you|"
    # A plain go-ahead that leaves the subject to Eche or the chat context
    # (live 10:01 ET: "just do it based off my context" was declined).
    r"(?:just\s+)?do\s+it|go\s+ahead|you\s+decide|"
    r"(?:based|base\s+it)\s+(?:on|off(?:\s+of)?)\s+(?:my|our|the|this)\s+(?:context|chat|convo|conversation|messages?))\b"
)


DECLINE_NO_REQUEST = "no-request-wording"
DECLINE_NO_OVERLAP = "prompt-not-from-message"
DECLINE_ASKS_ABOUT = "asks-about-media"

# Live 2026-11-18 23:05 ET: "Describe the video" made a new video. A message
# that asks Eche to look at, describe or explain media is not a request to
# make something, unless it also uses a make/edit verb.
_ABOUT_RE = re.compile(
    r"(?i)\b(?:describe\w*|explain\w*|summari[sz]e\w*|identify|recogni[sz]e|analy[sz]e\w*|"
    r"what(?:'?s|\s+is|\s+are|\s+was|\s+happens|\s+happening)\b[^.?!]*\b(?:in|on|about|this|that|the)\b|"
    r"what\s+do\s+you\s+see|can\s+you\s+see|do\s+you\s+see|look\s+at|watch\s+(?:this|that|the))"
)
_MAKE_VERB_RE = re.compile(
    r"(?i)\b(?:make|makes|making|generate\w*|gen|create\w*|draw\w*|render\w*|paint\w*|sketch\w*|"
    r"imagine|edit\w*|change\w*|turn|add|remove\w*|put|replace\w*|swap\w*|convert\w*|transform\w*|"
    r"animate\w*|recolou?r\w*|gimme|redo|retry)\b"
)


def asks_about_media(text: str) -> bool:
    """True for "describe/explain/what's in the video" with no make/edit verb."""
    clean = _clean(text)
    return bool(_ABOUT_RE.search(clean)) and not _MAKE_VERB_RE.search(clean)


def decline_reason(prompt: str, message_text: str, context_text: str = "") -> str:
    """'' when the tool may run, else a short fixed reason for the audit log.

    context_text is the recent conversation the message follows: the
    replied-to message and Eche's and this user's recent turns in the
    channel. A short request such as "make the shirt" that follows Eche's own
    offer passes when the tool prompt overlaps that context (live 2026-11-19
    09:54 / 09:55 ET: "Generate the t shirt" and "Make the shirt man" were
    declined). A prompt that overlaps neither the message nor the context is
    still declined. The message itself must still ask for something.
    """
    text = _clean(message_text)
    if not has_request_wording(text):
        return DECLINE_NO_REQUEST
    if asks_about_media(text):
        return DECLINE_ASKS_ABOUT
    if is_retry(text) or _OPEN_RE.search(text):
        return ""
    if not (prompt or "").strip():
        return ""
    if shares_meaningful_word(prompt, text):
        return ""
    if context_text and shares_meaningful_word(prompt, context_text):
        return ""
    return DECLINE_NO_OVERLAP


# Youthful styles the chat model may add on its own. Only these are stripped,
# and only when the user did not write them; other style words stay.
_ADDED_STYLE_RE = re.compile(r"(?i)\b(?:anime|manga|chibi|kawaii|moe)(?:[-\s]+(?:style|styled|art|aesthetic))?\b,?\s*")


def strip_added_youthful_styles(prompt: str, user_text: str) -> str:
    """Drop anime/chibi style words the user did not write. The checks still run."""
    said = _clean(user_text).casefold()

    def _keep(match: re.Match) -> str:
        word = re.match(r"[a-z]+", match.group(0).casefold())
        if word and word.group(0) in said:
            return match.group(0)
        return ""

    out = _ADDED_STYLE_RE.sub(_keep, str(prompt or ""))
    return " ".join(out.replace(" ,", ",").split()).strip(" ,")


# School cues and youth-emphasis phrases the chat model adds on its own
# (owner-approved 2026-11-14 10:34 ET, after a model-written prompt at 10:01 ET
# added "young adult", a school jacket and "at school"). Only phrases the user
# did not write are removed. A prompt the minors matcher already blocks is
# left unchanged by the caller, so stripping never turns a blocked prompt into
# an allowed one.
_ADDED_CUES = (
    ("youth", re.compile(
        r"(?i)\b(?:young[\s-]+adult|barely[\s-]+(?:legal|18|eighteen)|just[\s-]+(?:turned[\s-]+)?(?:18|eighteen)|"
        r"fresh(?:ly)?[\s-]+(?:18|eighteen)|newly[\s-]+(?:18|eighteen)|youthful(?:[\s-]+looking)?|"
        r"young[\s-]+looking|young-?ish|young(?=[\s-]+(?:woman|women|man|men|lady|ladies|female|male|person|"
        r"people|adult|guy|gal|girl|boy)s?\b))\b,?\s*")),
    ("school", re.compile(
        r"(?i)\b(?:at|in|after|before|to|from|for)\s+(?:(?:high|middle|grade|boarding|prep|elementary)\s+)?school\b,?\s*")),
    ("school", re.compile(r"(?i)\b(?:high|middle|grade|boarding|prep|elementary)[\s-]+school(?:ers?)?\b,?\s*")),
    ("school", re.compile(
        r"(?i)\bschool[\s-]+(?=(?:jacket|blazer|skirt|backpack|bag|tie|shirt|sweater|cardigan|hoodie|shoes|socks|"
        r"bus|desk|hallway|hall|locker|gym|days?)s?\b)")),
    ("school", re.compile(
        r"(?i)\b(?:(?:in|at|inside|into|to|from)\s+(?:a|an|the|her|his|their|my)\s+)?(?:class\s*rooms?|home\s*room|hall\s*pass(?:es)?|class\s*mates?|detention|recess|"
        r"freshm[ae]n|sophomores?|students?|pupils?)\b,?\s*")),
    ("school", re.compile(r"(?i)(?<!old\s)(?<!old-)\bschool(?:s|yard|house|work)?\b(?!\s+of\s+(?:fish|thought)),?\s*")),
)


def strip_added_school_and_youth_cues(prompt: str, user_text: str) -> tuple[str, list[str]]:
    """Drop school cues and youth-emphasis phrases the user did not write.

    Returns (prompt, removed phrases). The checks still run on the result.
    """
    said = " ".join(_clean(user_text).casefold().split())
    removed: list[str] = []

    def _drop(match: re.Match) -> str:
        phrase = " ".join(match.group(0).strip(" ,").casefold().split())
        if phrase and phrase in said:
            return match.group(0)
        removed.append(phrase)
        return " "

    out = str(prompt or "")
    for _name, rx in _ADDED_CUES:
        out = rx.sub(_drop, out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", " ".join(out.split()))
    out = re.sub(r"(?:,\s*){2,}", ", ", out).strip(" ,")
    return out, removed


# Owner-approved 2026-11-19 00:29 ET: "girl"/"girls" in an asset prompt
# ("goth girls") means adult women. It is rewritten to "woman"/"women" before
# the checks, in every channel, unless the text has any youth or school cue;
# then nothing is rewritten and the existing minors block applies. Every
# other check still runs on the rewritten text.
GIRL_REWRITE_RULE = "girl-to-woman"
_GIRL_WORD = re.compile(
    r"(?<![\w'’-])(girl)(s)?(?=['’]s\b|\b)(?![-\w])(?!['’]?s?\s+(?:nights?|scouts?)\b)", re.I
)
_YOUTH_CUE = re.compile(
    r"(?i)(?:\b(?:teen\w*|tween\w*|preteen\w*|school\w*|uniform\w*|student\w*|pupil\w*|class\s*mates?|"
    r"petite|child\w*|kid\w*|kiddo|young\w*|youth\w*|loli\w*|shota\w*|little|lil|tiny|small|smol|baby|babies|"
    r"under\s*-?\s*age\w*|minors?|jail\s*bait|innocent|barely|daughter\w*|niece\w*|sister\w*|"
    r"juvenile|adolescen\w*|pubescent|prepubescent|junior|chibi|kawaii|moe|tot|toddler\w*|infant\w*|"
    r"grade\s*\w+|freshm[ae]n|sophomore\w*|girlish|innocence|cute|cutie\w*)\b"
    r"|\b(?:1[0-7]|[1-9])\s*(?:yo|y/o|yrs?|years?)\b|\b(?:1[0-7]|[1-9])\b)"
)


def has_youth_cue(text: str) -> bool:
    return bool(_YOUTH_CUE.search(_clean(text or "")))


def rewrite_adult_girls(text: str) -> tuple[str, bool]:
    """('goth women', True) for 'goth girls'. Unchanged when any youth cue is present."""
    raw = str(text or "")
    if not _GIRL_WORD.search(raw) or has_youth_cue(raw):
        return raw, False

    def _swap(m: re.Match) -> str:
        word = "women" if m.group(2) else "woman"
        if m.group(1).isupper():
            return word.upper()
        if m.group(1)[0].isupper():
            return word.capitalize()
        return word

    out = _GIRL_WORD.sub(_swap, raw)
    return out, out != raw


# ---------------------------------------------------------------------------
# Negated minor exclusions the chat model appends to its own asset prompt
# (live 2026-11-19 10:24 ET: a long, clearly adult prompt ended with safety
# boilerplate such as "no children" and was refused as llm-output).
#
# Only a whole clause that is nothing but the exclusion is removed: the
# negation must open the clause and govern the youth word directly, with
# only a few fixed filler words around it ("no children", "without any
# minors", "children not present", "no one under 18", "not for children",
# "all characters are adults, no kids"). Anything else stays and is checked
# as written ("no adults, only children", "children with no parents",
# "no clothes children", "no children's toys"). If the text still has any
# minors-list hit after the strip, nothing is removed, so the original is
# blocked and logged. Model-written prompts only; never the user's text.
# ---------------------------------------------------------------------------
_YOUTH_NOUN = (
    r"(?:children|child|kids|kid|kiddos?|minors|minor|teens|teen|teenagers|teenager|"
    r"underage(?:\s+(?:people|persons|characters|individuals|subjects|figures|anyone))?|"
    r"young\s+children|little\s+ones|youngsters?)"
)
_YOUTH_LIST = _YOUTH_NOUN + r"(?:\s*(?:,|/|\bor\b|\band\b|\bnor\b)\s*(?:or\s+|and\s+|nor\s+)?" + _YOUTH_NOUN + r")*"
_EXCL_TAIL = (
    r"(?:\s+(?:are\s+|is\s+|being\s+)?(?:present|shown|depicted|included|allowed|pictured|visible|involved|"
    r"in\s+(?:the\s+)?(?:scene|image|picture|frame|shot|background|artwork|illustration|video)|"
    r"anywhere|at\s+all|whatsoever|of\s+any\s+kind))*"
)
_LEAD = r"(?:(?:and|but|with|also|strictly|absolutely|definitely|important|note|please)\s*[:\-]?\s+)*"
_NEGATED_EXCLUSION_RES = (
    # no children / without any minors / zero kids / excluding teens / free of children
    re.compile(
        r"(?is)^\s*" + _LEAD
        + r"(?:no|zero|without|never|excluding|exclude|free\s+of|not\s+any|no\s+depictions?\s+of)\s+"
        r"(?:any\s+|other\s+|real\s+|actual\s+|visible\s+)?" + _YOUTH_LIST + _EXCL_TAIL + r"\s*$"
    ),
    # children not present / kids are not shown / minors are not depicted
    re.compile(
        r"(?is)^\s*" + _LEAD + r"(?:the\s+)?" + _YOUTH_LIST
        + r"\s+(?:are\s+|is\s+)?(?:not|never)\s+(?:present|shown|depicted|included|allowed|pictured|visible|involved)"
        + _EXCL_TAIL + r"\s*$"
    ),
    # not for children / not suitable for kids
    re.compile(
        r"(?is)^\s*" + _LEAD + r"(?:this\s+is\s+|it\s+is\s+)?not\s+(?:suitable\s+|intended\s+|meant\s+|appropriate\s+)?"
        r"for\s+" + _YOUTH_LIST + r"\s*$"
    ),
    # no one under 18 / nobody below the age of 18 / no characters under eighteen
    re.compile(
        r"(?is)^\s*" + _LEAD
        + r"(?:no\s*(?:one|body)|nobody|no\s+(?:people|persons|person|characters?|figures?|subjects?|individuals?))\s+"
        r"(?:who\s+(?:is|are)\s+|aged\s+)?(?:under|below|younger\s+than)\s+(?:the\s+age\s+of\s+)?(?:18|eighteen)"
        r"(?:\s+(?:years?\s+old|present|shown|depicted|included))?\s*$"
    ),
)
# Clause boundaries. Commas are handled inside a clause, below.
_CLAUSE_SPLIT_RE = re.compile(r"([.;:!?\n()\[\]{}]|\s[\u2013\u2014-]\s|\u2014)")
# A comma segment that only continues a negated list ("no kids, teens, or minors").
_LIST_TAIL_RE = re.compile(r"(?is)^\s*(?:or|and|nor)?\s*" + _YOUTH_NOUN + _EXCL_TAIL + r"\s*$")
NEGATED_EXCLUSION_RULE = "negated-minor-exclusion"


def _is_negated_exclusion(segment: str) -> bool:
    return any(rx.match(segment or "") for rx in _NEGATED_EXCLUSION_RES)


def _strip_clause(clause: str, removed: list[str]) -> str:
    if clause.strip() and _is_negated_exclusion(clause):
        removed.append(" ".join(clause.split()))
        return ""
    parts = clause.split(",")
    kept: list[str] = []
    in_list = False
    for part in parts:
        if part.strip() and _is_negated_exclusion(part):
            removed.append(" ".join(part.split()))
            in_list = True
            continue
        if in_list and part.strip() and _LIST_TAIL_RE.match(part):
            removed.append(" ".join(part.split()))
            continue
        in_list = False
        kept.append(part)
    return ",".join(kept)


def strip_negated_minor_exclusions(text: str) -> tuple[str, list[str]]:
    """(text, removed clauses) with model-added "no children"-style exclusions removed.

    Returns the text unchanged with [] when nothing qualifies, when the
    result would be empty, or when the result still has a minors-list hit
    (any non-negated youth term elsewhere keeps the whole prompt blocked).
    """
    raw = str(text or "")
    if not raw.strip():
        return raw, []
    removed: list[str] = []
    pieces = _CLAUSE_SPLIT_RE.split(raw)
    out_pieces = [
        piece if i % 2 else _strip_clause(piece, removed)
        for i, piece in enumerate(pieces)
    ]
    if not removed:
        return raw, []
    out = "".join(out_pieces)
    # Tidy what the removal left behind: empty brackets, doubled punctuation.
    out = re.sub(r"\(\s*[,.;:]*\s*\)|\[\s*[,.;:]*\s*\]", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", out)
    out = re.sub(r"([.!?])(?:\s*[,;:.])+", r"\1", out)
    out = re.sub(r"[,;:]\s*([.!?])", r"\1", out)
    out = " ".join(out.split()).strip(" ,;:")
    if not out:
        return raw, []
    try:
        from core.blocklist import match

        if match(out):
            return raw, []
    except Exception:
        return raw, []
    return out, removed


def strip_model_exclusions_logged(text: str, *, where: str, ids: dict | None = None) -> str:
    """strip_negated_minor_exclusions plus one audit row when something was removed."""
    out, removed = strip_negated_minor_exclusions(text)
    if removed:
        try:
            from core.audit import record

            record("asset-strip", source=where, rule=NEGATED_EXCLUSION_RULE,
                   removed=" | ".join(removed)[:300], safety=True, **(ids or {}))
        except Exception:
            pass
    return out
