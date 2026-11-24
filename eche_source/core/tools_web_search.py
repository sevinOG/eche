# core/tools_web_search.py
# Chat tool: web search through the Gemini API with Grounding with Google
# Search. Replaces the DuckDuckGo lookup (scraping its lite page started
# getting a bot check on every call, live 2026-11-21 .. 11-23).
#
# One generateContent call: the question goes in, a short grounded answer
# and its sources come out. The answer is handed back to the chat model as
# lookup notes; the Google search links for the queries Gemini ran are added
# under the chat reply (Discord cannot show Gemini's search-suggestion chip).
#
# Key: Settings -> Web search -> Gemini API key (gemini_api_key, DPAPI like
# the other keys). It is sent only in the x-goog-api-key header, never in a
# URL, and never logged. Without a key the tool is not offered.
#
# This is a TEXT model call. The request never asks for images
# (no output-modality field), the model id must be a text Gemini model, and a
# reply with image or file data is refused. This is the only module allowed
# to name the Gemini API host (tests/test_no_media_gen.py).

from __future__ import annotations

import re
from urllib.parse import quote_plus

import aiohttp

from core.debuglog import dprint
from core.tools import ACCESS_ANYONE, Tool, ToolContext, ToolResult, register

TOOL_NAME = "web_search"
LEGACY_NAME = "duckduckgo"  # hidden alias for one release (core/tools._ALIASES)
DEFAULT_MODEL = "gemini-3.5-flash-lite"
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_TIMEOUT = 15
_QUERY_LIMIT = 300
_ANSWER_LIMIT = 1100
_NOTES_LIMIT = 1500
_SOURCES = 3
_SEARCH_LINKS = 2
_TEMPERATURE = 0.2
_MAX_OUTPUT_TOKENS = 400
_SYSTEM = (
    "Answer the question in 2 to 4 factual sentences using Google Search. "
    "Say plainly when the results do not answer it. No speculation."
)
# Text models only. Anything that names an image, video, audio or live model is refused.
_MODEL_RE = re.compile(r"^gemini-[0-9a-z][0-9a-z.\-]*$")
_NOT_TEXT = ("image", "banana", "imagen", "veo", "tts", "live", "audio", "embedding", "robotics", "omni")

_DESCRIPTION = (
    "Search the web (Google) for a fact, score, news, date, or definition. "
    "Pass their question as query."
)


def _messages():
    from core import client

    return client


def gemini_key() -> str:
    """The saved Gemini API key, or GEMINI_API_KEY in a dev .env. Never logged."""
    key = ""
    try:
        from core.secrets import load_all

        key = str(load_all().get("gemini_api_key") or "").strip()
    except Exception:
        key = ""
    if not key:
        import os

        key = str(os.environ.get("GEMINI_API_KEY") or "").strip()
    return key


def has_key() -> bool:
    return bool(gemini_key())


def text_model_ok(model: str) -> bool:
    name = (model or "").strip().lower()
    if not _MODEL_RE.match(name):
        return False
    if any(word in name for word in _NOT_TEXT):
        return False
    try:
        from core.media_scan import is_image_model_id

        if is_image_model_id(name):
            return False
    except Exception:
        return False
    return True


def search_model() -> str:
    """Settings -> Web search -> Model. Blank or not a text Gemini model uses the default."""
    stored = ""
    try:
        from core.secrets import public_value

        stored = str(public_value("gemini_search_model") or "").strip()
    except Exception:
        stored = ""
    if not stored:
        return DEFAULT_MODEL
    if not text_model_ok(stored):
        dprint(f"[web_search] model {stored!r} is not a text Gemini model; using {DEFAULT_MODEL}")
        return DEFAULT_MODEL
    return stored.lower()


def build_request(query: str, model: str) -> tuple[str, dict, dict]:
    """(url, headers, body) for one grounded generateContent call. The key is added by the caller."""
    url = _ENDPOINT.format(model=model)
    headers = {"Content-Type": "application/json"}
    body = {
        "systemInstruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": query}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": _TEMPERATURE, "maxOutputTokens": _MAX_OUTPUT_TOKENS},
    }
    return url, headers, body


def _query_text(arguments: dict | None) -> str:
    raw = ""
    if isinstance(arguments, dict):
        value = arguments.get("query")
        if value is None or str(value).strip() == "":
            value = arguments.get("q")
        raw = "" if value is None else str(value)
    text = " ".join(raw.split())
    if len(text) > _QUERY_LIMIT:
        text = text[:_QUERY_LIMIT].rstrip()
    return text


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def search_link(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query)


class SearchFailure(Exception):
    """A lookup that did not produce an answer. status is a short machine word."""

    def __init__(self, status: str, outcome: str, text: str):
        super().__init__(status)
        self.status = status
        self.outcome = outcome
        self.text = text


def parse_response(data) -> dict:
    """{"answer", "sources": [(title, uri)], "queries": [..], "grounded", "finish"}.

    Raises SearchFailure for a blocked prompt, an empty answer, or any image
    or file data in the reply.
    """
    msgs = _messages()
    if not isinstance(data, dict):
        raise SearchFailure("unreadable", "error", msgs.LOOKUP_UNREACHABLE)
    feedback = data.get("promptFeedback") if isinstance(data.get("promptFeedback"), dict) else {}
    if feedback.get("blockReason"):
        raise SearchFailure("refused", "refusal", msgs.LOOKUP_REFUSED)
    candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    if not candidates or not isinstance(candidates[0], dict):
        raise SearchFailure("empty", "error", msgs.LOOKUP_REFUSED)
    first = candidates[0]
    finish = str(first.get("finishReason") or "")
    content = first.get("content") if isinstance(first.get("content"), dict) else {}
    parts = content.get("parts") if isinstance(content.get("parts"), list) else []
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if "inlineData" in part or "inline_data" in part or "fileData" in part or "file_data" in part:
            raise SearchFailure("media", "refusal", msgs.LOOKUP_REFUSED)
        if part.get("thought"):
            continue
        if isinstance(part.get("text"), str):
            texts.append(part["text"])
    answer = " ".join(" ".join(texts).split())
    if not answer:
        if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
            raise SearchFailure("refused", "refusal", msgs.LOOKUP_REFUSED)
        raise SearchFailure("empty", "error", msgs.LOOKUP_REFUSED)
    meta = first.get("groundingMetadata") if isinstance(first.get("groundingMetadata"), dict) else {}
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for chunk in meta.get("groundingChunks") or []:
        web = chunk.get("web") if isinstance(chunk, dict) else None
        if not isinstance(web, dict):
            continue
        uri = str(web.get("uri") or "").strip()
        title = " ".join(str(web.get("title") or "").split())
        if not uri.startswith("https://"):
            continue
        tag = (title or uri).lower()
        if tag in seen:
            continue
        seen.add(tag)
        sources.append((title or uri, uri))
        if len(sources) >= _SOURCES:
            break
    queries: list[str] = []
    for q in meta.get("webSearchQueries") or []:
        q = " ".join(str(q or "").split())[:_QUERY_LIMIT]
        if q and q not in queries:
            queries.append(q)
    return {
        "answer": answer,
        "sources": sources,
        "queries": queries,
        "grounded": bool(meta),
        "finish": finish,
    }


def notes_text(parsed: dict) -> str:
    """Lookup notes for the chat model: the answer, then numbered sources."""
    body = _clip(parsed.get("answer") or "", _ANSWER_LIMIT)
    lines = [f"{i}. {title} — {uri}" for i, (title, uri) in enumerate(parsed.get("sources") or [], 1)]
    text = body + ("\nSources:\n" + "\n".join(lines) if lines else "")
    return _clip(text, _NOTES_LIMIT)


def links_footer(parsed: dict) -> str:
    """Google search links for the queries Gemini ran (the search-suggestion stand-in)."""
    queries = (parsed.get("queries") or [])[:_SEARCH_LINKS]
    if not queries:
        return ""
    links = " · ".join(f"[{_clip(q, 80)}](<{search_link(q)}>)" for q in queries)
    return f"-# Google Search: {links}"


def _detail(query: str, model: str, parsed: dict | None = None, status: str = "", http: int = 0) -> str:
    lines = [f"query: {query}", f"model: {model}"]
    if status:
        lines.append(f"status: {status}" + (f" (HTTP {http})" if http else ""))
    if parsed:
        lines.append(f"grounded: {'yes' if parsed.get('grounded') else 'no (answer not grounded)'}")
        lines.append(f"finish: {parsed.get('finish') or '-'}")
        for q in parsed.get("queries") or []:
            lines.append(f"searched: {q}")
        for i, (title, uri) in enumerate(parsed.get("sources") or [], 1):
            lines.append(f"{i}. {title} — {uri}")
    return "\n".join(lines)


def _error_reason(payload) -> str:
    """Google's machine reason (API_KEY_INVALID, ...) or status word. Never the message text."""
    if not isinstance(payload, dict):
        return ""
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    for item in err.get("details") or []:
        if isinstance(item, dict) and item.get("reason"):
            return str(item["reason"])[:60]
    return str(err.get("status") or "")[:60]


def _error_message(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    return str(err.get("message") or "").lower()


def classify_http(status: int, payload) -> SearchFailure:
    msgs = _messages()
    reason = _error_reason(payload)
    message = _error_message(payload)
    key_problem = "API_KEY" in reason or "api key" in message
    if status in (400, 401, 403) and key_problem:
        return SearchFailure("bad_key", "refusal", msgs.LOOKUP_BAD_KEY)
    if status == 429:
        return SearchFailure("quota", "refusal", msgs.LOOKUP_QUOTA)
    if status == 404:
        return SearchFailure("model_missing", "error", msgs.LOOKUP_MODEL)
    if status in (400, 403):
        if status == 403 or any(w in message for w in ("google_search", "grounding", "billing", "free tier", "search tool")):
            return SearchFailure("no_grounding", "refusal", msgs.LOOKUP_NO_GROUNDING)
        return SearchFailure("bad_request", "error", msgs.LOOKUP_UNREACHABLE)
    return SearchFailure("unreachable", "error", msgs.LOOKUP_UNREACHABLE)


def _audit_api(outcome: str, model: str, status: str, http: int = 0) -> None:
    try:
        from core.audit import api as audit_api

        audit_api("gemini-search", outcome=outcome, model=model, status=status, http_status=str(http or ""))
    except Exception:
        pass


async def post_search(url: str, headers: dict, body: dict, key: str) -> tuple[int, object]:
    """One POST. Returns (HTTP status, parsed JSON or None). Raises on transport failure."""
    sent = dict(headers)
    sent["x-goog-api-key"] = key
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body, headers=sent) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = None
            return resp.status, data


async def web_search(ctx: ToolContext, arguments: dict) -> ToolResult:
    """Run one grounded search and return notes for the chat model."""
    del ctx
    msgs = _messages()
    query = _query_text(arguments)
    if not query:
        return ToolResult(text="Tell me what to look up.", fence=False, detail="query: (empty)", declined=True)
    model = search_model()
    key = gemini_key()
    if not key:
        return ToolResult(
            text=msgs.LOOKUP_NO_KEY, fence=False, for_model=True, outcome="refusal",
            status="no_key", detail=_detail(query, model, status="no_key"),
        )
    url, headers, body = build_request(query, model)
    http = 0
    try:
        try:
            http, data = await post_search(url, headers, body, key)
        except Exception as exc:
            dprint(f"[web_search] request failed: {type(exc).__name__}")
            raise SearchFailure("unreachable", "error", msgs.LOOKUP_UNREACHABLE) from None
        if http != 200:
            raise classify_http(http, data)
        parsed = parse_response(data)
    except SearchFailure as fail:
        _audit_api(fail.outcome, model, fail.status, http)
        return ToolResult(
            text=fail.text, fence=False, for_model=True, outcome=fail.outcome,
            status=fail.status, detail=_detail(query, model, status=fail.status, http=http),
        )
    _audit_api("success", model, "grounded" if parsed["grounded"] else "ungrounded", http)
    return ToolResult(
        text=notes_text(parsed),
        fence=False,
        for_model=True,
        footer=links_footer(parsed),
        detail=_detail(query, model, parsed),
    )


def register_web_search_tool() -> None:
    register(
        Tool(
            name=TOOL_NAME,
            description=_DESCRIPTION,
            handler=web_search,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The question or search terms.",
                    },
                },
                "required": ["query"],
            },
            access=ACCESS_ANYONE,
            available=has_key,
        )
    )


register_web_search_tool()
