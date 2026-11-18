"""Golden clearing outcomes, contract divergence and structural eligibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tzgate.errors import SettlementError
from tzgate.fare_tap_intake import load_fare_taps, preview_first_transfer
from tzgate.settlement import settle_transfers

from fare_settlement_helpers import build_workspace

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_CONTRACT = ROOT / "contracts" / "fare_taps.yaml"
GOLDEN_INTAKE = ROOT / "fixtures" / "fare_taps" / "intake.json"


def _golden_data(run_id: str = "fare-run"):
    return load_fare_taps(GOLDEN_CONTRACT, ROOT, GOLDEN_INTAKE, run_id)


def _golden_settlement(run_id: str = "fare-run"):
    data = _golden_data(run_id)
    return data, settle_transfers(data, GOLDEN_CONTRACT, GOLDEN_INTAKE, ROOT)


def _by_key(items, key):
    return {item[key]: item for item in items}


def test_golden_aggregate_relief_picks_h2_h3_only():
    _, settlement = _golden_settlement()
    day = settlement["days"][0]
    contract = day["contracts"]["aggregate_relief"]
    assert [(edge["from_tap"], edge["to_tap"]) for edge in contract["redeemed_edges"]] == [
        ("h2", "h3")]
    assert contract["redeemed_edges"][0]["discount_cents"] == 180
    assert contract["redeemed_edges"][0]["second_operator"] == "ferry"
    assert contract["objective_value"] == {"savings_cents": 180, "riders_served": 1}
    bills = _by_key(contract["rider_bills"], "rider_id")
    assert bills["H"]["paid_cents"] == 620
    assert bills["L1"]["paid_cents"] == 260
    assert bills["L2"]["paid_cents"] == 260
    assert sum(item["paid_cents"] for item in contract["rider_bills"]) == 1140
    ledger = _by_key(contract["operator_ledger"], "operator")
    assert ledger["ferry"] == {"operator": "ferry", "base_fare_cents": 360,
                               "second_leg_discount_cents": 180, "net_receipt_cents": 180}
    assert contract["budgets"]["municipal"] == {"limit_cents": 180, "used_cents": 180,
                                                "remaining_cents": 0}


def test_golden_coverage_first_serves_both_l_riders():
    _, settlement = _golden_settlement()
    contract = settlement["days"][0]["contracts"]["coverage_first"]
    assert [(edge["from_tap"], edge["to_tap"]) for edge in contract["redeemed_edges"]] == [
        ("l1a", "l1b"), ("l2a", "l2b")]
    assert contract["objective_value"] == {"savings_cents": 160, "riders_served": 2}
    assert sum(item["paid_cents"] for item in contract["rider_bills"]) == 1160
    ledger = _by_key(contract["operator_ledger"], "operator")
    assert ledger["bus"]["base_fare_cents"] == 460
    assert ledger["bus"]["net_receipt_cents"] == 300
    assert contract["budgets"]["municipal"]["remaining_cents"] == 20
    bus_budget = next(item for item in contract["budgets"]["operator"] if item["operator"] == "bus")
    assert bus_budget == {"operator": "bus", "limit_cents": 160, "used_cents": 160,
                          "remaining_cents": 0}


def test_golden_preview_quotes_230_but_does_not_clear_budget():
    data = _golden_data()
    preview = preview_first_transfer(data)
    assert preview["quoted_discount_cents"] == 230
    assert preview["shared_budget_cleared"] is False
    # The local 230 quote cannot be honored against the 180 municipal budget.
    settlement = settle_transfers(data, GOLDEN_CONTRACT, GOLDEN_INTAKE, ROOT)
    for objective in ("aggregate_relief", "coverage_first"):
        used = settlement["days"][0]["contracts"][objective]["budgets"]["municipal"]["used_cents"]
        assert used <= 180


def test_two_contracts_produce_distinct_real_bills():
    _, settlement = _golden_settlement()
    aggregate = settlement["days"][0]["contracts"]["aggregate_relief"]
    coverage = settlement["days"][0]["contracts"]["coverage_first"]
    agg_keys = {(e["from_tap"], e["to_tap"]) for e in aggregate["redeemed_edges"]}
    cov_keys = {(e["from_tap"], e["to_tap"]) for e in coverage["redeemed_edges"]}
    assert agg_keys != cov_keys
    agg_paid = {item["rider_id"]: item["paid_cents"] for item in aggregate["rider_bills"]}
    cov_paid = {item["rider_id"]: item["paid_cents"] for item in coverage["rider_bills"]}
    assert agg_paid != cov_paid
    agg_net = {item["operator"]: item["net_receipt_cents"] for item in aggregate["operator_ledger"]}
    cov_net = {item["operator"]: item["net_receipt_cents"] for item in coverage["operator_ledger"]}
    assert agg_net != cov_net
    # Each contract's riders and operators still conserve to the same base total.
    base_total = sum(tap["base_fare_cents"] for tap in _golden_data()["taps"])
    for contract in (aggregate, coverage):
        assert contract["conservation"]["total_base_fare_cents"] == base_total
        assert contract["conservation"]["total_paid_cents"] == contract["conservation"][
            "total_base_fare_cents"] - contract["objective_value"]["savings_cents"]
        assert contract["conservation"]["total_net_receipt_cents"] == contract["conservation"][
            "total_paid_cents"]
        assert contract["conservation"]["municipal_used_cents"] == contract["objective_value"][
            "savings_cents"]


def test_certificates_explain_why_each_losing_edge_lost():
    _, settlement = _golden_settlement()
    aggregate = settlement["days"][0]["contracts"]["aggregate_relief"]
    losing = {
        (item["edge"]["from_tap"], item["edge"]["to_tap"]): item
        for item in aggregate["unselected_edges"]
    }
    h1h2 = losing[("h1", "h2")]
    assert h1h2["blockers"][0]["code"] == "objective_dominated"
    # Forcing the 70-cent quote can only reach 150 total (70 + one 80 quote).
    assert h1h2["blockers"][0]["forced_objective_vector"] == [150, 2]
    assert h1h2["blockers"][0]["optimal_objective_vector"] == [180, 1]
    coverage = settlement["days"][0]["contracts"]["coverage_first"]
    h2h3 = next(item for item in coverage["unselected_edges"]
                if item["edge"]["from_tap"] == "h2")
    assert h2h3["blockers"][0]["forced_objective_vector"] == [1, 180]
    assert h2h3["blockers"][0]["optimal_objective_vector"] == [2, 160]


def test_shared_tap_cannot_redeem_two_transfers(tmp_path):
    rows = {
        "2024-06-01": [
            ("a1", "R", "2024-06-01T08:00:00Z", "g1", "rail", 100),
            ("a2", "R", "2024-06-01T08:20:00Z", "g2", "bus", 200),
            ("a3", "R", "2024-06-01T08:40:00Z", "g3", "ferry", 200),
        ]
    }
    terms = {
        "dataset": "fare_taps", "transfer_window_minutes": 60,
        "eligible_pairs": "adjacent_consecutive_taps_only", "tap_reuse_limit": 1,
        "discount_percent_of_second_leg": 50, "municipal_discount_budget_cents": 500,
        "operator_discount_budget_cents": {"bus": 500, "ferry": 500, "rail": 500},
        "contracts": dict(load_contracts()),
    }
    contract_path, intake_path = build_workspace(tmp_path, rows, terms)
    data = load_fare_taps(contract_path, tmp_path, intake_path, "shared-tap")
    settlement = settle_transfers(data, contract_path, intake_path, tmp_path)
    for objective in ("aggregate_relief", "coverage_first"):
        result = settlement["days"][0]["contracts"][objective]
        assert len(result["redeemed_edges"]) == 1
        # Both edges are 100 cents; lex tie-break keeps the earlier from_tap a1.
        assert (result["redeemed_edges"][0]["from_tap"],
                result["redeemed_edges"][0]["to_tap"]) == ("a1", "a2")
        conflict = next(item for item in result["unselected_edges"]
                        if item["edge"]["from_tap"] == "a2")
        assert conflict["blockers"][0]["code"] == "tap_exclusivity_conflict"
        assert "a1->a2" in conflict["blockers"][0]["evidence"]


def load_contracts():
    return {
        "aggregate_relief": "maximize total rider fare savings, then riders served",
        "coverage_first": "maximize number of riders with at least one discount, then total savings",
    }


def test_non_adjacent_pair_is_audited_but_never_a_candidate(tmp_path):
    rows = {
        "2024-06-01": [
            ("b1", "R", "2024-06-01T08:00:00Z", "g1", "rail", 100),
            ("b2", "R", "2024-06-01T08:20:00Z", "g2", "bus", 100),
            ("b3", "R", "2024-06-01T08:40:00Z", "g3", "ferry", 400),
        ]
    }
    terms = {
        "dataset": "fare_taps", "transfer_window_minutes": 60,
        "eligible_pairs": "adjacent_consecutive_taps_only", "tap_reuse_limit": 1,
        "discount_percent_of_second_leg": 50, "municipal_discount_budget_cents": 500,
        "operator_discount_budget_cents": {"bus": 500, "ferry": 500, "rail": 500},
        "contracts": dict(load_contracts()),
    }
    contract_path, intake_path = build_workspace(tmp_path, rows, terms)
    data = load_fare_taps(contract_path, tmp_path, intake_path, "decoy")
    settlement = settle_transfers(data, contract_path, intake_path, tmp_path)
    day = settlement["days"][0]
    audit = {(item["from_tap"], item["to_tap"]): item for item in day["pair_audit"]}
    assert audit[("b1", "b3")]["adjacent"] is False
    assert audit[("b1", "b3")]["reason"] == "non_adjacent_pair"
    candidate_keys = {
        (edge["from_tap"], edge["to_tap"])
        for objective in day["contracts"].values()
        for edge in (objective["redeemed_edges"]
                     + [item["edge"] for item in objective["unselected_edges"]])
    }
    assert ("b1", "b3") not in candidate_keys
    # The b2->b3 adjacent edge (200 cents) is eligible and beats b1->b2 (50);
    # the non-adjacent b1->b3 decoy never becomes a candidate at all.
    aggregate = day["contracts"]["aggregate_relief"]
    assert [(edge["from_tap"], edge["to_tap"]) for edge in aggregate["redeemed_edges"]] == [
        ("b2", "b3")]
    assert aggregate["redeemed_edges"][0]["discount_cents"] == 200


def test_input_row_reordering_does_not_change_settlement(tmp_path):
    rows = {
        "2024-06-01": [
            ("c3", "R2", "2024-06-01T09:50:00Z", "g6", "bus", 160),
            ("c1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
            ("c2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", 160),
        ]
    }
    contract_path, intake_path = build_workspace(tmp_path, rows)
    data = load_fare_taps(contract_path, tmp_path, intake_path, "shuffled")
    settlement = settle_transfers(data, contract_path, intake_path, tmp_path)
    canonical = json.dumps(settlement, sort_keys=True)
    # Re-materialize the same logical rows in a different order.
    shuffled_root = tmp_path / "again"
    shuffled_rows = {"2024-06-01": list(reversed(rows["2024-06-01"]))}
    contract2, intake2 = build_workspace(shuffled_root, shuffled_rows)
    data2 = load_fare_taps(contract2, shuffled_root, intake2, "shuffled")
    settlement2 = settle_transfers(data2, contract2, intake2, shuffled_root)
    # Bindings differ by run file paths/SHAs only; strip them and compare outcomes.
    for document in (settlement, settlement2):
        document.pop("binding")
    assert json.dumps(settlement, sort_keys=True) == json.dumps(settlement2, sort_keys=True)
    assert canonical  # the first settlement was produced
