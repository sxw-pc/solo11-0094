"""Exhaustive brute-force oracle cross-check and edge eligibility audit."""

from __future__ import annotations

import itertools
import json

from tzgate.fare_tap_intake import load_fare_taps
from tzgate.settlement import build_candidates, settle_transfers
from tzgate.settlement_opt import optimize

from fare_settlement_helpers import DEFAULT_TERMS, build_workspace


def _terms():
    return json.loads(json.dumps(DEFAULT_TERMS))


def _brute_force(edges, terms, objective):
    """Enumerate every feasible selection; the exact reference oracle.

    Mirrors every clearing rule: a rider redeems at most one edge, because two
    edges of one rider either share a tap or have a skipped middle edge between
    them. Riders are otherwise independent, subject to the shared budgets.
    """

    municipal = terms["municipal_discount_budget_cents"]
    op_budgets = terms["operator_discount_budget_cents"]
    by_rider: dict[str, list[int]] = {}
    for index, edge in enumerate(edges):
        by_rider.setdefault(edge.rider_id, []).append(index)
    best_vector = None
    best_keys = None

    def evaluate(picks):
        spent_op = {name: 0 for name in op_budgets}
        spent_municipal = 0
        for index in picks:
            edge = edges[index]
            spent_op[edge.second_operator] += edge.discount_cents
            spent_municipal += edge.discount_cents
        if spent_municipal > municipal:
            return None
        if any(spent_op[op] > op_budgets[op] for op in op_budgets):
            return None
        savings = sum(edges[index].discount_cents for index in picks)
        served = len({edges[index].rider_id for index in picks})
        vector = (savings, served) if objective == "aggregate_relief" else (served, savings)
        keys = tuple(sorted(edges[index].key for index in picks))
        return vector, keys

    # Per rider choose: no edge or exactly one candidate edge.
    options_per_rider = [[()] + [(index,) for index in chain]
                         for chain in (by_rider[rider] for rider in sorted(by_rider))]
    for combo in itertools.product(*options_per_rider):
        picks = tuple(index for option in combo for index in option)
        evaluated = evaluate(picks)
        if evaluated is None:
            continue
        vector, keys = evaluated
        if best_vector is None or vector > best_vector or (
                vector == best_vector and keys < best_keys):
            best_vector, best_keys = vector, keys
    return best_vector, best_keys


def test_optimizer_matches_brute_force_on_random_instances(tmp_path):
    import random

    rng = random.Random(20240601)
    operators = ("bus", "ferry", "rail")
    for instance in range(120):
        terms = _terms()
        rider_count = rng.randint(1, 5)
        rows = []
        for rider_index in range(rider_count):
            rider = f"R{rider_index}"
            minute = rng.randint(0, 30)
            for tap_index in range(rng.randint(2, 5)):
                minute += rng.randint(10, 55)
                tap_id = f"i{instance:03d}-r{rider_index}-t{tap_index}"
                rows.append((tap_id, rider,
                             f"2024-06-01T{8 + minute // 60:02d}:{minute % 60:02d}:00Z",
                             f"g{tap_index}", rng.choice(operators),
                             rng.choice((40, 60, 100, 140, 200, 320))))
        # Vary budget tightness so zero, slack and binding caps are all exercised.
        terms["municipal_discount_budget_cents"] = rng.choice((0, 40, 80, 120, 300, 1000))
        terms["operator_discount_budget_cents"] = {
            "bus": rng.choice((0, 30, 70, 200)),
            "ferry": rng.choice((0, 40, 60, 200)),
            "rail": rng.choice((0, 20, 40, 200))}
        base = tmp_path / f"instance-{instance}"
        contract_path, intake_path = build_workspace(base, {"2024-06-01": rows}, terms)
        data = load_fare_taps(contract_path, base, intake_path, f"brute-{instance}")
        edges, _ = build_candidates(data["taps"], terms)
        for objective in ("aggregate_relief", "coverage_first"):
            optimal = optimize(edges, terms["municipal_discount_budget_cents"],
                               terms["operator_discount_budget_cents"], objective)
            vector, keys = _brute_force(edges, terms, objective)
            assert optimal.objective_vector(objective) == vector
            got_keys = tuple(sorted(edges[index].key for index in optimal.selected))
            assert got_keys == keys
            # Every redeemed selection must respect tap exclusivity and budgets.
            used: set[str] = set()
            for index in optimal.selected:
                edge = edges[index]
                assert edge.from_tap not in used and edge.to_tap not in used
                used.update((edge.from_tap, edge.to_tap))


def test_window_and_same_operator_edges_are_audited_not_selected(tmp_path):
    rows = {"2024-06-01": [
        ("a1", "R1", "2024-06-01T08:00:00Z", "g1", "rail", 100),
        ("a2", "R1", "2024-06-01T08:30:00Z", "g2", "rail", 100),
        ("a3", "R1", "2024-06-01T10:00:00Z", "g3", "bus", 160)]}
    contract_path, intake_path = build_workspace(tmp_path, rows)
    data = load_fare_taps(contract_path, tmp_path, intake_path, "audit")
    settlement = settle_transfers(data, contract_path, intake_path, tmp_path)
    audit = {(item["from_tap"], item["to_tap"]): item
             for item in settlement["days"][0]["pair_audit"]}
    assert audit[("a1", "a2")]["reason"] == "same_operator"
    assert audit[("a2", "a3")]["reason"] == "outside_transfer_window"
    assert settlement["days"][0]["candidate_edge_count"] == 0
