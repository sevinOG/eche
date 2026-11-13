# core/guardrails.py
# Help Eche not coach anyone past a filter, not rewrite a prompt so it
# passes one, and not keep those preferences in memory.
#
# The minors block itself lives in core/blocklist.py. Adult crude or sexual
# content is still allowed. These checks are about process, not new topics.

from __future__ import annotations

import re

BYPASS_REFUSAL = "I can't help get around the filter."

# Requests to beat, pass, or plan around a filter or moderation system.
_BYPASS_RE = re.compile(
    r"(?:"
    r"get(?:ting)?\s+(?:it|this|us|that|them\s+)?(?:past|around|through)\s+(?:the\s+)?(?:moderation\s+)?filters?"
    r"|pass(?:ing)?\s+(?:the\s+)?(?:moderation\s+)?filters?"
    r"|beat(?:ing)?\s+(?:the\s+)?(?:moderation\s+)?filters?"
    r"|(?:circumvent|bypass|dodge|evade)(?:ing)?\s+(?:the\s+)?(?:moderation\s+)?filters?"
    r"|around\s+(?:the\s+)?(?:moderation\s+)?filters?"
    r"|come\s+up\s+with\s+a\s+plan.{0,80}filters?"
    r"|plan\s+to\s+get\s+(?:us|me|this|it).{0,60}filters?"
    r"|rewrite.{0,60}(?:pass|filters?|moderation)"
    r"|prompt\s+rewrite.{0,40}filters?"
    r"|filter\s+fight"
    r"|fighting\s+(?:a\s+|the\s+)?moderation"
    r"|to\s+pass\s+moderation(?:\s+filters?)?"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_FILTER_WORD_RE = re.compile(r"\b(?:filters?|moderation)\b", re.IGNORECASE)
_PLAN_RE = re.compile(
    r"come\s+up\s+with\s+a\s+plan"
    r"|get\s+(?:us|me)\s+this"
    r"|get\s+(?:it|this)\s+through"
    r"|how\s+do\s+(?:i|we)\s+(?:get|make)"
    r"|make\s+it\s+(?:go|get)\s+through",
    re.IGNORECASE,
)

# Wording Eche has used when teaching someone to beat a filter.
_COACH_RE = re.compile(
    r"filter's\s+catnip"
    r"|here'?s\s+the\s+play"
    r"|those\s+words\s+are\s+the\s+filter"
    r"|makes?\s+the\s+filter\s+go"
    r"|prompt\s+rewrite\s+clearing"
    r"|bet\s+on\s+the\s+prompt\s+rewrite"
    r"|filter\s+fight"
    r"|kill\s+the\s+body\s+language"
    r"|context\s+makes\s+the\s+filter"
    r"|drop\s+[\"']adult[\"']",
    re.IGNORECASE,
)

# Softening a generator prompt so a filter will let it through.
_SOFTEN_RE = re.compile(
    r"\b(?:tasteful|sfw|safe\s+for\s+work|"
    r"to\s+pass\s+moderation(?:\s+filters?)?|"
    r"pass(?:ing)?\s+(?:the\s+)?(?:moderation\s+)?filters?|"
    r"realistic\s+body\s+proportions?)\b",
    re.IGNORECASE,
)

# Memory lines that should never be kept.
_EVASION_RE = re.compile(
    r"pass(?:ing)?\s+moderation(?:\s+filters?)?"
    r"|moderation\s+filters?"
    r"|get(?:ting)?\s+(?:past|around|through)\s+(?:the\s+)?filters?"
    r"|beat(?:ing)?\s+(?:the\s+)?filters?"
    r"|filter(?:'s)?\s+(?:fight|catnip)"
    r"|to\s+pass\s+(?:the\s+)?filters?"
    r"|realistic\s+body\s+proportions?",
    re.IGNORECASE,
)

# A short "1/5. hold tight." stub; real content in one message is left alone.
_PART_STUB_RE = re.compile(
    r"^\s*1\s*/\s*([2-9]|[1-9]\d+)\b(.{0,80})$",
    re.IGNORECASE | re.DOTALL,
)
_PART_REQUEST_RE = re.compile(
    r"\bin\s+(\d+|two|three|four|five|six|seven|eight|nine|ten)\s+messages?\b",
    re.IGNORECASE,
)
_PART_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
PART_DECLINE = (
    "I only send one message at a time. I can do that in one message, "
    "or I can open a thread."
)


def asks_to_bypass_filter(text: str) -> bool:
    """True when they are asking for help getting around a filter."""
    raw = text or ""
    if not raw.strip():
        return False
    if _BYPASS_RE.search(raw):
        return True
    return bool(_FILTER_WORD_RE.search(raw) and _PLAN_RE.search(raw))


def coaches_bypass(text: str) -> bool:
    """True when a reply is teaching someone how to beat a filter."""
    return bool(_COACH_RE.search(text or ""))


def softened_prompt(model_prompt: str, user_text: str) -> bool:
    """True when the tool prompt added filter-evasion wording they did not use."""
    prompt = model_prompt or ""
    if not _SOFTEN_RE.search(prompt):
        return False
    return not _SOFTEN_RE.search(user_text or "")


def drop_filter_evasion(text: str) -> str:
    """Drop sentences or bullets about beating or passing a filter."""
    raw = text or ""
    if not raw:
        return raw
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        parts = re.split(r"(?<=[.!?])\s+", stripped)
        kept_parts = [part for part in parts if part and not _EVASION_RE.search(part)]
        if not kept_parts:
            continue
        prefix = line[: len(line) - len(line.lstrip())]
        kept.append(prefix + " ".join(kept_parts))
    return "\n".join(kept)


def is_refusal_text(text: str) -> bool:
    """True for the minors refusal or the filter-bypass refusal."""
    from core.blocklist import REFUSAL

    return (text or "").strip() in (REFUSAL, BYPASS_REFUSAL)


def part_count_request(text: str) -> int:
    """How many messages they asked for, or 0 if they did not."""
    hit = _PART_REQUEST_RE.search(text or "")
    if not hit:
        return 0
    raw = hit.group(1).casefold()
    if raw.isdigit():
        return int(raw)
    return _PART_WORDS.get(raw, 0)


def part_stub(text: str) -> bool:
    """True for a short '1/N' placeholder with no real body."""
    hit = _PART_STUB_RE.match((text or "").strip())
    if not hit:
        return False
    rest = (hit.group(2) or "").strip().strip(".-–—:")
    if len(rest) > 60:
        return False
    # "1/5. The cat sits..." is content. "1/5. hold tight." is a stub.
    if not rest:
        return True
    return bool(re.search(r"hold tight|coming right up|one moment|wait", rest, re.I)) or len(rest) <= 24


def asks_for_memory(text: str) -> bool:
    """True when they explicitly ask to see their own memory or context."""
    raw = " ".join((text or "").split())
    if not raw:
        return False
    return bool(
        re.search(
            r"(?:"
            r"what\s+(?:do\s+you|you)\s+(?:know|remember)\s+about\s+me"
            r"|what\s+you(?:'re|'ve| have)?\s+(?:know|remember|stored|got)\s+about\s+me"
            r"|show\s+(?:me\s+)?(?:my|the)\s+(?:memory|context|notes|profile)"
            r"|see\s+(?:my|the)\s+(?:memory|context)"
            r"|(?:my|your)\s+(?:memory|context)\b"
            r"|what(?:'s| is)\s+in\s+my\s+(?:memory|context)"
            r"|dump\s+(?:my\s+)?(?:memory|context)"
            r"|read\s+(?:my\s+)?(?:memory|context)"
            r"|remember\s+about\s+me"
            r")",
            raw,
            re.IGNORECASE,
        )
    )
