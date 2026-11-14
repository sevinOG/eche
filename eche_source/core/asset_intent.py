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
    folded = _clean(text).casefold()
    # f-35 and f35 are the same word.
    folded = re.sub(r"(?<=\w)[-'’](?=\w)", "", folded)
    out: set[str] = set()
    for word in re.findall(r"[^\W_]+", folded):
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


def decline_reason(prompt: str, message_text: str) -> str:
    """'' when the tool may run, else a short fixed reason for the audit log."""
    text = _clean(message_text)
    if not has_request_wording(text):
        return DECLINE_NO_REQUEST
    if is_retry(text) or _OPEN_RE.search(text):
        return ""
    if not (prompt or "").strip():
        return ""
    if not shares_meaningful_word(prompt, text):
        return DECLINE_NO_OVERLAP
    return ""


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
# did not write are removed. A prompt the minors matcher or the media filter
# already blocks is left unchanged by the caller, so stripping never turns a
# blocked prompt into an allowed one.
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
