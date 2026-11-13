# core/chat_panel.py
# One emit point for the desktop Chat panel (the Discord mirror).
# Inbound user lines and outbound bot lines both go through emit_line.
# Delivery is thread-safe: a lock-protected model, plus an optional
# in-process handler for tests. The GUI also queues stdout events onto
# the Qt thread before appending.

from __future__ import annotations

from threading import Lock
from typing import Callable, Optional

_handler: Optional[Callable[[str], None]] = None
_handler_lock = Lock()


def set_handler(handler: Optional[Callable[[str], None]]) -> None:
    """In-process listener, used by tests and same-process GUI hooks."""
    global _handler
    with _handler_lock:
        _handler = handler


def fold_body(text: str) -> str:
    """Indent continuation lines so a user newline plus 'Bot:' cannot
    look like a separate bot reply in the panel."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.rstrip("\n")
    if "\n" not in raw:
        return raw
    first, *rest = raw.split("\n")
    return first + "\n" + "\n".join("  " + line for line in rest)


def format_inbound(author: str, text: str) -> str:
    who = " ".join(str(author or "User").split()) or "User"
    return f"{who}: {fold_body(text)}"


def format_outbound(text: str) -> str:
    return f"Bot: {fold_body(text)}"


def visible_from_embed(embed) -> str:
    """Plain text from a Discord embed (or a dict-shaped stand-in)."""
    if embed is None:
        return ""
    if isinstance(embed, dict):
        title = embed.get("title") or ""
        desc = embed.get("description") or ""
        fields = embed.get("fields") or []
        parts = [str(title).strip(), str(desc).strip()]
        for field in fields:
            if isinstance(field, dict):
                name = field.get("name") or ""
                value = field.get("value") or ""
            else:
                name = getattr(field, "name", "") or ""
                value = getattr(field, "value", "") or ""
            bit = f"{name}: {value}" if str(name).strip() else str(value)
            if str(bit).strip():
                parts.append(str(bit).strip())
        return "\n".join(p for p in parts if p).strip()
    title = getattr(embed, "title", "") or ""
    desc = getattr(embed, "description", "") or ""
    parts = [str(title).strip(), str(desc).strip()]
    for field in getattr(embed, "fields", None) or []:
        name = getattr(field, "name", "") or ""
        value = getattr(field, "value", "") or ""
        bit = f"{name}: {value}" if str(name).strip() else str(value)
        if str(bit).strip():
            parts.append(str(bit).strip())
    return "\n".join(p for p in parts if p).strip()


def visible_payload(content: str = "", **send_kw) -> str:
    """The text the chat panel should show for a Discord send or edit."""
    text = str(content if content is not None else "")
    if not text.strip():
        text = str(send_kw.get("content") or "")
    if text.strip():
        return text
    embed = send_kw.get("embed")
    if embed is None:
        embeds = send_kw.get("embeds") or []
        embed = embeds[0] if embeds else None
    shown = visible_from_embed(embed)
    if shown.strip():
        return shown
    if send_kw.get("file") or send_kw.get("files"):
        return "[file]"
    return ""


def emit_line(text: str) -> str:
    """Deliver one chat-panel line. Empty or whitespace-only text is ignored."""
    line = str(text or "").rstrip("\n")
    if not line.strip():
        return ""
    try:
        from core.gui_bridge import chat as gui_chat

        gui_chat(line)
    except Exception:
        pass
    handler = None
    with _handler_lock:
        handler = _handler
    if handler is not None:
        try:
            handler(line)
        except Exception:
            pass
    return line


def inbound(author: str, text: str) -> str:
    """Mirror a user message into the chat panel."""
    return emit_line(format_inbound(author, text))


def outbound(text: str) -> str:
    """Mirror a bot message into the chat panel."""
    body = str(text or "")
    if not body.strip():
        return ""
    return emit_line(format_outbound(body))


def author_label(person) -> str:
    """Best display name from a Discord user or similar object."""
    if person is None:
        return "User"
    return (
        getattr(person, "display_name", None)
        or getattr(person, "name", None)
        or "User"
    )


async def send_visible(dest, content: str = "", *, sender=None, **send_kw):
    """Post text to Discord, then mirror it to the chat panel.

    dest is typically a channel, thread, or context with a .send method.
    Pass sender=message.reply or sender=ctx.reply to reply instead.
    The panel is updated only after the Discord send succeeds.
    """
    text = str(content if content is not None else "")
    send = sender if sender is not None else getattr(dest, "send", dest)
    # Pass the body the same way the old call sites did (positional first
    # argument), so existing tests and Discord wrappers keep working.
    if text:
        result = await send(text, **send_kw)
    elif send_kw:
        result = await send(**send_kw)
    else:
        result = await send()
    shown = visible_payload(text, **send_kw)
    if shown.strip():
        outbound(shown)
    return result


async def edit_visible(target, content: str = "", *, editor=None, **edit_kw):
    """Edit a Discord message or interaction response, then mirror the new text."""
    text = str(content if content is not None else "")
    edit = editor
    if edit is None:
        edit = getattr(target, "edit_message", None) or getattr(target, "edit")
    if text:
        result = await edit(content=text, **edit_kw)
    else:
        result = await edit(**edit_kw)
    shown = visible_payload(text, **edit_kw)
    if shown.strip():
        outbound(shown)
    return result


class ChatPanelModel:
    """Thread-safe list of chat lines. The GUI binds this; tests assert on it."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._lock = Lock()
        self._listeners: list[Callable[[str], None]] = []

    def append(self, text: str) -> None:
        line = str(text or "").rstrip("\n")
        if not line:
            return
        with self._lock:
            self._lines.append(line)
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(line)
            except Exception:
                pass

    def lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    def on_append(self, fn: Callable[[str], None]) -> None:
        with self._lock:
            self._listeners.append(fn)
