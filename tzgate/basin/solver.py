"""Bounded exhaustive joint-world search and legal quantifier decisions."""
from __future__ import annotations

from typing import Any

from .legal import evaluate_contract
from .validation import (
    WORLD_MATERIALIZATION_LIMIT,
    AttributionError,
    build_slots,
    validate_rules,
)
from .worlds import (
    day_indices,
    day_names,
    extremal_world,
    materialize,
    product_count,
    world_payload,
)


def attribute_basin(
    data: dict[str, Any], *, materialization_limit: int = WORLD_MATERIALIZATION_LIMIT
) -> dict[str, Any]:
    """Enumerate joint possible worlds and settle every legal contract."""
    rules = validate_rules(data["rules"])
    slots = build_slots(data["readings"], rules)
    dischargers = rules["dischargers"]
    matrix = tuple(
        (tuple(spec["coefficients"]), spec["bias_coefficient"], name)
        for name, spec in sorted(rules["sensors"].items())
    )
    feasible = _feasible_bias_worlds(rules, slots, matrix, len(dischargers))
    world_count = sum(
        product_count(len(vectors) for vectors in per_slot)
        for _, per_slot in feasible
    )
    materialized = world_count <= materialization_limit
    world_ids: dict[tuple[int, tuple[tuple[int, ...], ...]], str] = {}
    worlds: list[dict[str, Any]] = []
    if materialized:
        for index, (bias, vectors) in enumerate(materialize(feasible), start=1):
            world_ids[(bias, vectors)] = f"w{index}"
            worlds.append({
                "world_id": f"w{index}",
                "bias": bias,
                "loads": {
                    slots[i]["event_time"]: dict(zip(dischargers, vector))
                    for i, vector in enumerate(vectors)
                },
            })
    days = day_names(slots)
    evidence: dict[str, Any] = {
        "run_id": data["run_id"],
        "contract": data["contract"],
        "rulebook": data["rulebook"],
        "verification_run": data["run_summary"],
        "source_file": {
            "path": data["file"],
            "sha256": data["file_sha256"],
            "record_count": data["record_count"],
        },
        "sensor_matrix": {
            "dischargers": dischargers,
            "sensors": {
                name: {
                    "coefficients": rules["sensors"][name]["coefficients"],
                    "bias_coefficient": rules["sensors"][name]["bias_coefficient"],
                }
                for name in sorted(rules["sensors"])
            },
            "common_bias_values": rules["common_bias_values"],
            "bias_constant_across_slots": True,
            "load_bounds": rules["load_bounds"],
        },
        "slots": slots,
        "feasible_biases": [bias for bias, _ in feasible],
        "joint_world_count": world_count,
        "worlds_materialized": materialized,
        "worlds": worlds,
        "compact_proof": None if materialized else _compact_proof(feasible, slots, dischargers),
        "daily_bounds": _daily_bounds(
            feasible, slots, dischargers, days, world_ids, materialized
        ),
        "legal_decisions": {
            name: evaluate_contract(
                spec, feasible, slots, dischargers, days, world_ids, materialized
            )
            for name, spec in sorted(rules["contracts"].items())
        },
    }
    return evidence


def _feasible_bias_worlds(rules, slots, matrix, size):
    slot_solutions = []
    for slot in slots:
        targets = tuple(
            (coeff, bias_coeff, slot["readings"][name])
            for coeff, bias_coeff, name in matrix
        )
        solutions = {}
        for bias in rules["common_bias_values"]:
            vectors = solve_slot(targets, bias, rules["load_bounds"], size)
            if vectors:
                solutions[bias] = vectors
        slot_solutions.append(solutions)
    shared = sorted(set.intersection(*(set(item) for item in slot_solutions)))
    if not shared:
        raise AttributionError(
            "no feasible world: no shared calibration bias satisfies every "
            "slot within the declared load bounds"
        )
    return [
        (bias, tuple(slot_solutions[index][bias] for index in range(len(slots))))
        for bias in shared
    ]


def solve_slot(targets, bias, bounds, size):
    """All bounded integer vectors satisfying every sensor equation.

    Variables are searched most-constrained-first; partial assignments are
    pruned with linear min/max interval checks on every incomplete row.
    """
    low, high = bounds
    order = sorted(
        range(size),
        key=lambda index: (-sum(abs(row[0][index]) for row in targets), index),
    )
    equations = [(coeff, reading - bc * bias) for coeff, bc, reading in targets]
    solutions: list[tuple[int, ...]] = []
    assignment = [0] * size
    used = [False] * size

    def prefix_ok() -> bool:
        for coeff, target in equations:
            fixed = 0
            span_low = span_high = 0
            complete = True
            for index in range(size):
                if used[index]:
                    fixed += coeff[index] * assignment[index]
                else:
                    complete = False
                    lo, hi = sorted((coeff[index] * low, coeff[index] * high))
                    span_low += lo
                    span_high += hi
            if complete:
                if fixed != target:
                    return False
            elif not fixed + span_low <= target <= fixed + span_high:
                return False
        return True

    def search(depth: int) -> None:
        if depth == size:
            solutions.append(tuple(assignment))
            return
        index = order[depth]
        used[index] = True
        for value in range(low, high + 1):
            assignment[index] = value
            if prefix_ok():
                search(depth + 1)
        used[index] = False

    search(0)
    return tuple(sorted(solutions))


def _compact_proof(feasible, slots, dischargers):
    return {
        "method": "exact_per_slot_enumeration_cartesian_products",
        "per_slot_solutions": [
            {
                "event_time": slots[index]["event_time"],
                "bias_solutions": [
                    {
                        "bias": bias,
                        "feasible_loads": [
                            dict(zip(dischargers, vector)) for vector in per_slot[index]
                        ],
                    }
                    for bias, per_slot in feasible
                ],
            }
            for index in range(len(slots))
        ],
        "world_count_by_bias": [
            {"bias": bias, "world_count": product_count(len(v) for v in per_slot)}
            for bias, per_slot in feasible
        ],
    }


def _daily_bounds(feasible, slots, dischargers, days, world_ids, materialized):
    bounds = {}
    for day in days:
        indices = day_indices(slots, day)
        per_source = {}
        for source_index, source in enumerate(dischargers):
            lo_b, lo_v, lo_total = extremal_world(feasible, indices, source_index, True)
            hi_b, hi_v, hi_total = extremal_world(feasible, indices, source_index, False)
            per_source[source] = {
                "min": lo_total,
                "max": hi_total,
                "min_witness": world_payload(
                    lo_b, lo_v, slots, dischargers, world_ids, materialized
                ),
                "max_witness": world_payload(
                    hi_b, hi_v, slots, dischargers, world_ids, materialized
                ),
            }
        bounds[day] = per_source
    return bounds
