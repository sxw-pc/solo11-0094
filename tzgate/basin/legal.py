"""Legal decisions over joint possible worlds.

Quantifier order is enforced literally:

* ``individual_fine`` acts on one source only when that *same* source exceeds
  its cap in **every** feasible world;
* ``collective_remediation`` acts when **every** feasible world contains *at
  least one* source over cap.

These are different quantifiers and must never be interchanged or replaced by
merged per-source interval flags.
"""
from __future__ import annotations

from itertools import product

from .validation import FINE_RULE, REMEDIATION_RULE
from .worlds import day_indices, extremal_world, world_payload


def evaluate_contract(
    spec, feasible, slots, dischargers, days, world_ids, materialized
) -> dict:
    rule = spec["decision"]
    conclusions = []
    for day in days:
        indices = day_indices(slots, day)
        if rule == FINE_RULE:
            conclusions.append(_fine_conclusion(
                spec, day, indices, feasible, slots,
                dischargers, world_ids, materialized,
            ))
        else:
            assert rule == REMEDIATION_RULE
            conclusions.append(_remediation_conclusion(
                spec, day, indices, feasible, slots,
                dischargers, world_ids, materialized,
            ))
    return {
        "decision_rule": rule,
        "daily_caps": dict(spec["daily_caps"]),
        "conclusions": conclusions,
    }


def _fine_conclusion(
    spec, day, indices, feasible, slots, dischargers, world_ids, materialized
) -> dict:
    caps = spec["daily_caps"]
    subjects = []
    for source_index, source in enumerate(dischargers):
        cap = caps[source]
        bias, vectors, total = extremal_world(feasible, indices, source_index, True)
        fined = total > cap
        subjects.append({
            "source": source,
            "cap": cap,
            "minimum_daily_total": total,
            "outcome": "fine" if fined else "no_fine",
            "witness": world_payload(
                bias, vectors, slots, dischargers, world_ids, materialized
            ),
            "witness_role": (
                "every_world_violates_minimizer_still_over_cap"
                if fined else "counterexample_world_source_within_cap"
            ),
        })
    return {
        "day": day,
        "quantifier": "for_all_worlds:same_source_over_cap",
        "subjects": subjects,
        "fines_issued": [item for item in subjects if item["outcome"] == "fine"],
    }


def _remediation_conclusion(
    spec, day, indices, feasible, slots, dischargers, world_ids, materialized
) -> dict:
    caps = spec["daily_caps"]
    compliant = _find_compliant_world(feasible, indices, caps, dischargers)
    base = {
        "day": day,
        "quantifier": "for_all_worlds:exists_source_over_cap",
    }
    if compliant is not None:
        bias, vectors = compliant
        return {
            **base,
            "outcome": "no_order",
            "counterexample_world": world_payload(
                bias, vectors, slots, dischargers, world_ids, materialized
            ),
            "exhaustion": None,
        }
    exhaustion = []
    for bias, per_slot in feasible:
        examined, nearest = _exhaust_bias(per_slot, indices, caps, dischargers)
        exhaustion.append({
            "bias": bias,
            "examined_combinations": examined,
            "nearest_miss_world": world_payload(
                bias, nearest, slots, dischargers, world_ids, materialized
            ),
        })
    return {
        **base,
        "outcome": "order",
        "counterexample_world": None,
        "exhaustion": exhaustion,
    }


def _find_compliant_world(feasible, indices, caps, dischargers):
    """Return a joint world where every source stays within cap, or None."""
    cap_vector = tuple(caps[source] for source in dischargers)
    for bias, per_slot in feasible:
        totals = [0] * len(dischargers)
        chosen: list[tuple[int, ...]] = []

        def search(position: int) -> bool:
            if position == len(indices):
                return True
            for vector in per_slot[indices[position]]:
                if all(
                    totals[i] + vector[i] <= cap_vector[i]
                    for i in range(len(dischargers))
                ):
                    for i, value in enumerate(vector):
                        totals[i] += value
                    chosen.append(vector)
                    if search(position + 1):
                        return True
                    chosen.pop()
                    for i, value in enumerate(vector):
                        totals[i] -= value
            return False

        if search(0):
            return bias, tuple(chosen)
    return None


def _exhaust_bias(per_slot, indices, caps, dischargers):
    """Count every combination for one bias and return its least-violating world."""
    cap_vector = tuple(caps[source] for source in dischargers)
    examined = 0
    nearest = None
    nearest_key = None
    for combo in product(*(per_slot[index] for index in indices)):
        examined += 1
        totals = [0] * len(dischargers)
        for vector in combo:
            for i, value in enumerate(vector):
                totals[i] += value
        violations = sum(1 for total, cap in zip(totals, cap_vector) if total > cap)
        excess = sum(max(total - cap, 0) for total, cap in zip(totals, cap_vector))
        key = (violations, excess)
        if nearest_key is None or key < nearest_key:
            nearest_key = key
            nearest = combo
    return examined, nearest
