from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tzgate.checks.partition import evaluate_dual_partition
from tzgate.io_utils import sha256_file
from tzgate.rulebook import load_rulebook
from tzgate.timestamp import parse_timestamp

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ny():
    path = ROOT / "contracts" / "timezone_rules.yaml"
    return load_rulebook(path, sha256_file(path)).zones["America/New_York"]


def _dual(ny, event_text, arrival_text, basis="event_local_date"):
    return evaluate_dual_partition(
        ny, ny, parse_timestamp(event_text, "iso8601"), "local_wall",
        parse_timestamp(arrival_text, "iso8601"), basis, timedelta(hours=2))


def test_candidates_carry_individual_lags(ny):
    result = _dual(ny, "2024-11-03T01:30:00", "2024-11-03T03:00:00-05:00")
    assert result["verdict"] == "indeterminate" and result["event_utc"] is None
    assert [(c["utc"], c["lag_seconds"]) for c in result["candidates"]] == [
        ("2024-11-03T05:30:00Z", 9000), ("2024-11-03T06:30:00Z", 5400)
    ]


def test_route_depends_only_on_basis(ny):
    event = _dual(ny, "2024-11-03T01:30:00", "2024-11-03T03:00:00-05:00")
    arrival = _dual(ny, "2024-11-03T01:30:00", "2024-11-03T03:00:00-05:00", "arrival_local_date")
    assert event["expected"] == arrival["event_date"]
    assert arrival["expected"] == arrival["arrival_date"]
    assert event["verdict"] == arrival["verdict"] == "indeterminate"


def test_gap_and_normal_wall(ny):
    gap = _dual(ny, "2024-03-10T02:30:00", "2024-03-10T07:00:00Z")
    assert (gap["verdict"], gap["candidates"]) == ("gap", [])
    normal = _dual(ny, "2024-11-04T01:30:00", "2024-11-04T02:00:00-05:00")
    assert normal["verdict"] == "all_compliant"
    assert normal["event_utc"] == "2024-11-04T06:30:00Z"
    assert normal["candidates"][0]["lag_seconds"] == 1800
