# core/search_grok.py
# Web search provider: Grok on the xAI Responses API with the server-side
# web_search and x_search tools (Settings -> Web search -> Search provider).
#
# One POST to the Responses endpoint with ONLY those two tools. No image
# tools, no image search, no image or video understanding. The answer text
# and its citations come back in the same shape as the Gemini provider
# (core/tools_web_search.parse_response). A reply with any image or file
# output is refused.
#
# Key: the existing xAI API key (xai_api_key, DPAPI, the same field Imagine
# uses). It is sent only in the Authorization header and never logged.
# This is the only module allowed to name the xAI API host for search
# (tests/test_no_media_gen.py).

from __future__ import annotations

import re
from urllib.parse import urlparse

import aiohttp

from core.debuglog import dprint

# Cheapest current tier ($1.25 / $2.50 per 1M tokens), non-reasoning so it
# answers fast; eche already runs its safety checks on it with this key.
GROK_MODEL = "grok-4.20-0309-non-reasoning"
_ENDPOINT = "https://api.x.ai/v1/responses"
_TIMEOUT = 30
_MAX_OUTPUT_TOKENS = 500
_MAX_TURNS = 3
_TEMPERATURE = 0.2
_SOURCES = 3
_TOOLS = ({"type": "web_search"}, {"type": "x_search"})
_SYSTEM = (
    "Answer the question in 2 to 4 factual sentences using web and X search. "
    "Say plainly when the results do not answer it. No speculation. Text only."
)
_TEXT_TYPES = frozenset({"output_text", "text"})
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_INLINE_CITE = re.compile(r"\[\[\d+\]\]\([^)]*\)")


def _w():
    from core import tools_web_search

    return tools_web_search


def _messages():
    from core import client

    return client


def xai_key() -> str:
    """The saved xAI key (the Imagine key). Never logged."""
    try:
        from core.secrets import load_all

        return str(load_all().get("xai_api_key") or "").strip()
    except Exception:
        return ""


def build_request(query: str) -> tuple[str, dict, dict]:
    """(url, headers, body). The key is added by post_search only."""
    body = {
        "model": GROK_MODEL,
        "instructions": _SYSTEM,
        "input": [{"role": "user", "content": query}],
        "tools": [dict(t) for t in _TOOLS],
        "include": ["no_inline_citations"],
        "max_output_tokens": _MAX_OUTPUT_TOKENS,
        "max_turns": _MAX_TURNS,
        "temperature": _TEMPERATURE,
        "store": False,
    }
    return _ENDPOINT, {"Content-Type": "application/json"}, body


async def post_search(url: str, headers: dict, body: dict, key: str) -> tuple[int, object]:
    sent = dict(headers)
    sent["Authorization"] = f"Bearer {key}"
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body, headers=sent) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = None
            return resp.status, data


def _site(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return url
    return host[4:] if host.startswith("www.") else host or url


def parse_response(data) -> dict:
    """{"answer", "sources": [(title, uri)], "queries", "grounded", "finish"}."""
    W = _w()
    msgs = _messages()
    if not isinstance(data, dict):
        raise W.SearchFailure("unreadable", "error", msgs.LOOKUP_XAI_UNREACHABLE)
    output = data.get("output") if isinstance(data.get("output"), list) else []
    texts: list[str] = []
    annotated: list[tuple[str, str]] = []
    searched = False
    for item in output:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if "image" in kind or ("file" in kind and "search" not in kind):
            raise W.SearchFailure("media", "refusal", msgs.LOOKUP_XAI_REFUSED)
        if kind.endswith("search_call"):
            searched = True
            continue
        if kind != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            ptype = str(part.get("type") or "")
            if ptype == "refusal":
                raise W.SearchFailure("refused", "refusal", msgs.LOOKUP_XAI_REFUSED)
            if ptype not in _TEXT_TYPES:
                raise W.SearchFailure("media", "refusal", msgs.LOOKUP_XAI_REFUSED)
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
            for ann in part.get("annotations") or []:
                if isinstance(ann, dict) and ann.get("type") == "url_citation":
                    url = str(ann.get("url") or "").strip()
                    title = " ".join(str(ann.get("title") or "").split())
                    annotated.append((title, url))
    answer = " ".join(" ".join(texts).split())
    answer = " ".join(_INLINE_CITE.sub("", _MD_IMAGE.sub("", answer)).split())
    if not answer:
        raise W.SearchFailure("empty", "error", msgs.LOOKUP_XAI_REFUSED)
    urls = [(t, u) for t, u in annotated]
    for u in data.get("citations") or []:
        urls.append(("", str(u or "").strip()))
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for title, url in urls:
        if not url.startswith("https://") or url in seen:
            continue
        seen.add(url)
        if not title or title.isdigit():
            title = _site(url)
        sources.append((title, url))
        if len(sources) >= _SOURCES:
            break
    return {
        "answer": answer,
        "sources": sources,
        "queries": [],
        "grounded": bool(sources) or searched,
        "finish": str(data.get("status") or ""),
    }


def error_info(payload, key: str = "") -> dict:
    """xAI error body ({"code", "error"} or {"error": {"message", "type", "code"}})."""
    W = _w()
    info = {"status": "", "message": "", "reason": "", "violations": [], "retry": ""}
    if not isinstance(payload, dict):
        return info
    err = payload.get("error")
    if isinstance(err, dict):
        info["message"] = W.scrub(err.get("message"), key)
        info["status"] = W._code(err.get("type"), 40)
        info["reason"] = W._code(err.get("code"), 60)
    else:
        info["message"] = W.scrub(err, key)
        info["status"] = W._code(str(payload.get("code") or "").replace(" ", "_"), 60)
    return info


def classify_http(status: int, info: dict):
    W = _w()
    msgs = _messages()
    message = (info.get("message") or "").lower()
    credits = any(w in message for w in ("credit", "spending limit", "billing", "balance", "payment"))
    if status == 401 or (status in (400, 403) and ("api key" in message or "api_key" in message)):
        fail = W.SearchFailure("bad_key", "refusal", msgs.LOOKUP_XAI_BAD_KEY)
    elif credits and status in (402, 403, 429):
        fail = W.SearchFailure("no_credits", "refusal", msgs.LOOKUP_XAI_CREDITS)
    elif status == 403:
        fail = W.SearchFailure("bad_key", "refusal", msgs.LOOKUP_XAI_BAD_KEY)
    elif status == 429:
        fail = W.SearchFailure("quota", "refusal", msgs.LOOKUP_XAI_RATE)
        fail.reason = "rate"
    elif status == 404:
        fail = W.SearchFailure("model_missing", "error", msgs.LOOKUP_MODEL)
    elif status in (400, 422):
        fail = W.SearchFailure("bad_request", "error", msgs.LOOKUP_XAI_UNREACHABLE)
    else:
        fail = W.SearchFailure("unreachable", "error", msgs.LOOKUP_XAI_UNREACHABLE)
    if not fail.reason:
        fail.reason = W._code(info.get("reason") or fail.status, 40)
    fail.http = status
    return fail


async def search(query: str, key: str):
    """One call: the parsed dict, or a SearchFailure (logged and audited)."""
    W = _w()
    msgs = _messages()
    url, headers, body = build_request(query)
    http = 0
    try:
        try:
            http, data = await post_search(url, headers, body, key)
        except Exception as exc:
            dprint(f"[web_search] xAI request failed: {type(exc).__name__}")
            raise W.SearchFailure("unreachable", "error", msgs.LOOKUP_XAI_UNREACHABLE) from None
        if not 200 <= http < 300:
            info = error_info(data, key)
            fail = classify_http(http, info)
            fail.lines = W.error_lines(GROK_MODEL, http, info)
            W._log_lines(fail.lines)
            raise fail
        parsed = parse_response(data)
    except W.SearchFailure as fail:
        fail.http = fail.http or http
        W._audit_api(fail.outcome, GROK_MODEL, fail.status, http, fail.reason, service="grok-search")
        return fail
    W._audit_api("success", GROK_MODEL, "grounded" if parsed["grounded"] else "ungrounded", http,
                 service="grok-search")
    return parsed
