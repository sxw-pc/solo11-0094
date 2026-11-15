"""Timestamp parsing with explicit wall-clock and instant representations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .errors import TimestampParseError


@dataclass(frozen=True)
class ParsedTimestamp:
    text: str
    wall: datetime
    instant_utc: datetime | None
    offset: timedelta | None

    @property
    def has_offset(self) -> bool:
        return self.instant_utc is not None


def parse_timestamp(text: str, date_format: str) -> ParsedTimestamp:
    value = text.strip()
    if not value:
        raise TimestampParseError("timestamp is empty")
    try:
        if date_format.lower() == "iso8601":
            moment = _parse_iso8601(value)
        else:
            moment = datetime.strptime(value, date_format)
    except ValueError as exc:
        raise TimestampParseError(str(exc)) from exc
    if moment.tzinfo is not None and moment.utcoffset() is not None:
        offset = moment.utcoffset()
        if offset is None or offset.total_seconds() % 60 != 0:
            raise TimestampParseError("explicit offset must have minute granularity")
        return ParsedTimestamp(
            text=value,
            wall=moment.replace(tzinfo=None),
            instant_utc=moment.astimezone(timezone.utc),
            offset=offset,
        )
    return ParsedTimestamp(
        text=value,
        wall=moment.replace(tzinfo=None),
        instant_utc=None,
        offset=None,
    )


def _parse_iso8601(value: str) -> datetime:
    normalized = value
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)
