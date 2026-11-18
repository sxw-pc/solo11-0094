"""Budget caps, deterministic tie-breaks and per-day budget reset."""

from __future__ import annotations

import json

from tzgate.fare_tap_intake import load_fare_taps
from tzgate.settlement import settle_transfers

from fare_settlement_helpers import DEFAULT_TERMS, build_workspace


def _terms(**overrides):
    terms = json.loads(json.dumps(DEFAULT_TERMS))
    terms.update(overrides)
    return terms


def _load_and_settle(base, rows_by_day, terms=None, run_id="run"):
    contract_path, intake_path = build_workspace(base, rows_by_day, terms)
    data = load_fare_taps(contract_path, base, intake_path, run_id)
    return data, settle_transfers(data, contract_path, intake_path, base)


def test_operator_budget_truncation_blocks_second_bus_edge(tmp_path):
    # Two independent bus transfer edges worth 80 each; bus cap 90 allows one.
    rows = {"2024-06-01": [
        ("d1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
        ("d2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", 160),
        ("e1", "R2", "2024-06-01T09:00:00Z", "g3", "rail", 100),
        ("e2", "R2", "2024-06-01T09:30:00Z", "g4", "bus", 160)]}
    terms = _terms(municipal_discount_budget_cents=500,
                   operator_discount_budget_cents={"bus": 90, "ferry": 0, "rail": 0})
    _, settlement = _load_and_settle(tmp_path, rows, terms)
    for objective in ("aggregate_relief", "coverage_first"):
        contract = settlement["days"][0]["contracts"][objective]
        assert len(contract["redeemed_edges"]) == 1
        assert contract["redeemed_edges"][0]["discount_cents"] == 80
        bus = next(item for item in contract["budgets"]["operator"] if item["operator"] == "bus")
        assert bus["used_cents"] == 80 and bus["remaining_cents"] == 10
        # The second edge alone is affordable; its certificate proves globally
        # that selecting it cannot beat the capped optimum.
        unselected = next(item for item in contract["unselected_edges"]
                          if item["edge"]["to_tap"] == "e2")
        blocker = next(block for block in unselected["blockers"]
                       if block["code"] == "objective_dominated")
        assert blocker["forced_inclusion_feasible"] is True


def test_individually_affordable_edge_blocked_when_operator_cap_is_full(tmp_path):
    # Both edges are 80 cents and the operator cap is exactly 80: once one edge
    # spends the cap, the other cannot be added alongside it.
    rows = {"2024-06-01": [
        ("d1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
        ("d2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", 160),
        ("e1", "R2", "2024-06-01T09:00:00Z", "g3", "rail", 100),
        ("e2", "R2", "2024-06-01T09:30:00Z", "g4", "bus", 160)]}
    terms = _terms(municipal_discount_budget_cents=500,
                   operator_discount_budget_cents={"bus": 80, "ferry": 0, "rail": 0})
    _, settlement = _load_and_settle(tmp_path, rows, terms)
    contract = settlement["days"][0]["contracts"]["aggregate_relief"]
    assert contract["budgets"]["operator"][0]["used_cents"] == 80
    assert sum(edge["discount_cents"] for edge in contract["redeemed_edges"]) == 80


def test_zero_operator_budget_rejects_rail_discount_edges(tmp_path):
    rows = {"2024-06-01": [
        ("f1", "R1", "2024-06-01T08:00:00Z", "g1", "bus", 100),
        ("f2", "R1", "2024-06-01T08:30:00Z", "g2", "rail", 200)]}
    # rail budget is 0 in the default terms: this edge can never be redeemed.
    _, settlement = _load_and_settle(tmp_path, rows)
    contract = settlement["days"][0]["contracts"]["aggregate_relief"]
    assert contract["redeemed_edges"] == []
    assert contract["objective_value"] == {"savings_cents": 0, "riders_served": 0}
    blocker = contract["unselected_edges"][0]["blockers"][0]
    assert blocker["code"] == "operator_budget_exceeded"
    assert "rail budget 0" in blocker["evidence"]


def test_municipal_budget_tie_resolved_by_lexicographic_edge_set(tmp_path):
    # Two disjoint 80-cent edges and a municipal cap of 80: either one alone is
    # optimal; (d1, d2) sorts before (e1, e2) and must win deterministically.
    rows = {"2024-06-01": [
        ("d1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
        ("d2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", 160),
        ("e1", "R2", "2024-06-01T09:00:00Z", "g3", "rail", 100),
        ("e2", "R2", "2024-06-01T09:30:00Z", "g4", "bus", 160)]}
    terms = _terms(municipal_discount_budget_cents=80,
                   operator_discount_budget_cents={"bus": 200, "ferry": 0, "rail": 0})
    _, settlement = _load_and_settle(tmp_path, rows, terms)
    aggregate = settlement["days"][0]["contracts"]["aggregate_relief"]
    assert [(edge["from_tap"], edge["to_tap"]) for edge in aggregate["redeemed_edges"]] == [
        ("d1", "d2")]
    tie = aggregate["tie_evidence"]
    assert tie["selected_edge_keys"] == [["d1", "d2"]]
    assert tie["tied_alternatives_rejected"] == [[["e1", "e2"]]]
    assert tie["deterministic"] is True
    coverage = settlement["days"][0]["contracts"]["coverage_first"]
    assert [(edge["from_tap"], edge["to_tap"]) for edge in coverage["redeemed_edges"]] == [
        ("d1", "d2")]


def test_multi_day_budgets_reset_and_days_settle_independently(tmp_path):
    rows = {
        "2024-06-01": [
            ("m1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
            ("m2", "R1", "2024-06-01T08:30:00Z", "g2", "bus", 160)],
        "2024-06-02": [
            ("n1", "R1", "2024-06-02T08:00:00Z", "g3", "rail", 100),
            ("n2", "R1", "2024-06-02T08:30:00Z", "g4", "bus", 160)],
    }
    terms = _terms(municipal_discount_budget_cents=80,
                   operator_discount_budget_cents={"bus": 80, "ferry": 0, "rail": 0})
    _, settlement = _load_and_settle(tmp_path, rows, terms)
    days = {day["partition_date"]: day for day in settlement["days"]}
    assert sorted(days) == ["2024-06-01", "2024-06-02"]
    for day in days.values():
        contract = day["contracts"]["aggregate_relief"]
        assert contract["budgets"]["municipal"]["used_cents"] == 80
        assert contract["budgets"]["municipal"]["remaining_cents"] == 0
    # Each day's 80-cent subsidy is granted again: no cross-day budget pooling.
    first = days["2024-06-01"]["contracts"]["aggregate_relief"]["redeemed_edges"]
    second = days["2024-06-02"]["contracts"]["aggregate_relief"]["redeemed_edges"]
    assert [edge["to_tap"] for edge in first] == ["m2"]
    assert [edge["to_tap"] for edge in second] == ["n2"]


def test_cross_midnight_taps_never_share_one_day_budget(tmp_path):
    # Adjacent taps 30 minutes apart but on different partition days.
    rows = {
        "2024-06-01": [("p1", "R1", "2024-06-01T23:50:00Z", "g1", "rail", 100)],
        "2024-06-02": [("p2", "R1", "2024-06-02T00:20:00Z", "g2", "bus", 160)],
    }
    _, settlement = _load_and_settle(tmp_path, rows)
    assert len(settlement["days"]) == 2
    for day in settlement["days"]:
        assert day["candidate_edge_count"] == 0
