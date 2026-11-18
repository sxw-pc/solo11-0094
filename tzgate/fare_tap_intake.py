"""Read verified tap rows and make a preliminary local transfer quote."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .discovery import discover_dataset
from .engine import run_verification
from .errors import SettlementError
from .io_utils import sha256_bytes
from .timestamp import parse_timestamp

TERM_KEYS = {
    "dataset",
    "transfer_window_minutes",
    "eligible_pairs",
    "tap_reuse_limit",
    "discount_percent_of_second_leg",
    "municipal_discount_budget_cents",
    "operator_discount_budget_cents",
    "contracts",
}
CONTRACT_OBJECTIVES = {
    "aggregate_relief": ("savings_cents", "riders_served"),
    "coverage_first": ("riders_served", "savings_cents"),
}


def validate_terms(terms: dict) -> None:
    """Reject intake terms that cannot support an exact integer clearing."""

    unknown = sorted(set(terms) - TERM_KEYS)
    if unknown:
        raise SettlementError(f"unknown intake term(s): {', '.join(unknown)}")
    window = terms.get("transfer_window_minutes")
    if not isinstance(window, int) or isinstance(window, bool) or window < 0:
        raise SettlementError("transfer_window_minutes must be a non-negative integer")
    if terms.get("eligible_pairs") != "adjacent_consecutive_taps_only":
        raise SettlementError("only adjacent_consecutive_taps_only eligibility is supported")
    if terms.get("tap_reuse_limit") != 1:
        raise SettlementError("tap_reuse_limit must be 1")
    percent = terms.get("discount_percent_of_second_leg")
    if not isinstance(percent, int) or isinstance(percent, bool) or not 0 <= percent <= 100:
        raise SettlementError("discount_percent_of_second_leg must be an integer in [0, 100]")
    municipal = terms.get("municipal_discount_budget_cents")
    if not isinstance(municipal, int) or isinstance(municipal, bool) or municipal < 0:
        raise SettlementError("municipal budget must be a non-negative integer")
    operator_budgets = terms.get("operator_discount_budget_cents")
    if not isinstance(operator_budgets, dict) or not operator_budgets:
        raise SettlementError("operator_discount_budget_cents must be a non-empty mapping")
    for operator, budget in operator_budgets.items():
        if not isinstance(operator, str) or not operator:
            raise SettlementError("operator budget keys must be non-empty strings")
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
            raise SettlementError(f"operator budget for {operator} must be a non-negative integer")
    contracts = terms.get("contracts")
    if not isinstance(contracts, dict) or set(contracts) != set(CONTRACT_OBJECTIVES):
        raise SettlementError(
            "intake must pre-authorize exactly the contracts: "
            + ", ".join(sorted(CONTRACT_OBJECTIVES))
        )


def load_fare_taps(contract_path: Path, root: Path, intake_path: Path, run_id: str) -> dict:
    intake = json.loads(intake_path.read_text(encoding="utf-8"))
    validate_terms(intake)
    run = run_verification(contract_path, root, run_id)
    if run.status != "pass" or len(run.datasets) != 1:
        raise SettlementError("fare tap dataset must pass timezone and partition checks")
    dataset = run.datasets[0]
    if dataset.spec.name != intake["dataset"] or dataset.issue_count:
        raise SettlementError("verified dataset and fare intake disagree")
    known_operators = set(intake["operator_discount_budget_cents"])
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
                    raise SettlementError("duplicate or blank tap_id")
                seen.add(tap_id)
                rider_id = row["rider_id"]
                if not rider_id:
                    raise SettlementError("blank rider_id")
                operator = row["operator"]
                if operator not in known_operators:
                    raise SettlementError(f"unknown operator: {operator}")
                instant = parse_timestamp(row["event_time"], "iso8601").instant_utc
                if instant is None:
                    raise SettlementError("tap timestamp must have an offset")
                if instant.date() != ref.partition_date:
                    raise SettlementError(
                        f"tap {tap_id} UTC date {instant.date().isoformat()} does not belong to "
                        f"partition {ref.partition_date.isoformat()}"
                    )
                fare = int(row["base_fare_cents"])
                if fare <= 0:
                    raise SettlementError("tap fare must be positive")
                rows.append({"tap_id": tap_id, "rider_id": rider_id,
                             "event_time": row["event_time"], "instant_utc": instant.isoformat(),
                             "operator": operator, "base_fare_cents": fare,
                             "partition_date": ref.partition_date.isoformat()})
    if len(rows) != dataset.record_count:
        raise SettlementError("verified tap count changed")
    return {"run_id": run_id, "contract_sha256": run.contract.sha256,
            "rulebook_sha256": run.contract.rulebook_sha256,
            "terms_sha256": sha256_bytes(intake_path.read_bytes()),
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
