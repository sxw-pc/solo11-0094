"""Fixed UTC-offset parsing and formatting.

The rulebook deliberately stores offsets as explicit ``+HH:MM`` strings.
Parsing them ourselves keeps the engine independent of the host's tzdata.
"""

from __future__ import annotations

import re
from datetime import timedelta

_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")

class OffsetSyntaxError(ValueError):
    """Raised when an offset is not a minute-granularity UTC offset."""

def parse_offset(text: str) -> timedelta:
    """Parse ``Z``, ``+HH:MM``, ``-HH:MM``, ``+HHMM`` or ``-HHMM``."""

    if not isinstance(text, str):
        raise OffsetSyntaxError("offset must be a string")
    value = text.strip()
    if value in {"Z", "z"}:
        return timedelta(0)
    match = _OFFSET_RE.fullmatch(value)
    if match is None:
        raise OffsetSyntaxError(f"invalid UTC offset: {text!r}")
    sign, hours_text, minutes_text = match.groups()
    hours = int(hours_text)
    minutes = int(minutes_text)
    if hours > 23 or minutes > 59:
        raise OffsetSyntaxError(f"UTC offset out of range: {text!r}")
    delta = timedelta(hours=hours, minutes=minutes)
    return -delta if sign == "-" else delta

def format_offset(offset: timedelta) -> str:
    """Format a minute-granularity offset as ``+HH:MM`` or ``-HH:MM``."""

    total_seconds = int(offset.total_seconds())
    if total_seconds % 60 != 0:
        raise OffsetSyntaxError("offset has sub-minute precision")
    sign = "+" if total_seconds >= 0 else "-"
    total_minutes = abs(total_seconds) // 60
    if total_minutes >= 24 * 60:
        raise OffsetSyntaxError("offset is outside the supported range")
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"

