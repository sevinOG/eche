# core/memory_store.py
# Structured long-term memory for one person (side "user") and for Eche's own
# outputs with that person (side "bot").
#
# Stored as JSON on disk: memories/user-{id}/profile.json and self.json. The
# Discord pin ("Context for ..." / "Eche with ...") is a rendered copy.
#
# The summarizer only proposes edits (add / update / remove / touch, plus
# "kept" for Eche's promises). Everything else happens here, in code:
#   * every text is cleaned (mentions, ids, slurs, filter-evasion wording),
#     scrubbed (school/age wording next to sexual content), and checked by the
#     privacy filter (other people's names, sensitive details, minors);
#   * one-off asks are moved out of the lasting sections into "recent";
#   * near-copies are merged into the entry they repeat;
#   * projects and promises expire, recent topics fade, each section has a cap;
#   * the rendered text has a total length cap.

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass

VERSION = 2
ENTRY_CHARS = 120
# Rendered memory is never longer than this, whatever the pin allows.
RENDER_CAP = 900
DROP_WEIGHT = 0.2
DAY = 86400.0


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    cap: int
    durable: bool = True
    half_life_days: float = 0.0  # > 0: fades
    expire_days: float = 0.0  # > 0: removed after this long without a mention


SECTIONS: dict[str, tuple[Section, ...]] = {
    "user": (
        Section("identity", "Identity", 5),
        Section("style", "How they want to be treated", 6),
        Section("likes", "Likes", 5),
        Section("dislikes", "Dislikes", 3),
        Section("projects", "Ongoing projects", 4, durable=False, expire_days=30),
        Section("recent", "Recent topics", 5, durable=False, half_life_days=7),
    ),
    "bot": (
        Section("stances", "My stances with them", 5),
        Section("bits", "Running bits", 4, durable=False, half_life_days=21),
        Section("commitments", "My open promises", 4, durable=False, expire_days=21),
        Section("kept", "Promises I kept", 3, durable=False, half_life_days=14),
        Section("recent", "What I did lately", 4, durable=False, half_life_days=7),
    ),
}

# What the reply sees first when the room is short.
RENDER_ORDER: dict[str, tuple[str, ...]] = {
    "user": ("identity", "style", "projects", "likes", "dislikes", "recent"),
    "bot": ("commitments", "stances", "bits", "kept", "recent"),
}

_ID_PREFIX = {"user": "u", "bot": "b"}

_ONE_OFF = re.compile(
    r"(?i)^(?:they\s+|the person\s+)?(?:asked|asks|wanted to know|wants to know|requested|requests|"
    r"searched|looked up|inquired|was curious|wondered|had a question)\b"
)
_TRANSIENT = re.compile(
    r"(?i)\b(?:is|are|was)\s+(?:currently\s+)?(?:working on|building|making|fixing|setting up|trying)\b|"
    r"\b(?:right now|today|tonight|this week|at the moment|currently)\b"
)
_STOP = frozenset(
    """a an the and or but of to for in on at by is are was were be been has have had do does
    did with from that this these those it its they them their i my me you your he she his her
    about into also just like some very not as so than then there here all any each likes""".split()
)


def sections(side: str) -> tuple[Section, ...]:
    return SECTIONS["bot" if side == "bot" else "user"]


def section(side: str, key: str) -> Section | None:
    for sec in sections(side):
        if sec.key == key:
            return sec
    return None


# ------------------------------------------------------------------ files


def _path(user_id: int, side: str) -> str:
    from core.paths import memories_dir

    folder = os.path.join(memories_dir(), f"user-{int(user_id)}")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "self.json" if side == "bot" else "profile.json")


def store_names() -> tuple[str, ...]:
    """File names in memories/user-{id}/ (for the memory reset)."""
    return ("profile.json", "self.json")


def empty(side: str) -> dict:
    return {
        "v": VERSION,
        "side": "bot" if side == "bot" else "user",
        "sections": {sec.key: [] for sec in sections(side)},
        "next_id": 1,
        "legacy": None,
        "updated": 0.0,
    }


def exists(user_id: int, side: str) -> bool:
    try:
        return os.path.isfile(_path(int(user_id), side))
    except (TypeError, ValueError):
        return False


def load(user_id: int, side: str) -> dict | None:
    """The stored memory, or None when this person has none yet (not migrated)."""
    try:
        path = _path(int(user_id), side)
    except (TypeError, ValueError):
        return None
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return normalize(data, side)


def normalize(data, side: str) -> dict:
    out = empty(side)
    if not isinstance(data, dict):
        return out
    raw_sections = data.get("sections") if isinstance(data.get("sections"), dict) else {}
    highest = 0
    for sec in sections(side):
        rows = raw_sections.get(sec.key) if isinstance(raw_sections.get(sec.key), list) else []
        clean_rows = []
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("text") or "").strip():
                continue
            entry = {
                "id": str(row.get("id") or ""),
                "text": str(row["text"]).strip()[:ENTRY_CHARS],
                "first": _num(row.get("first")),
                "last": _num(row.get("last")),
                "hits": max(1, int(_num(row.get("hits"), 1))),
            }
            if not entry["id"]:
                continue
            digits = re.sub(r"\D", "", entry["id"])
            if digits:
                highest = max(highest, int(digits))
            clean_rows.append(entry)
        out["sections"][sec.key] = clean_rows
    out["next_id"] = max(int(_num(data.get("next_id"), 1)), highest + 1)
    legacy = data.get("legacy")
    out["legacy"] = legacy if isinstance(legacy, dict) else None
    out["updated"] = _num(data.get("updated"))
    return out


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def save(user_id: int, side: str, profile: dict) -> None:
    path = _path(int(user_id), side)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


# ------------------------------------------------------------------ text


def terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {w.strip("'") for w in words if len(w) >= 3 and w not in _STOP}


def similar(left: str, right: str) -> bool:
    """Near-copies: most words shared, or one contains the other."""
    a, b = terms(left), terms(right)
    if not a or not b:
        return (left or "").strip().lower() == (right or "").strip().lower()
    shared = len(a & b)
    small = min(len(a), len(b))
    if small and shared == small and small >= 2:
        return True
    return shared / len(a | b) >= 0.6


def _clip(text: str, limit: int = ENTRY_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "."


def clean_text(text) -> str:
    """One memory entry: one short sentence, filtered. Empty means drop it."""
    from core.content_filter import clean_memory_text
    from core.summary_safety import NEUTRAL_LINE, risky, scrub_summary

    body = " ".join(str(text or "").split()).strip()
    body = re.sub(r"^(?:[-*•–]\s+)+", "", body).strip().strip("\"'`").strip()
    if not body:
        return ""
    if risky(body) or scrub_summary(body) != body or NEUTRAL_LINE in body:
        return ""
    body = clean_memory_text(body).strip()
    if len(body) < 3:
        return ""
    try:
        from core.context_summarizer import _is_bad_llm_output

        if _is_bad_llm_output(body):
            return ""
    except Exception:
        pass
    if body[-1] not in ".!?":
        body += "."
    return _clip(body)


def _user_centric(text: str) -> bool:
    return bool(re.match(r"(?i)^(?:the\s+)?(?:user|they|the person|them)\b", text or ""))


# ------------------------------------------------------------------ edits


def _all(profile: dict):
    for key, rows in profile["sections"].items():
        for row in rows:
            yield key, row


def find(profile: dict, entry_id: str):
    for key, row in _all(profile):
        if row["id"] == str(entry_id):
            return key, row
    return None, None


def _new_id(profile: dict, side: str) -> str:
    number = int(profile.get("next_id") or 1)
    profile["next_id"] = number + 1
    return f"{_ID_PREFIX['bot' if side == 'bot' else 'user']}{number}"


def _bump(row: dict, now: float) -> None:
    row["last"] = now
    row["hits"] = int(row.get("hits") or 1) + 1


def _route(side: str, key: str, text: str) -> str:
    """Durability test in code: one-off asks fade, ongoing work expires."""
    sec = section(side, key)
    if sec is None:
        key = "recent"
        sec = section(side, key)
    if side == "user" and sec.durable:
        if _ONE_OFF.search(text) or text.rstrip().endswith("?"):
            return "recent"
        if key == "identity" and _TRANSIENT.search(text):
            return "projects"
    if side == "bot" and key == "stances" and _ONE_OFF.search(text):
        return "recent"
    return key


def _allowed(side: str, text: str, own_names, known_names, stats: dict) -> bool:
    from core.memory_privacy import entry_problem

    problem = entry_problem(text, own_names, known_names)
    if problem:
        stats["dropped"].append(problem)
        return False
    if side == "bot" and _user_centric(text):
        stats["dropped"].append("not first person")
        return False
    return True


def apply_ops(
    profile: dict,
    ops,
    side: str,
    *,
    now: float | None = None,
    own_names=(),
    known_names=(),
) -> dict:
    """Apply the summarizer's edits. Returns counts. Invalid parts are ignored."""
    now = time.time() if now is None else float(now)
    stats = {"added": 0, "updated": 0, "removed": 0, "touched": 0, "merged": 0, "kept": 0, "dropped": []}
    if not isinstance(ops, dict):
        return stats

    def _ids(name):
        raw = ops.get(name)
        return [str(x).strip() for x in raw if str(x).strip()] if isinstance(raw, list) else []

    for entry_id in _ids("remove"):
        key, row = find(profile, entry_id)
        if row is not None:
            profile["sections"][key].remove(row)
            stats["removed"] += 1

    if side == "bot":
        for entry_id in _ids("kept"):
            key, row = find(profile, entry_id)
            if row is None or key != "commitments":
                continue
            profile["sections"][key].remove(row)
            row = dict(row, last=now)
            profile["sections"]["kept"].append(row)
            stats["kept"] += 1

    updates = ops.get("update") if isinstance(ops.get("update"), list) else []
    for item in updates:
        if not isinstance(item, dict):
            continue
        key, row = find(profile, str(item.get("id") or ""))
        if row is None:
            continue
        text = clean_text(item.get("text"))
        if not text or not _allowed(side, text, own_names, known_names, stats):
            # The old wording stays. A bad rewrite is never stored.
            continue
        row["text"] = text
        _bump(row, now)
        stats["updated"] += 1

    for entry_id in _ids("touch"):
        key, row = find(profile, entry_id)
        if row is not None:
            _bump(row, now)
            stats["touched"] += 1

    adds = ops.get("add") if isinstance(ops.get("add"), list) else []
    for item in adds:
        if not isinstance(item, dict):
            continue
        text = clean_text(item.get("text"))
        if not text or not _allowed(side, text, own_names, known_names, stats):
            continue
        key = _route(side, str(item.get("section") or "").strip().lower(), text)
        if _merge_into_existing(profile, side, key, text, now):
            stats["merged"] += 1
            continue
        profile["sections"][key].append(
            {"id": _new_id(profile, side), "text": text, "first": now, "last": now, "hits": 1}
        )
        stats["added"] += 1

    maintain(profile, side, now=now)
    profile["updated"] = now
    return stats


def _merge_into_existing(profile: dict, side: str, key: str, text: str, now: float) -> bool:
    """A near-copy bumps the entry it repeats instead of adding a second one.

    A recent topic that repeats a lasting fact bumps the lasting fact.
    """
    candidates = [key]
    if not section(side, key).durable:
        candidates += [sec.key for sec in sections(side) if sec.key != key]
    for name in candidates:
        for row in profile["sections"].get(name, []):
            if similar(row["text"], text):
                _bump(row, now)
                return True
    return False


def weight(row: dict, sec: Section, now: float) -> float:
    if sec.half_life_days <= 0:
        return 1.0
    age = max(0.0, now - _num(row.get("last")))
    return math.pow(0.5, age / (sec.half_life_days * DAY))


def maintain(profile: dict, side: str, *, now: float | None = None) -> int:
    """Expire, fade, de-duplicate, and cap. Returns how many entries went."""
    now = time.time() if now is None else float(now)
    gone = 0
    for sec in sections(side):
        rows = profile["sections"].setdefault(sec.key, [])
        kept: list[dict] = []
        for row in rows:
            if sec.expire_days > 0 and now - _num(row.get("last")) > sec.expire_days * DAY:
                gone += 1
                continue
            if sec.half_life_days > 0 and weight(row, sec, now) < DROP_WEIGHT:
                gone += 1
                continue
            if any(similar(other["text"], row["text"]) for other in kept):
                twin = next(o for o in kept if similar(o["text"], row["text"]))
                twin["hits"] = int(twin["hits"]) + int(row.get("hits") or 1)
                twin["last"] = max(_num(twin["last"]), _num(row.get("last")))
                gone += 1
                continue
            kept.append(row)
        if len(kept) > sec.cap:
            ranked = sorted(kept, key=lambda r: _rank(r, sec, now), reverse=True)
            keep_ids = {r["id"] for r in ranked[: sec.cap]}
            gone += len(kept) - sec.cap
            kept = [r for r in kept if r["id"] in keep_ids]
        profile["sections"][sec.key] = kept
    return gone


def _rank(row: dict, sec: Section, now: float) -> tuple:
    if sec.durable:
        return (int(row.get("hits") or 1), _num(row.get("last")))
    return (weight(row, sec, now), _num(row.get("last")))


# ------------------------------------------------------------------ render


def listing(profile: dict | None) -> str:
    """What the summarizer sees: id | section | text."""
    if not profile:
        return "(none yet)"
    lines = [f"{row['id']} | {key} | {row['text']}" for key, row in _all(profile)]
    return "\n".join(lines) or "(none yet)"


def is_empty(profile: dict | None) -> bool:
    return not profile or not any(profile["sections"].get(k) for k in profile["sections"])


def render(profile: dict | None, limit: int, side: str, *, now: float | None = None) -> str:
    """One "- Title: entry entry" line per section, most important first.

    Whole entries only; an entry that would not fit is skipped. Never longer
    than limit (and never above RENDER_CAP).
    """
    if not profile:
        return ""
    now = time.time() if now is None else float(now)
    room = max(0, min(int(limit), RENDER_CAP))
    lines: list[str] = []
    used = 0
    for key in RENDER_ORDER["bot" if side == "bot" else "user"]:
        sec = section(side, key)
        rows = list(profile["sections"].get(key) or [])
        if not rows:
            continue
        rows.sort(key=lambda r: _rank(r, sec, now), reverse=True)
        head = f"- {sec.title}:"
        sep = 1 if lines else 0
        parts: list[str] = []
        for row in rows:
            trial = " ".join([head, *parts, row["text"]])
            if used + sep + len(trial) <= room:
                parts.append(row["text"])
        if parts:
            line = " ".join([head, *parts])
            used += sep + len(line)
            lines.append(line)
    return "\n".join(lines)


def prompt_text(profile: dict | None, side: str, limit: int | None = None) -> str:
    """The long-term memory block the reply sees."""
    from core.content_filter import clean_memory_text

    if limit is None:
        from core.context_caps import get

        limit = get("memory_summary_prompt_chars")
    return clean_memory_text(render(profile, limit, side))
