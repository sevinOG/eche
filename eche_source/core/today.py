# core/today.py
# The date stamped onto chat lines and shown to the model.

from datetime import datetime


def today_stamp() -> str:
    """Tuesday, October 6, 2026. Local time on the machine running Eche."""
    now = datetime.now()
    return f"{now.strftime('%A, %B')} {now.day}, {now.year}"


def today_day() -> str:
    """2026-10-06, for a line in the stored chat log."""
    return datetime.now().strftime("%Y-%m-%d")
