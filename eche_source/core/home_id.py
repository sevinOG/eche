"""Parse a Discord home-server id without crashing on a pasted link.

Settings ask people to right-click the server icon and copy the server id.
Discord's icon address looks like
https://cdn.discordapp.com/icons/<guild id>/<hash>.webp
and that URL is not an integer. The guild id is the long number in the path.
"""
from __future__ import annotations

import os
import re

# Discord snowflakes are 17–20 digits. Ignore short numbers such as ?size=1024.
_SNOWFLAKE = re.compile(r"(?<!\d)(\d{17,20})(?!\d)")


def parse_home_server_id(raw: str | None) -> int:
    """Return a guild id, or 0 when the text has none."""
    text = (raw or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    match = _SNOWFLAKE.search(text)
    if match:
        return int(match.group(1))
    return 0


def home_server_id_from_env() -> int:
    """Read HOME_SERVER_ID. A Discord icon or channel link still yields the guild id."""
    return parse_home_server_id(os.getenv("HOME_SERVER_ID"))
