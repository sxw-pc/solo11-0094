from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tzgate.contract import load_contract
from tzgate.engine import run_verification
from tzgate.errors import ContractError
from tzgate.pipeline import write_outputs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def event_run():
    return run_verification(ROOT / "contracts/dual_event.yaml", ROOT, "dual-event")


@pytest.fixture(scope="module")
def arrival_run():
    return run_verification(ROOT / "contracts/dual_arrival.yaml", ROOT, "dual-arrival")


def _issues(run, dataset, code=None):
    return [i for i in run.issues
            if i.dataset == dataset and (code is None or i.code == code)]


def _row(run, dataset, line):
    return next(i for i in _issues(run, dataset) if i.line == line)


def test_dual_contract_parses_both_clocks(tmp_path):
    spec = load_contract(ROOT / "contracts/dual_event.yaml", ROOT).datasets[0]
    assert spec.time.dual and spec.time.arrival.column == "arrival_time"
    assert spec.time.max_arrival_lag.total_seconds() == 18000
    assert spec.partition.basis == "event_local_date"
    single = load_contract(ROOT / "contracts/default.yaml", ROOT).datasets[0]
    assert not single.time.dual and single.time.max_arrival_lag is None
    text = (ROOT / "contracts/dual_event.yaml").read_text(encoding="utf-8")
    path = tmp_path / "bad.yaml"
    for old, new in (("basis: event_local_date", "basis: zone_local_date"),
                     ("max_arrival_lag: 2h", "max_arrival_lag: 0s")):
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        with pytest.raises(ContractError):
            load_contract(path, ROOT)


def test_cross_day_flips_with_partition_basis(event_run, arrival_run):
    event = _issues(event_run, "cross_day", "TZ-PART-001")[0]
    arrival = _issues(arrival_run, "cross_day", "TZ-PART-001")[0]
    assert event.raw == arrival.raw == "2024-11-03T23:30:00"
    assert "dt=2024-11-04" in event.file
    assert event.details["event_local_date"] == "2024-11-03"
    assert "dt=2024-11-03" in arrival.file
    assert arrival.details["expected_local_date"] == "2024-11-04"


def test_fold_evidence_covers_every_candidate(event_run, arrival_run):
    indeterminate = _row(event_run, "fold", 3)
    assert indeterminate.details["lag_verdict"] == "indeterminate"
    assert [(c["offset"], c["lag_seconds"], c["state"])
            for c in indeterminate.details["candidates"]] == [
        ("-04:00", 9000, "late"), ("-05:00", 5400, "ok")]
    assert indeterminate.details["event_utc"] is None
    assert 2 not in {i.line for i in _issues(event_run, "fold")}  # both candidates compliant
    assert _row(event_run, "fold", 4).code == "TZ-LAG-LATE"
    assert _row(arrival_run, "fold", 3).code == "TZ-PART-LAG-002"
    assert _row(arrival_run, "fold", 4).code == "TZ-PART-LAG-001"


def test_gap_and_order_never_become_partition_errors(event_run, arrival_run):
    for run in (event_run, arrival_run):
        assert [i.code for i in _issues(run, "gap")] == ["TZ-DST-GAP"]
        assert _row(run, "fold", 5).code == "TZ-LAG-ORDER"
        assert not _issues(run, "fold", "TZ-PART-001")
        assert not _issues(run, "gap", "TZ-PART-001")


def test_both_clock_errors_are_separate_and_route_is_recorded(event_run, tmp_path):
    codes = {(i.code, i.column) for i in _issues(event_run, "bad_clocks")}
    assert {("TZ-PARSE-001", "event_time"), ("TZ-PARSE-001", "arrival_time"),
            ("TZ-SEM-002", "arrival_time")} <= codes
    with sqlite3.connect(write_outputs(event_run, tmp_path / "out").ledger) as conn:
        rows = conn.execute(
            "SELECT name, partition_basis, arrival_column FROM dataset_clocks"
        ).fetchall()
    assert ("cross_day", "event_local_date", "arrival_time") in rows


def test_missing_clock_columns_are_shape_errors(event_run):
    shape = [i for i in event_run.issues if i.code == "TZ-SHAPE-001"]
    assert {(i.dataset, i.column) for i in shape} == {
        ("no_arrival_column", "arrival_time"), ("no_event_column", "event_time")}


def test_legacy_ledger_is_unchanged(tmp_path, run_result):
    with sqlite3.connect(write_outputs(run_result, tmp_path / "legacy").ledger) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "dataset_clocks" not in tables
