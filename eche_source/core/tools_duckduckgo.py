# core/tools_duckduckgo.py
# Default chat tool: DuckDuckGo lookup.
# Instant Answer is used when it has text. An empty shell (the "Just Another
# Test" meta block) falls through to the lite search results. Anyone can ask.
# The lookup notes are handed back to the model. Chat gets that reply, not the raw page.

from __future__ import annotations

import asyncio
import html
import json
import re
import urllib.request
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from core.debuglog import dprint
from core.tools import ACCESS_ANYONE, Tool, ToolContext, ToolResult, register

_API = "https://api.duckduckgo.com/"
_LITE = "https://lite.duckduckgo.com/lite/"
_TAG = re.compile(r"<[^>]+>")
_RESULT_A = re.compile(
    r"<a\b([^>]*\bclass=['\"]result-link['\"][^>]*)>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_HREF = re.compile(r"""href=['"]([^'"]+)['"]""", re.IGNORECASE)
_SNIPPET = re.compile(
    r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
_LIMIT = 1500
_QUERY_LIMIT = 300
_RELATED_LIMIT = 4
_SEARCH_LIMIT = 4
_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_DESCRIPTION = (
    "Look up a fact, score, news, date, or definition. Pass their question as query."
)


def _plain(value) -> str:
    text = html.unescape(str(value or ""))
    text = _TAG.sub("", text)
    return " ".join(text.split())


def _footer(source: str, url: str) -> str:
    source = _plain(source)
    url = _plain(url)
    if source and url:
        return f"Source: {source} — {url}"
    if url:
        return url
    if source:
        return f"Source: {source}"
    return ""


def _clip(body: str, footer: str, limit: int = _LIMIT) -> str:
    body = (body or "").strip()
    footer = (footer or "").strip()
    text = f"{body}\n{footer}" if footer else body
    if len(text) <= limit:
        return text
    if not footer or limit < len(footer) + 40:
        return text[: limit - 1].rstrip() + "…"
    room = limit - len(footer) - 2
    return body[:room].rstrip() + "…\n" + footer


def _related_lines(items, limit: int = _RELATED_LIMIT) -> list[str]:
    lines: list[str] = []

    def walk(nodes) -> None:
        if not isinstance(nodes, list):
            return
        for item in nodes:
            if len(lines) >= limit:
                return
            if not isinstance(item, dict):
                continue
            nested = item.get("Topics")
            if nested:
                walk(nested)
                continue
            text = _plain(item.get("Text"))
            if text:
                lines.append(text)

    walk(items)
    return lines


def debug_payload(query: str, payload: dict, sent: str) -> str:
    """Fields the log can expand when the short answer is not enough to debug."""
    lines = [f"query: {query}", f"sent: {sent}", ""]
    if not isinstance(payload, dict):
        lines.append(f"payload: {payload!r}")
        return "\n".join(lines)
    for key in (
        "Type",
        "Answer",
        "AnswerType",
        "AbstractText",
        "Abstract",
        "AbstractSource",
        "AbstractURL",
        "Definition",
        "DefinitionSource",
        "DefinitionURL",
        "Heading",
    ):
        value = _plain(payload.get(key))
        if len(value) > 500:
            value = value[:499].rstrip() + "…"
        lines.append(f"{key}: {value or '(empty)'}")
    topics = payload.get("RelatedTopics")
    count = len(topics) if isinstance(topics, list) else 0
    lines.append(f"RelatedTopics: {count}")
    for line in _related_lines(topics, limit=6):
        if len(line) > 240:
            line = line[:239].rstrip() + "…"
        lines.append(f"  - {line}")
    try:
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        raw = repr(payload)
    if len(raw) > 4000:
        raw = raw[:3999].rstrip() + "…"
    lines.append("")
    lines.append("raw:")
    lines.append(raw)
    return "\n".join(lines)


def has_instant(payload: dict) -> bool:
    """True when Instant Answer has text. The empty test meta block does not count."""
    if not isinstance(payload, dict):
        return False
    if _plain(payload.get("Answer")):
        return True
    if _plain(payload.get("AbstractText") or payload.get("Abstract")):
        return True
    if _plain(payload.get("Definition")):
        return True
    return bool(_related_lines(payload.get("RelatedTopics"), limit=1))


def _short(value, limit: int = 280) -> str:
    text = _plain(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _result_url(href: str) -> str:
    href = html.unescape(href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    try:
        target = parse_qs(urlparse(href).query).get("uddg", [""])[0]
    except Exception:
        target = ""
    if target.startswith("http"):
        return target
    if href.startswith("http") and "duckduckgo.com/l/" not in href:
        return href
    return ""


def parse_lite_results(page: str, limit: int = _SEARCH_LIMIT) -> list[dict]:
    """Titles, snippets, and target URLs from a DuckDuckGo lite results page."""
    found: list[dict] = []
    matches = list(_RESULT_A.finditer(page or ""))
    for index, match in enumerate(matches):
        if len(found) >= limit:
            break
        title = _plain(match.group(2))
        if not title:
            continue
        href_m = _HREF.search(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(page)
        snippet_m = _SNIPPET.search(page[match.end() : end])
        snippet = _short(snippet_m.group(1)) if snippet_m else ""
        url = _result_url(href_m.group(1)) if href_m else ""
        found.append({"title": title, "snippet": snippet, "url": url})
    return found


def search_answer(results: list[dict]) -> str:
    """The first hit is the answer. Two more sit under it as extra reading."""
    if not results:
        return "DuckDuckGo had no results for that."
    first = results[0]
    title = first.get("title") or ""
    snippet = first.get("snippet") or ""
    if title and snippet:
        body = f"{title}\n{snippet}"
    else:
        body = title or snippet
    more = []
    for item in results[1:3]:
        bit = item.get("title") or ""
        snip = item.get("snippet") or ""
        if bit and snip:
            more.append(f"- {bit} — {snip}")
        elif bit or snip:
            more.append(f"- {bit or snip}")
    if more:
        body = body + "\n\n" + "\n".join(more)
    return _clip(body, first.get("url") or "")


def search_detail(query: str, results: list[dict], sent: str) -> str:
    lines = [
        f"query: {query}",
        "instant: empty (the meta block is DuckDuckGo's test record, not a result)",
        f"sent: {sent}",
        "",
        "search:",
    ]
    if not results:
        lines.append("(no results)")
    for index, item in enumerate(results, 1):
        lines.append(f"{index}. {item.get('title') or '(no title)'}")
        if item.get("snippet"):
            lines.append(f"   {item['snippet']}")
        if item.get("url"):
            lines.append(f"   {item['url']}")
    return "\n".join(lines)


def instant_answer_text(payload: dict) -> str:
    """The sentence or short list a person should read. Empty payloads say so."""
    if not isinstance(payload, dict):
        return "DuckDuckGo had no instant answer for that."

    answer = _plain(payload.get("Answer"))
    if answer:
        return _clip(answer, "")

    abstract = _plain(payload.get("AbstractText") or payload.get("Abstract"))
    if abstract:
        return _clip(
            abstract,
            _footer(payload.get("AbstractSource"), payload.get("AbstractURL")),
        )

    definition = _plain(payload.get("Definition"))
    if definition:
        return _clip(
            definition,
            _footer(payload.get("DefinitionSource"), payload.get("DefinitionURL")),
        )

    related = _related_lines(payload.get("RelatedTopics"))
    if related:
        heading = _plain(payload.get("Heading"))
        body = "\n".join(f"- {line}" for line in related)
        if heading:
            body = f"{heading}\n{body}"
        return _clip(body, "")

    return "DuckDuckGo had no instant answer for that."


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


def _fetch_lite_sync(query: str) -> str:
    """Lite results via urllib. aiohttp is served DuckDuckGo's bot check."""
    url = _LITE + "?" + urlencode({"q": query})
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _BROWSER,
            "Accept": "text/html",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        page = resp.read().decode("utf-8", "replace")
    if "result-link" not in page and "anomaly" in page.lower():
        raise RuntimeError("DuckDuckGo asked for a human check")
    return page


async def fetch_lite(query: str) -> str:
    """HTML of the lite results page. Raises on transport, HTTP, or a bot check."""
    return await asyncio.to_thread(_fetch_lite_sync, query)


async def fetch_instant_answer(query: str) -> dict:
    """JSON from the Instant Answer API. Raises on transport or HTTP failure."""
    timeout = aiohttp.ClientTimeout(total=8)
    headers = {"User-Agent": "Eche/1.0 (Discord bot; DuckDuckGo Instant Answer)"}
    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
        "no_redirect": "1",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(_API, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    if not isinstance(data, dict):
        return {}
    return data


async def lookup_instant(ctx: ToolContext, arguments: dict) -> ToolResult:
    """Run the query and return the instant answer as the reply."""
    del ctx
    query = _query_text(arguments)
    if not query:
        return ToolResult(
            text="Tell me what to look up.",
            fence=False,
            detail="query: (empty)",
        )
    payload = None
    instant_error = None
    try:
        payload = await fetch_instant_answer(query)
    except Exception as exc:
        instant_error = exc
        dprint(f"[tools] duckduckgo instant failed: {exc}")
    if payload and has_instant(payload):
        sent = instant_answer_text(payload)
        return ToolResult(
            text=sent,
            fence=False,
            detail=debug_payload(query, payload, sent),
            for_model=True,
        )
    try:
        results = parse_lite_results(await fetch_lite(query))
    except Exception as exc:
        dprint(f"[tools] duckduckgo search failed: {exc}")
        detail = f"query: {query}\nsearch error: {type(exc).__name__}: {exc}"
        if instant_error is not None:
            detail += f"\ninstant error: {type(instant_error).__name__}: {instant_error}"
        text = "I couldn't reach DuckDuckGo."
        if "human check" in str(exc):
            text = "DuckDuckGo blocked that lookup."
        return ToolResult(text=text, fence=False, detail=detail, for_model=True)
    sent = search_answer(results)
    return ToolResult(
        text=sent,
        fence=False,
        detail=search_detail(query, results, sent),
        for_model=True,
    )


def register_duckduckgo_tool() -> None:
    register(
        Tool(
            name="duckduckgo",
            description=_DESCRIPTION,
            handler=lookup_instant,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The question or lookup terms to send to DuckDuckGo.",
                    },
                },
                "required": ["query"],
            },
            access=ACCESS_ANYONE,
        )
    )


register_duckduckgo_tool()
