# watch_auto.py
# A clip addressed to Eche is watched before the model is asked anything.
#
# Live 2026-11-25 15:42-15:44 ET (#ai-slop-chat): three replies with a video
# got no watch_video call. The chat model answered from an older caption,
# said it had "no eyes", and once typed "[Watched the video from this
# message]" without anything running. A tool the model may skip is not
# enough, so when the message (or the message it replies to) carries a
# video or audio clip, on_message runs watch_video itself, through
# core.tools.execute (default-tools toggle, per-tool toggle, audit row,
# the watch lock, the cache and the local GPU queue all apply), and the
# reply is written from the watch notes.
#
# This module also cleans what the model says about watching: typed
# "[Watched ...]" style records are removed, a "watching it now" with no
# watch behind it and a "no eyes" denial while watching is on are replaced
# with a plain line, and those denials are kept out of memory prompts.

from __future__ import annotations

import re

from core.debuglog import dprint

TOOL = "watch_video"

# "watch this", "what's in this video", "listen to my voice message".
# Used to find a clip in the last few messages when none is on the message
# or its reply, and to watch Eche's own posted clip when asked about it.
_ASK = re.compile(
    r"(?i)\b(?:watch|listen\s+to|check\s+out|look\s+at|peep)\s+"
    r"(?:this|that|it|these|my|his|her|the\s+(?:video|vid|clip|audio|voice))\b"
    r"|\bwatch\s+(?:a|the)\s+(?:video|vid|clip)\b"
    r"|\b(?:what(?:'s|s|\s+is|\s+happens|\s+happened|\s+happening|\s+did|\s+do)|describe|explain|"
    r"summari[sz]e|transcribe|who(?:'s|\s+is))\b[^.?!\n]{0,40}\b(?:video|vid|clip|audio|"
    r"voice\s*(?:message|note|memo|clip))\b"
)
# "what's this", "what is happening": only counts when a clip is right there.
_QUESTION = re.compile(
    r"(?i)\bwhat(?:'s|s|\s+is|\s+are|\s+happens|\s+happened|\s+happening|\s+did|\s+do)\b"
    r"|\bdescribe\b|\bexplain\b|\bwho(?:'s|\s+is)\b|\bthoughts\b|\bwtf\b|\blol\b|\blmao\b|\?"
)


def asks_to_watch(text: str) -> bool:
    return bool(_ASK.search(str(text or "")))


def _make_or_edit(text: str) -> bool:
    """"turn this video into ...", "make it longer": the asset tool's job, not a watch."""
    try:
        from core.asset_intent import _MAKE_VERB_RE, _clean

        return bool(_MAKE_VERB_RE.search(_clean(text)))
    except Exception:
        return False


def _clip_refs(message) -> list:
    if message is None:
        return []
    try:
        from core.tools_watch import _video_refs

        return list(_video_refs(message))
    except Exception as exc:
        dprint(f"[watch_auto] clip read failed: {type(exc).__name__}")
        return []


def _has_pictures(message) -> bool:
    """A picture (not a clip) on the message: a picture question stays a picture question."""
    if message is None:
        return False
    try:
        from core.tools_watch import _other_files
    except Exception:
        return False
    try:
        return _other_files(message) > 0
    except Exception:
        return False


def watch_offered(*, speaker_is_owner: bool, admin_enabled: bool) -> bool:
    """watch_video would be offered to the model on this turn (toggles, ffmpeg)."""
    try:
        from core.tools import visible_names

        return TOOL in visible_names(speaker_is_owner=speaker_is_owner, admin_enabled=admin_enabled)
    except Exception:
        return False


async def plan(
    message, text: str, *, bot_user_id=None, offered: bool = True, media=None
) -> tuple[dict | None, str]:
    """(tool call, reason). The call is None when this turn should not auto-watch.

    Only reached for messages addressed to Eche (a mention or a reply to
    Eche); on_message returns before this for anything else.
    """
    if not offered or message is None:
        return None, "off"
    asks = asks_to_watch(text)
    if _make_or_edit(text) and not asks:
        return None, "edit"
    call = {"name": TOOL, "arguments": {}, "auto": True}
    if _clip_refs(message):
        return call, "trigger"
    from core.message_media import _referenced

    try:
        replied = await _referenced(message)
    except Exception:
        replied = None
    if replied is not None and _clip_refs(replied):
        mine = bot_user_id is not None and getattr(getattr(replied, "author", None), "id", None) == bot_user_id
        if mine and not (asks or _QUESTION.search(str(text or ""))):
            # A reply to Eche's own posted clip ("nice") is not a watch request.
            return None, "own-clip"
        return call, "reply"
    pictures = bool(getattr(media, "images", None) or getattr(media, "reply_images", None))
    if asks and not pictures and not _has_pictures(message) and not _has_pictures(replied):
        # watch_video looks through the last few messages here.
        return call, "asked"
    return None, "none"


# --- the reply ---------------------------------------------------------------------------------

def watch_prompt(prompt: str, notes: str) -> str:
    """The same turn again, with the watch notes. The reply is the chat answer."""
    from core.chat_media import FETCH_ERROR_NOTE, TOO_LARGE_NOTE

    source = (notes or "").strip() or "(no notes)"
    if len(source) > 1500:
        source = source[:1499].rstrip() + "…"
    base = (prompt or "").strip()
    # Notes from the chat-side frame grab ("you can't see it. Say so") are
    # stale once the clip has been watched.
    for stale in (FETCH_ERROR_NOTE, TOO_LARGE_NOTE):
        base = base.replace(stale, "")
    return (
        f"{base}\n\n"
        "Watch notes (what you saw and heard when you watched the clip just now; "
        f"[m:ss] are timestamps):\n{source}\n\n"
        "You watched it. Answer the most recent message from these notes only: say what is "
        "in the clip using them, and do not add anything they don't show. If they don't "
        "answer the question, say that. Do not say you can't see or watch videos, and do "
        "not write bracketed notes such as [Watched ...]."
    )


def notes_fallback(notes: str, limit: int = 600) -> str:
    """The watch notes themselves, when the model's answer can't be used."""
    flat = " ".join(str(notes or "").split())
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return f"Here's what I got from it: {flat}" if flat else ""


# Bracketed records the model types in imitation of the real tool notes:
# "[Watched the video from this message]", "[I used watch_video]".
_MARKER = re.compile(
    r"\[\s*(?:I\s+|Eche\s+)?(?:used|watched|watching|listened|listening|looked|viewed|saw|heard|"
    r"searched|opened|ran|called|played|posted|checked|analy[sz]ed|transcribed)\b[^\[\]\n]{0,200}\]",
    re.I,
)


def strip_action_markers(text: str) -> str:
    out = _MARKER.sub("", str(text or ""))
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


_DENIAL = re.compile(
    r"(?i)\b(?:don'?t|do\s+not|doesn'?t|does\s+not|ain'?t|haven'?t)\s+(?:have|has|got)\s+(?:any\s+)?eyes\b"
    r"|\b(?:have|has|got)\s+no\s+eyes\b|\bno\s+eyes\b"
    r"|\b(?:can'?t|cannot|can\s+not|unable\s+to|not\s+able\s+to|don'?t|do\s+not)\s+"
    r"(?:watch|see|view|play|open|hear|listen\s+to)\s+(?:videos?|vids?|clips?|audio|"
    r"voice\s+(?:messages?|notes?))\b"
    r"|\buse\s+the\s+asset\s+tool\s+on\s+it\b"
)
_PRETEND = re.compile(
    r"(?i)\b(?:i'?m|i\s+am|im)\s+(?:watching|listening\s+to|looking\s+at)\s+(?:it|this|that|the\s+(?:video|clip|audio))\b"
    r"|\b(?:okay|ok|alright|sure)[, ]+(?:i'?ll|i\s+will|let\s+me)\s+(?:watch|listen\s+to)\b"
    r"|\bwatched\s+the\s+(?:video|clip)\s+from\s+this\s+message\b"
)


def denies_watching(text: str) -> bool:
    return bool(_DENIAL.search(str(text or "")))


def pretends_watching(text: str) -> bool:
    return bool(_PRETEND.search(str(text or "")))


CAN_WATCH = (
    "I can watch videos and listen to audio clips. Reply to the clip (or send it) and "
    "mention me, and I'll watch it."
)
NOT_WATCHED = (
    "I haven't watched anything yet. Reply to the clip (or send it) and mention me, and I'll watch it."
)


_WATCH_MARKER = re.compile(
    r"\[\s*(?:I\s+|Eche\s+)?(?:watched|watching|listened|listening|used\s+watch_video)", re.I
)


def clean_reply(reply: str, *, watch_on: bool, watched: bool) -> str:
    """The model's chat reply on a turn where it wrote the answer itself."""
    raw = str(reply or "").strip()
    text = strip_action_markers(raw)
    if watch_on and not watched and (pretends_watching(raw) or _WATCH_MARKER.search(raw)):
        # Nothing was watched this turn: no "I'm watching it now".
        return NOT_WATCHED
    if watch_on and denies_watching(text):
        return CAN_WATCH
    return text


def grounded_reply(spoken: str, notes: str) -> str:
    """The answer written from watch notes. A denial or a stall falls back to the notes."""
    text = strip_action_markers(spoken)
    if not text or denies_watching(text) or pretends_watching(text):
        return notes_fallback(notes) or text
    return text


# --- memory ------------------------------------------------------------------------------------

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*\s*")


def scrub_text(text: str) -> str:
    """Drop sentences that deny watching or pretend to; drop typed markers."""
    raw = str(text or "")
    if not (denies_watching(raw) or pretends_watching(raw) or _MARKER.search(raw)):
        return raw
    kept = [
        part
        for part in _SENTENCE.findall(_MARKER.sub("", raw))
        if part.strip() and not denies_watching(part) and not pretends_watching(part)
    ]
    return "".join(kept).strip()


_RECORD = re.compile(r"\[I used [^\[\]\n]*\]")


def scrub_rows(rows) -> list[str]:
    """Eche's own recent replies, without capability denials or fake watch records.

    Real tool records ("[I used watch_video ...]") are written by on_message
    after a tool ran and are kept."""
    out = []
    for row in rows or []:
        line = str(row or "")
        records = _RECORD.findall(line)
        rest = scrub_text(_RECORD.sub("", line)) if records else scrub_text(line)
        cleaned = " ".join([*records, rest.strip()]).strip()
        if cleaned:
            out.append(cleaned)
    return out
