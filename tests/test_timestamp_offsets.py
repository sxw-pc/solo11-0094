from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tzgate.errors import TimestampParseError
from tzgate.offsets import OffsetSyntaxError, format_offset, parse_offset
from tzgate.timestamp import parse_timestamp


@pytest.mark.parametrize(
    ("text", "offset_minutes", "wall", "instant"),
    [
        ("2024-03-10T07:00:00Z", 0, datetime(2024, 3, 10, 7), datetime(2024, 3, 10, 7)),
        (
            "2024-05-01T08:00:00+08:00",
            480,
            datetime(2024, 5, 1, 8),
            datetime(2024, 5, 1, 0),
        ),
        ("2024-11-03T01:30:00-04:00", -240, datetime(2024, 11, 3, 1, 30), datetime(2024, 11, 3, 5, 30)),
        ("2024-03-10 02:30:00", None, datetime(2024, 3, 10, 2, 30), None),
    ],
)
def test_iso8601(text, offset_minutes, wall, instant):
    parsed = parse_timestamp(text, "iso8601")
    assert parsed.wall == wall
    if offset_minutes is None:
        assert parsed.offset is None
        assert parsed.instant_utc is None
    else:
        assert parsed.offset == timedelta(minutes=offset_minutes)
        assert parsed.instant_utc == instant.replace(tzinfo=timezone.utc)


def test_strptime_format():
    parsed = parse_timestamp("2024/05/01 08:00:00 +0800", "%Y/%m/%d %H:%M:%S %z")
    assert parsed.offset == timedelta(hours=8)
    assert parsed.instant_utc == datetime(2024, 5, 1, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("text", ["", "   ", "not-a-time", "2024-13-01T00:00:00Z"])
def test_parse_errors(text):
    with pytest.raises(TimestampParseError):
        parse_timestamp(text, "iso8601")


def test_offsets_round_trip():
    for text, delta, formatted in [
        ("+00:00", timedelta(0), "+00:00"),
        ("-05:00", timedelta(hours=-5), "-05:00"),
        ("+09:30", timedelta(hours=9, minutes=30), "+09:30"),
        ("+0830", timedelta(hours=8, minutes=30), "+08:30"),
        ("Z", timedelta(0), "+00:00"),
    ]:
        assert parse_offset(text) == delta
        assert format_offset(delta) == formatted


@pytest.mark.parametrize("text", ["", "08:00", "+24:00", "+08:60", "UTC"])
def test_offset_errors(text):
    with pytest.raises(OffsetSyntaxError):
        parse_offset(text)
