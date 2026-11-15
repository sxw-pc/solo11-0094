from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tzgate.errors import RulebookError
from tzgate.io_utils import sha256_file
from tzgate.rulebook import load_rulebook


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _load(root: Path, text: str):
    path = root / "rules.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return load_rulebook(path, sha256_file(path))


@pytest.fixture(scope="module")
def book(repo_root: Path):
    path = repo_root / "contracts" / "timezone_rules.yaml"
    return load_rulebook(path, sha256_file(path))


def test_transition_offsets(book):
    ny = book.zones["America/New_York"]
    assert ny.offset_at_utc(_utc("2024-03-10T06:59:59Z")).total_seconds() == -5 * 3600
    assert ny.offset_at_utc(_utc("2024-03-10T07:00:00Z")).total_seconds() == -4 * 3600
    assert ny.offset_at_utc(_utc("2024-11-03T05:59:59Z")).total_seconds() == -4 * 3600
    assert ny.offset_at_utc(_utc("2024-11-03T06:00:00Z")).total_seconds() == -5 * 3600
    assert ny.offset_strings == ("-04:00", "-05:00")
    sydney = book.zones["Australia/Sydney"]
    assert sydney.offset_at_utc(_utc("2024-01-01T00:00:00Z")).total_seconds() == 11 * 3600
    assert sydney.offset_at_utc(_utc("2024-07-01T00:00:00Z")).total_seconds() == 10 * 3600


def test_local_gap_and_fold(book):
    ny_gap = book.zones["America/New_York"].resolve_local(datetime(2024, 3, 10, 2, 30))
    assert ny_gap.kind == "gap"
    assert ny_gap.candidates == ()
    assert ny_gap.window_start == datetime(2024, 3, 10, 2, 0)
    assert ny_gap.window_end == datetime(2024, 3, 10, 3, 0)

    ny_fold = book.zones["America/New_York"].resolve_local(datetime(2024, 11, 3, 1, 30))
    assert ny_fold.kind == "ambiguous"
    offsets = [item.offset.total_seconds() for item in ny_fold.candidates]
    assert offsets == [-4 * 3600, -5 * 3600]
    instants = [item.instant_utc for item in ny_fold.candidates]
    assert instants == [_utc("2024-11-03T05:30:00Z"), _utc("2024-11-03T06:30:00Z")]

    sydney = book.zones["Australia/Sydney"]
    assert sydney.resolve_local(datetime(2024, 4, 7, 2, 30)).kind == "ambiguous"
    assert sydney.resolve_local(datetime(2024, 10, 6, 2, 30)).kind == "gap"


def test_normal_wall_and_local_date(book):
    shanghai = book.zones["Asia/Shanghai"]
    assert shanghai.resolve_local(datetime(2024, 5, 1, 12, 0)).kind == "normal"
    berlin = book.zones["Europe/Berlin"]
    assert berlin.local_date_for_instant(_utc("2024-03-09T23:30:00Z")).isoformat() == "2024-03-10"


@pytest.mark.parametrize(
    "text",
    [
        "version: 1\nzones: {}\n",
        "version: 2\nzones:\n  UTC: {base_offset: '+00:00'}\n",
        "version: 1\nzones:\n  UTC: {base_offset: '24:00'}\n",
        "version: 1\nzones:\n  UTC:\n    base_offset: '+01:00'\n    transitions:\n      - {at_utc: '2024-01-01T00:00:00Z', offset: '+01:00'}\n",
        "version: 1\nzones:\n  UTC:\n    base_offset: '+01:00'\n    transitions:\n      - {at_utc: '2024-02-01T00:00:00Z', offset: '+02:00'}\n      - {at_utc: '2024-01-01T00:00:00Z', offset: '+01:00'}\n",
        "version: 1\nzones:\n  UTC: {base_offset: '+00:00', surprise: true}\n",
    ],
)
def test_invalid_rulebooks(tmp_path: Path, text: str):
    with pytest.raises(RulebookError):
        _load(tmp_path, text)


def test_hash_mismatch(tmp_path: Path, book):
    path = tmp_path / "rules.yaml"
    path.write_text("version: 1\nzones:\n  UTC: {base_offset: '+00:00'}\n")
    with pytest.raises(RulebookError):
        load_rulebook(path, "0" * 64)
