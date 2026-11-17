"""Read verified tap rows and make a preliminary local transfer quote."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes
from .timestamp import parse_timestamp


def load_fare_taps(contract_path: Path, root: Path, intake_path: Path, run_id: str) -> dict:
    intake = json.loads(intake_path.read_text(encoding="utf-8"))
    run = run_verification(contract_path, root, run_id)
    if run.status != "pass" or len(run.datasets) != 1:
        raise ValueError("fare tap dataset must pass timezone and partition checks")
    dataset = run.datasets[0]
    if dataset.spec.name != intake["dataset"] or dataset.issue_count:
        raise ValueError("verified dataset and fare intake disagree")
    refs = discover_dataset(dataset.spec).files
    rows: list[dict] = []
    files: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        files.append({"path": ref.label, "sha256": sha256_bytes(ref.path.read_bytes())})
        with ref.path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                tap_id = row["tap_id"]
                if not tap_id or tap_id in seen:
                    raise ValueError("duplicate or blank tap_id")
                seen.add(tap_id)
                instant = parse_timestamp(row["event_time"], "iso8601").instant_utc
                if instant is None:
                    raise ValueError("tap timestamp must have an offset")
                fare = int(row["base_fare_cents"])
                if fare <= 0:
                    raise ValueError("tap fare must be positive")
                rows.append({"tap_id": tap_id, "rider_id": row["rider_id"],
                             "event_time": row["event_time"], "instant_utc": instant.isoformat(),
                             "operator": row["operator"], "base_fare_cents": fare})
    if len(rows) != dataset.record_count:
        raise ValueError("verified tap count changed")
    return {"run_id": run_id, "contract_sha256": run.contract.sha256,
            "rulebook_sha256": run.contract.rulebook_sha256,
            "files": files, "taps": rows, "terms": intake}


def preview_first_transfer(data: dict) -> dict:
    """Independent first adjacent transfer per rider; no shared budget clearing."""
    by_rider: dict[str, list[dict]] = defaultdict(list)
    for tap in data["taps"]:
        by_rider[tap["rider_id"]].append(tap)
    window = data["terms"]["transfer_window_minutes"]
    percent = data["terms"]["discount_percent_of_second_leg"]
    quotes: list[dict] = []
    for rider, taps in sorted(by_rider.items()):
        taps.sort(key=lambda tap: (tap["instant_utc"], tap["tap_id"]))
        for first, second in zip(taps, taps[1:]):
            from datetime import datetime
            gap = (datetime.fromisoformat(second["instant_utc"]) -
                   datetime.fromisoformat(first["instant_utc"])).total_seconds() / 60
            if 0 <= gap <= window and first["operator"] != second["operator"]:
                quotes.append({"rider_id": rider, "from_tap": first["tap_id"],
                               "to_tap": second["tap_id"],
                               "discount_cents": second["base_fare_cents"] * percent // 100})
                break
    return {"quotes": quotes,
            "quoted_discount_cents": sum(q["discount_cents"] for q in quotes),
            "shared_budget_cleared": False}
