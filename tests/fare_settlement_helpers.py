"""Helpers that materialize isolated synthetic fare-tap lakes for clearing tests.

Each test lake is built in its own empty temporary directory; nothing here
touches the committed fixtures. Values are fabricated integers only.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

HEADER = ("tap_id", "rider_id", "event_time", "gate_id", "operator", "base_fare_cents")
DEFAULT_TERMS = {
    "dataset": "fare_taps",
    "transfer_window_minutes": 60,
    "eligible_pairs": "adjacent_consecutive_taps_only",
    "tap_reuse_limit": 1,
    "discount_percent_of_second_leg": 50,
    "municipal_discount_budget_cents": 180,
    "operator_discount_budget_cents": {"bus": 160, "ferry": 180, "rail": 0},
    "contracts": {
        "aggregate_relief": "maximize total rider fare savings, then riders served",
        "coverage_first": "maximize number of riders with at least one discount, then total savings",
    },
}


def write_lake(base: Path, rows_by_day: dict[str, list[tuple]]) -> None:
    """Write ``dt=<day>/part-0001.csv`` files under ``base/fixtures/lake/...``."""

    for day, rows in rows_by_day.items():
        directory = base / "fixtures" / "lake" / "fare_taps" / f"dt={day}"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "part-0001.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(HEADER)
            writer.writerows(rows)


def write_contract(base: Path) -> Path:
    contracts = base / "contracts"
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "contracts",
        contracts,
    )
    return contracts / "fare_taps.yaml"


def write_intake(base: Path, terms: dict | None = None) -> Path:
    path = base / "intake.json"
    path.write_text(json.dumps(terms or DEFAULT_TERMS, indent=2), encoding="utf-8")
    return path


def build_workspace(base: Path, rows_by_day: dict[str, list[tuple]],
                    terms: dict | None = None) -> tuple[Path, Path]:
    contract = write_contract(base)
    intake = write_intake(base, terms)
    write_lake(base, rows_by_day)
    return contract, intake
