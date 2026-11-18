"""Shared world construction helpers used by the solver and legal decisions."""
from __future__ import annotations

from itertools import product


def product_count(values) -> int:
    total = 1
    for value in values:
        total *= value
    return total


def materialize(feasible):
    """Expand all feasible (bias, per-slot vector tuple) joint worlds."""
    worlds = []
    for bias, per_slot in feasible:
        for combo in product(*per_slot):
            worlds.append((bias, combo))
    return sorted(worlds, key=lambda item: (item[0], item[1]))


def day_indices(slots, day):
    return [i for i, slot in enumerate(slots) if slot["utc_date"] == day]


def world_payload(bias, vectors, slots, dischargers, world_ids, materialized):
    payload = {
        "bias": bias,
        "loads": {
            slots[i]["event_time"]: dict(zip(dischargers, vector))
            for i, vector in enumerate(vectors)
        },
    }
    if materialized:
        payload["world_id"] = world_ids[(bias, vectors)]
    return payload


def extremal_world(feasible, indices, source_index, minimize):
    """Joint world attaining the min/max daily total for one source."""
    best = None
    for bias, per_slot in feasible:
        chosen = []
        total = 0
        for slot_index in indices:
            vector = (min if minimize else max)(
                per_slot[slot_index],
                key=lambda value: (value[source_index], value),
            )
            chosen.append(vector)
            total += vector[source_index]
        candidate = (bias, tuple(chosen), total)
        if best is None or (
            (total < best[2] if minimize else total > best[2])
            or (total == best[2] and (candidate[0], candidate[1]) < (best[0], best[1]))
        ):
            best = candidate
    return best


def day_names(slots):
    seen: list[str] = []
    for slot in slots:
        if slot["utc_date"] not in seen:
            seen.append(slot["utc_date"])
    return sorted(seen)
