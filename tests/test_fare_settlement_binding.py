"""Rejection paths and stale-binding invalidation for the subsidy clearing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tzgate.errors import SettlementError, StaleSettlementError
from tzgate.fare_tap_intake import load_fare_taps
from tzgate.settlement import settle_transfers

from fare_settlement_helpers import build_workspace


def _load(base, rows, run_id="run"):
    contract_path, intake_path = build_workspace(base, rows)
    return load_fare_taps(contract_path, base, intake_path, run_id), contract_path, intake_path


def test_duplicate_tap_id_is_rejected(tmp_path):
    rows = {
        "2024-06-01": [
            ("x1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
            ("x1", "R2", "2024-06-01T08:30:00Z", "g2", "bus", 160),
        ]
    }
    with pytest.raises(SettlementError, match="duplicate"):
        _load(tmp_path, rows, "dup")


@pytest.mark.parametrize("fare", [0, -50])
def test_non_positive_fare_is_rejected(tmp_path, fare):
    rows = {
        "2024-06-01": [
            ("z1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
            ("z2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", fare),
        ]
    }
    with pytest.raises(SettlementError, match="fare"):
        _load(tmp_path, rows, "bad-fare")


def test_unknown_operator_is_rejected(tmp_path):
    rows = {"2024-06-01": [
        ("q1", "R1", "2024-06-01T08:00:00Z", "g1", "drone", 100)]}
    with pytest.raises(SettlementError, match="unknown operator"):
        _load(tmp_path, rows, "unknown-op")


def test_utc_date_outside_partition_is_rejected(tmp_path):
    rows = {"2024-06-01": [
        ("u1", "R1", "2024-06-02T00:30:00Z", "g1", "rail", 100)]}
    with pytest.raises(SettlementError, match="partition"):
        _load(tmp_path, rows, "wrong-day")


def test_changed_lake_file_voids_cached_settlement(tmp_path):
    rows = {"2024-06-01": [
        ("v1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
        ("v2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", 160)]}
    data, contract_path, intake_path = _load(tmp_path, rows)
    lake_file = tmp_path / "fixtures" / "lake" / "fare_taps" / "dt=2024-06-01" / "part-0001.csv"
    lake_file.write_text(lake_file.read_text() + "\n", encoding="utf-8")
    with pytest.raises(StaleSettlementError, match="changed since verification"):
        settle_transfers(data, contract_path, intake_path, tmp_path)


def test_changed_contract_voids_cached_settlement(tmp_path):
    rows = {"2024-06-01": [
        ("w1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
        ("w2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", 160)]}
    data, contract_path, intake_path = _load(tmp_path, rows)
    contract_path.write_text(contract_path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(StaleSettlementError, match="contract changed"):
        settle_transfers(data, contract_path, intake_path, tmp_path)


def test_changed_rulebook_voids_cached_settlement(tmp_path):
    rows = {"2024-06-01": [
        ("k1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
        ("k2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", 160)]}
    data, contract_path, intake_path = _load(tmp_path, rows)
    rulebook = tmp_path / "contracts" / "timezone_rules.yaml"
    rulebook.write_text(rulebook.read_text() + "\n", encoding="utf-8")
    with pytest.raises(StaleSettlementError):
        settle_transfers(data, contract_path, intake_path, tmp_path)


def test_changed_intake_terms_voids_cached_settlement(tmp_path):
    rows = {"2024-06-01": [
        ("t1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
        ("t2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", 160)]}
    data, contract_path, intake_path = _load(tmp_path, rows)
    intake_path.write_text(intake_path.read_text().replace("180", "179"), encoding="utf-8")
    with pytest.raises(StaleSettlementError, match="intake terms changed"):
        settle_transfers(data, contract_path, intake_path, tmp_path)


def test_settlement_carries_full_binding(tmp_path):
    rows = {"2024-06-01": [
        ("s1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
        ("s2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", 160)]}
    data, contract_path, intake_path = _load(tmp_path, rows, "bound-run")
    settlement = settle_transfers(data, contract_path, intake_path, tmp_path)
    assert settlement["run_id"] == "bound-run"
    binding = settlement["binding"]
    assert binding["contract_sha256"] == data["contract_sha256"]
    assert binding["rulebook_sha256"] == data["rulebook_sha256"]
    assert binding["terms_sha256"] == data["terms_sha256"]
    assert len(binding["files"]) == 1 and len(binding["files"][0]["sha256"]) == 64
