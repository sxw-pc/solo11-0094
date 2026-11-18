"""Headline worlds, shared-bias discipline and underdetermined matrices."""
from __future__ import annotations

import pytest

from tzgate.basin import AttributionError, attribute_basin, load_basin_intake
from tzgate.basin.solver import solve_slot

from basin_helpers import (
    CONTRACT,
    INTAKE,
    ROOT,
    T,
    T2,
    attribute,
    daily,
    rows,
    rules,
    workspace,
)


def _committed(run_id="x"):
    return attribute_basin(load_basin_intake(CONTRACT, ROOT, INTAKE, run_id))


def test_evidence_hashes_matrix_and_three_materialized_worlds():
    evidence = _committed("basin-run")
    assert evidence["run_id"] == "basin-run"
    assert evidence["contract"]["sha256"] and evidence["rulebook"]["sha256"]
    assert evidence["source_file"]["record_count"] == 6
    matrix = evidence["sensor_matrix"]
    assert matrix["dischargers"] == ["A", "B", "C"]
    assert matrix["common_bias_values"] == [-1, 0, 1]
    assert evidence["feasible_biases"] == [-1, 0, 1]
    assert evidence["joint_world_count"] == 3
    assert [w["world_id"] for w in evidence["worlds"]] == ["w1", "w2", "w3"]


def test_three_world_daily_totals_match_target_loads():
    evidence = _committed()
    by_bias = {w["bias"]: w for w in evidence["worlds"]}
    expected = {-1: (11, 6, 4), 0: (9, 6, 6), 1: (7, 6, 8)}
    for bias, target in expected.items():
        assert tuple(daily(by_bias[bias], s) for s in ("A", "B", "C")) == target
    bounds = evidence["daily_bounds"]["2024-07-01"]
    assert [(bounds[s]["min"], bounds[s]["max"]) for s in ("A", "B", "C")] == [
        (7, 11), (6, 6), (4, 8),
    ]


def test_quantifier_order_no_fines_but_remediation_order():
    evidence = _committed()
    fine = evidence["legal_decisions"]["individual_fine"]["conclusions"][0]
    assert fine["quantifier"] == "for_all_worlds:same_source_over_cap"
    assert fine["fines_issued"] == []
    for subject in fine["subjects"]:  # refusals carry counterexample worlds
        assert "world_id" in subject["witness"]
        assert daily(subject["witness"], subject["source"]) <= subject["cap"]
    order = evidence["legal_decisions"]["collective_remediation"]["conclusions"][0]
    assert order["quantifier"] == "for_all_worlds:exists_source_over_cap"
    assert order["outcome"] == "order"
    assert order["counterexample_world"] is None
    assert {item["bias"] for item in order["exhaustion"]} == {-1, 0, 1}
    assert sum(i["examined_combinations"] for i in order["exhaustion"]) == 3


def test_zero_bias_preview_would_wrongly_fine_a():
    evidence = _committed()
    bias_zero = next(w for w in evidence["worlds"] if w["bias"] == 0)
    assert daily(bias_zero, "A") == 9  # preview would fine A on cap 8
    fine = evidence["legal_decisions"]["individual_fine"]["conclusions"][0]
    subject_a = next(s for s in fine["subjects"] if s["source"] == "A")
    assert subject_a["outcome"] == "no_fine"
    assert subject_a["minimum_daily_total"] == 7  # bias=+1 counterexample


def test_per_slot_bias_reselection_is_rejected(tmp_path):
    # Slot 1 only fits b=-1; slot 2 only fits b=+1: no shared bias exists.
    conflict = rows({
        T: {"north": 0, "south": 2, "total": 1},
        T2: {"north": 2, "south": 0, "total": 1},
    })
    with pytest.raises(AttributionError, match="no feasible world"):
        attribute(tmp_path, conflict)
    pinned = rows({
        T: {"north": 0, "south": 2, "total": 1},
        T2: {"north": 0, "south": 2, "total": 1},
    })
    evidence = attribute(tmp_path / "p", pinned)
    assert evidence["feasible_biases"] == [-1]
    assert evidence["joint_world_count"] == 1
    assert evidence["worlds"][0]["bias"] == -1
    root, contract, intake = workspace(
        tmp_path / "r", pinned, rules(constant=False))
    with pytest.raises(AttributionError, match="shared value"):
        attribute_basin(load_basin_intake(contract, root, intake, "x"))


def test_shared_bias_worlds_are_not_per_slot_cross_products(tmp_path):
    evidence = attribute(tmp_path, rows({
        T: {"north": 7, "south": 6, "total": 10},
        T2: {"north": 8, "south": 6, "total": 11},
    }))
    assert evidence["joint_world_count"] == 3  # not 3*3 per-slot combinations
    assert all(len(w["loads"]) == 2 for w in evidence["worlds"])


def test_underdetermined_matrix_enumerates_joint_worlds(tmp_path):
    sensors = {"only": {"coefficients": [1, 1], "bias_coefficient": 0}}
    spec = rules(sensors=sensors, dischargers=("A", "B"),
                 caps={"A": 1, "B": 1}, biases=(0,))
    evidence = attribute(tmp_path, rows({T: {"only": 2}}), spec)
    assert evidence["joint_world_count"] == 3
    loads = {tuple(w["loads"][T][s] for s in ("A", "B"))
             for w in evidence["worlds"]}
    assert loads == {(0, 2), (1, 1), (2, 0)}
    fine = evidence["legal_decisions"]["individual_fine"]["conclusions"][0]
    assert fine["fines_issued"] == []
    order = evidence["legal_decisions"]["collective_remediation"]["conclusions"][0]
    assert order["outcome"] == "no_order"
    counter = order["counterexample_world"]["loads"][T]
    assert (counter["A"], counter["B"]) == (1, 1)


def test_underdetermined_multi_slot_uses_compact_cartesian_proof(tmp_path):
    sensors = {"only": {"coefficients": [1, 1], "bias_coefficient": 0}}
    spec = rules(sensors=sensors, dischargers=("A", "B"),
                 caps={"A": 3, "B": 3}, biases=(0,))
    evidence = attribute(
        tmp_path, rows({T: {"only": 2}, T2: {"only": 2}}), spec,
        materialization_limit=4)
    assert evidence["joint_world_count"] == 9
    assert evidence["worlds_materialized"] is False
    assert evidence["worlds"] == []
    proof = evidence["compact_proof"]
    assert proof["world_count_by_bias"] == [{"bias": 0, "world_count": 9}]
    assert len(proof["per_slot_solutions"]) == 2
    order = evidence["legal_decisions"]["collective_remediation"]["conclusions"][0]
    assert order["outcome"] == "no_order"  # exact without expanding the product
    bounds = evidence["daily_bounds"]["2024-07-01"]["A"]
    assert (bounds["min"], bounds["max"]) == (0, 4)


def test_remediation_fires_when_no_joint_world_is_fully_compliant(tmp_path):
    # Each source can independently be at cap, but no joint world keeps both.
    sensors = {"only": {"coefficients": [1, 1], "bias_coefficient": 0}}
    spec = rules(sensors=sensors, dischargers=("A", "B"),
                 caps={"A": 1, "B": 1}, biases=(0,))
    evidence = attribute(tmp_path, rows({T: {"only": 3}}), spec)
    assert evidence["legal_decisions"]["individual_fine"]["conclusions"][0][
        "fines_issued"] == []
    order = evidence["legal_decisions"]["collective_remediation"]["conclusions"][0]
    assert order["outcome"] == "order"
    assert order["exhaustion"][0]["examined_combinations"] == 4


def test_solver_pruning_keeps_all_solutions():
    targets = (((1, 1, 0), 1, 7), ((0, 1, 1), -1, 6), ((1, 1, 1), 0, 10))
    assert solve_slot(targets, 0, [0, 10], 3) == ((4, 3, 3),)
    assert solve_slot(targets, 1, [0, 10], 3) == ((3, 3, 4),)
    assert solve_slot(targets, -1, [0, 10], 3) == ((5, 3, 2),)
