"""Cross-operator transfer subsidy clearing over a verified fare-tap lake.

Everything here is offline: no collection or payment endpoint is called. The
clearing binds a specific verification run (run id, contract SHA, rulebook
SHA, every lake file SHA and intake SHA), splits taps by their verified
partition day, enumerates only adjacent cross-operator candidate transfers,
and selects the globally optimal feasible set under the municipal and
operator budgets for the two pre-authorized contracts.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .errors import SettlementError, StaleSettlementError
from .fare_tap_intake import CONTRACT_OBJECTIVES, validate_terms
from .io_utils import sha256_bytes
from .settlement_evidence import certify_unselected, edge_record, lex_tie_evidence
from .settlement_opt import OBJECTIVES, TransferEdge, optimize

MAX_TAPS = 30
MAX_RIDERS = 10
MAX_OPERATORS = 3
MAX_DAYS = 4
_BINDING_KEYS = ("run_id", "contract_sha256", "rulebook_sha256",
                 "terms_sha256", "files", "taps", "terms")


def _minutes_between(first: str, second: str) -> int:
    delta = datetime.fromisoformat(second) - datetime.fromisoformat(first)
    return delta.days * 24 * 60 + delta.seconds // 60


def _check_binding(data: dict, contract_path, intake_path, root) -> None:
    missing = [key for key in _BINDING_KEYS if key not in data]
    if missing:
        raise SettlementError("settlement input is missing binding fields: "
                              + ", ".join(sorted(missing)))
    validate_terms(data["terms"])
    if sha256_bytes(contract_path.read_bytes()) != data["contract_sha256"]:
        raise StaleSettlementError(
            "contract changed since verification; rerun verification, do not settle on a stale run")
    from .contract import load_contract  # local import keeps modules independent
    contract = load_contract(contract_path, root)
    if contract.rulebook_sha256 != data["rulebook_sha256"]:
        raise StaleSettlementError("rulebook changed since verification; rerun verification")
    if sha256_bytes(intake_path.read_bytes()) != data["terms_sha256"]:
        raise StaleSettlementError("intake terms changed; rerun the verified intake")
    # Re-hash every lake file from disk; a changed SHA voids the old settlement.
    for entry in sorted(data["files"], key=lambda item: item["path"]):
        live = root / entry["path"]
        if not live.is_file() or sha256_bytes(live.read_bytes()) != entry["sha256"]:
            raise StaleSettlementError(
                f"lake file {entry['path']} changed since verification; old settlement is void")


def build_candidates(day_taps: list[dict], terms: dict) -> tuple[
        tuple[TransferEdge, ...], list[dict]]:
    """Enumerate adjacent eligible transfers plus an audit of every rider pair."""

    window = terms["transfer_window_minutes"]
    percent = terms["discount_percent_of_second_leg"]
    edges: list[TransferEdge] = []
    audit: list[dict] = []
    by_rider: dict[str, list[dict]] = defaultdict(list)
    for tap in day_taps:
        by_rider[tap["rider_id"]].append(tap)
    for rider in sorted(by_rider):
        taps = sorted(by_rider[rider], key=lambda tap: (tap["instant_utc"], tap["tap_id"]))
        for first, second in zip(taps, taps[1:]):
            gap = _minutes_between(first["instant_utc"], second["instant_utc"])
            if first["operator"] == second["operator"]:
                reason = "same_operator"
            elif not (0 <= gap <= window):
                reason = "outside_transfer_window"
            else:
                reason = "eligible"
            audit.append({"rider_id": rider, "from_tap": first["tap_id"],
                          "to_tap": second["tap_id"], "gap_minutes": gap,
                          "adjacent": True, "reason": reason})
            if reason == "eligible":
                discount = second["base_fare_cents"] * percent // 100
                edges.append(TransferEdge(
                    rider_id=rider, from_tap=first["tap_id"], to_tap=second["tap_id"],
                    from_time=first["instant_utc"], to_time=second["instant_utc"],
                    second_operator=second["operator"], gap_minutes=gap,
                    discount_cents=discount,
                    partition_date=second["partition_date"]))
        for left in range(len(taps)):
            for right in range(left + 2, len(taps)):
                first, second = taps[left], taps[right]
                gap = _minutes_between(first["instant_utc"], second["instant_utc"])
                audit.append({"rider_id": rider, "from_tap": first["tap_id"],
                              "to_tap": second["tap_id"], "gap_minutes": gap,
                              "adjacent": False, "reason": "non_adjacent_pair"})
    audit.sort(key=lambda item: (item["rider_id"], item["from_tap"], item["to_tap"]))
    return tuple(edges), audit


def _check_day_limits(day: str, day_taps: list[dict], terms: dict) -> None:
    if len(day_taps) > MAX_TAPS:
        raise SettlementError(f"day {day} has {len(day_taps)} taps; limit is {MAX_TAPS}")
    if len({tap["rider_id"] for tap in day_taps}) > MAX_RIDERS:
        raise SettlementError(f"day {day} exceeds {MAX_RIDERS} riders")
    operators = {tap["operator"] for tap in day_taps}
    if len(operators) > MAX_OPERATORS:
        raise SettlementError(f"day {day} exceeds {MAX_OPERATORS} operators")
    unknown = sorted(operators - set(terms["operator_discount_budget_cents"]))
    if unknown:
        raise SettlementError(f"unknown operator(s) on {day}: {', '.join(unknown)}")


def _summarize(day_taps, edges, optimal, municipal, operator_budgets, terms,
               objective, data):
    chosen = frozenset(optimal.selected)
    redeemed = sorted((edge_record(edges[i]) for i in optimal.selected),
                      key=lambda record: (record["from_tap"], record["to_tap"]))
    unselected = [
        certify_unselected(i, edges, chosen, terms, objective,
                           optimal.objective_vector(objective))
        for i in range(len(edges)) if i not in chosen
    ]
    unselected.sort(key=lambda item: (item["edge"]["from_tap"], item["edge"]["to_tap"]))
    discount_by_rider: dict[str, int] = defaultdict(int)
    discount_by_operator: dict[str, int] = defaultdict(int)
    for i in optimal.selected:
        discount_by_rider[edges[i].rider_id] += edges[i].discount_cents
        discount_by_operator[edges[i].second_operator] += edges[i].discount_cents
    base_by_rider: dict[str, int] = defaultdict(int)
    base_by_operator: dict[str, int] = defaultdict(int)
    for tap in day_taps:
        base_by_rider[tap["rider_id"]] += tap["base_fare_cents"]
        base_by_operator[tap["operator"]] += tap["base_fare_cents"]
    rider_bills = [
        {"rider_id": rider, "base_fare_cents": base_by_rider[rider],
         "discount_cents": discount_by_rider.get(rider, 0),
         "paid_cents": base_by_rider[rider] - discount_by_rider.get(rider, 0),
         "served": discount_by_rider.get(rider, 0) > 0}
        for rider in sorted(base_by_rider)]
    operator_ledger = [
        {"operator": op, "base_fare_cents": base_by_operator[op],
         "second_leg_discount_cents": discount_by_operator.get(op, 0),
         "net_receipt_cents": base_by_operator[op] - discount_by_operator.get(op, 0)}
        for op in sorted(set(operator_budgets) | set(base_by_operator))]
    budgets = {
        "municipal": {"limit_cents": municipal, "used_cents": optimal.savings_cents,
                      "remaining_cents": municipal - optimal.savings_cents},
        "operator": [
            {"operator": op, "limit_cents": operator_budgets[op],
             "used_cents": discount_by_operator.get(op, 0),
             "remaining_cents": operator_budgets[op] - discount_by_operator.get(op, 0)}
            for op in sorted(operator_budgets)]}
    total_base = sum(base_by_rider.values())
    total_paid = sum(item["paid_cents"] for item in rider_bills)
    total_net = sum(item["net_receipt_cents"] for item in operator_ledger)
    if total_paid != total_base - optimal.savings_cents:
        raise SettlementError("rider conservation failed")
    if total_net != total_base - optimal.savings_cents:
        raise SettlementError("operator conservation failed")
    if sum(discount_by_operator.get(op, 0) for op in operator_budgets) != optimal.savings_cents:
        raise SettlementError("municipal subsidy conservation failed")
    return {
        "objective": objective,
        "objective_description": data["terms"]["contracts"][objective],
        "redeemed_edges": redeemed, "unselected_edges": unselected,
        "rider_bills": rider_bills, "operator_ledger": operator_ledger,
        "budgets": budgets,
        "objective_value": {"savings_cents": optimal.savings_cents,
                            "riders_served": optimal.riders_served},
        "tie_evidence": lex_tie_evidence(edges, optimal.selected, terms, objective, optimal),
        "search": {"states_explored": optimal.states_explored,
                   "memoized_states": optimal.memoized_states},
        "conservation": {"total_base_fare_cents": total_base,
                         "total_paid_cents": total_paid,
                         "total_net_receipt_cents": total_net,
                         "municipal_used_cents": optimal.savings_cents}}


def _clear_day(day: str, day_taps: list[dict], data: dict) -> dict:
    terms = data["terms"]
    _check_day_limits(day, day_taps, terms)
    edges, pair_audit = build_candidates(day_taps, terms)
    municipal = terms["municipal_discount_budget_cents"]
    operator_budgets = terms["operator_discount_budget_cents"]
    contracts_out: dict[str, dict] = {}
    for objective in OBJECTIVES:
        optimal = optimize(edges, municipal, operator_budgets, objective)
        if not optimal.feasible:
            raise SettlementError(f"{objective} optimizer reported infeasibility on {day}")
        contracts_out[objective] = _summarize(
            day_taps, edges, optimal, municipal, operator_budgets, terms, objective, data)
    return {"partition_date": day, "tap_count": len(day_taps),
            "candidate_edge_count": len(edges),
            "pair_audit": pair_audit, "contracts": contracts_out}


def settle_transfers(data: dict, contract_path, intake_path, root=None) -> dict:
    """Run both authorized clearing contracts over every verified partition day."""

    root = root if root is not None else contract_path.resolve().parents[1]
    _check_binding(data, contract_path, intake_path, root)
    terms = data["terms"]
    if set(terms["contracts"]) != set(CONTRACT_OBJECTIVES):
        raise SettlementError("settlement requires the two pre-authorized contracts")
    days: dict[str, list[dict]] = defaultdict(list)
    for tap in data["taps"]:
        days[tap["partition_date"]].append(tap)
    if len(days) > MAX_DAYS:
        raise SettlementError(f"{len(days)} clearing days; limit is {MAX_DAYS}")
    return {
        "run_id": data["run_id"],
        "binding": {
            "contract_sha256": data["contract_sha256"],
            "rulebook_sha256": data["rulebook_sha256"],
            "terms_sha256": data["terms_sha256"],
            "files": [dict(entry) for entry in sorted(data["files"], key=lambda x: x["path"])]},
        "terms": terms,
        "days": [_clear_day(day, days[day], data) for day in sorted(days)]}
