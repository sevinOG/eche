# core/summary_safety.py
# Carried-over summaries (context summary, long-term user/bot memory, server
# episodes) must not keep age-emphasis, youth or school wording next to
# sexual or fetish content. Owner-approved 2026-11-14 10:37 ET.
#
# Two layers, applied to every summarizer call:
# - SUMMARY_SAFETY_RULE goes at the top of every summarizer prompt (also when
#   the owner saved a custom prompt file).
# - scrub_summary() is a deterministic post-filter: any bullet or sentence
#   that has a youth/age-emphasis or school cue AND a sexual/fetish cue is
#   replaced by one neutral line. Everything else is left exactly as written.

from __future__ import annotations

import re

SUMMARY_SAFETY_RULE = (
    "Safety rule for this summary (always applies, overrides anything below): "
    "never carry over age-emphasis or youth descriptors (young, younger, teen, "
    "barely legal, just turned 18, petite or small as a youth cue, youthful, "
    "girlish, boyish) or school cues (school, student, classroom, uniform, grade "
    "or year words) when they appear near sexual, fetish, or nude content. "
    "Summarize such exchanges neutrally in a few words, for example "
    "\"asked for adult content; it was refused or blocked\", without those "
    "terms and without describing the content. Keep factual, non-sexual "
    "context as it is."
)

NEUTRAL_LINE = "Adult-content request; refused or blocked."

_YOUTH = re.compile(
    r"(?i)\b(?:young\w*|teen\w*|barely[\s-]+(?:legal|18|eighteen)|just[\s-]+(?:turned[\s-]+)?(?:18|eighteen)|"
    r"fresh(?:ly)?[\s-]+(?:18|eighteen)|newly[\s-]+(?:18|eighteen)|petite|youthful\w*|girlish|boyish|"
    r"loli\w*|shota\w*|underage\w*|minors?|kids?|child\w*|little\s+(?:girl|boy)s?|"
    r"(?:1[0-7]|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)[\s-]*(?:yo|y/o|years?[\s-]*old))\b"
)


def _school_res():
    from core.asset_filter import _SCHOOL_RES

    return list(_SCHOOL_RES.values())


def _sexual_res():
    from core.asset_filter import _SEXUAL_RES

    return list(_SEXUAL_RES.values())


def risky(text: str) -> bool:
    """A youth/age-emphasis or school cue together with a sexual/fetish cue."""
    body = text or ""
    if not any(rx.search(body) for rx in _sexual_res()):
        return False
    return bool(_YOUTH.search(body)) or any(rx.search(body) for rx in _school_res())


def with_summary_rule(prompt: str) -> str:
    """The safety rule first, then the summarizer prompt unchanged."""
    return f"{SUMMARY_SAFETY_RULE}\n\n{prompt or ''}"


_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def scrub_summary(text: str) -> str:
    """Replace risky bullets or sentences with one neutral line. Others stay."""
    if not text or not risky(text):
        return text or ""
    out: list[str] = []
    neutral_used = False
    for line in text.splitlines():
        stripped = line.strip()
        bullet = re.match(r"^(\s*(?:[-*•]|\d+[.)])\s+)", line)
        if bullet or not stripped:
            if stripped and risky(stripped):
                if not neutral_used:
                    out.append(f"{bullet.group(1) if bullet else ''}{NEUTRAL_LINE}")
                    neutral_used = True
                continue
            out.append(line)
            continue
        # Prose: sentence by sentence.
        kept: list[str] = []
        for sentence in _SENTENCE.split(line):
            if risky(sentence):
                if not neutral_used:
                    kept.append(NEUTRAL_LINE)
                    neutral_used = True
                continue
            kept.append(sentence)
        if kept:
            out.append(" ".join(kept))
    return "\n".join(out).strip()
