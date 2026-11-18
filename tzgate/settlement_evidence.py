"""Machine-checkable evidence for redeemed and rejected transfer edges."""

from __future__ import annotations

from typing import Any

from .settlement_opt import TransferEdge, optimize


def edge_record(edge: TransferEdge) -> dict:
    return {"rider_id": edge.rider_id, "from_tap": edge.from_tap, "to_tap": edge.to_tap,
            "from_time": edge.from_time, "to_time": edge.to_time,
            "second_operator": edge.second_operator, "gap_minutes": edge.gap_minutes,
            "discount_cents": edge.discount_cents}


def certify_unselected(
    index: int,
    edges: tuple[TransferEdge, ...],
    chosen: frozenset[int],
    terms: dict,
    objective: str,
    optimal_vector: tuple[int, int],
) -> dict:
    """Explain why one eligible edge is absent from the redeemed set."""

    edge = edges[index]
    blockers: list[dict] = []
    previous_of_rider = [
        other for other in range(len(edges))
        if edges[other].rider_id == edge.rider_id and edges[other].key < edge.key
    ]
    conflicts = sorted(set(previous_of_rider) & chosen)
    for other in conflicts:
        blockers.append({"code": "tap_exclusivity_conflict",
                         "evidence": f"shares a tap with redeemed edge "
                                     f"{edges[other].from_tap}->{edges[other].to_tap}"})
    municipal = terms["municipal_discount_budget_cents"]
    operator_budgets = terms["operator_discount_budget_cents"]
    if edge.discount_cents > municipal:
        blockers.append({"code": "municipal_budget_exceeded",
                         "evidence": f"discount {edge.discount_cents} > municipal budget {municipal}"})
    budget = operator_budgets[edge.second_operator]
    if edge.discount_cents > budget:
        blockers.append({"code": "operator_budget_exceeded",
                         "evidence": f"discount {edge.discount_cents} > "
                                     f"{edge.second_operator} budget {budget}"})
    if not blockers:
        # Force only this edge; the global optimizer proves the best feasible
        # completion under every constraint, so the result is a true certificate.
        forced = optimize(edges, municipal, operator_budgets, objective,
                          required=frozenset({index}))
        record = {"code": "objective_dominated",
                  "forced_inclusion_feasible": forced.feasible}
        if forced.feasible:
            forced_vector = forced.objective_vector(objective)
            record["forced_objective_vector"] = list(forced_vector)
            record["optimal_objective_vector"] = list(optimal_vector)
            if forced_vector == optimal_vector:
                record["evidence"] = (
                    "objective tie; lexicographic tie-break on "
                    "(from_tap, to_tap) preferred the redeemed set")
            else:
                record["evidence"] = (
                    f"forcing this edge yields objective {list(forced_vector)} "
                    f"which is worse than optimal {list(optimal_vector)}")
        else:
            record["evidence"] = "forcing this edge makes every selection infeasible"
        blockers.append(record)
    return {"edge": edge_record(edge), "blockers": blockers}


def lex_tie_evidence(
    edges: tuple[TransferEdge, ...],
    selected: tuple[int, ...],
    terms: dict,
    objective: str,
    optimal: Any,
) -> dict:
    """Enumerate every alternative set tied on the objective but lex-larger."""

    chosen_keys = sorted(edges[index].key for index in selected)
    lex_rule = {"rule": "minimize sorted tuple of (from_tap, to_tap) lexicographically"}
    tied_alternatives: list[list[tuple[str, str]]] = []
    for index in range(len(edges)):
        if index in selected:
            continue
        # Forcing one non-selected edge asks the optimizer for the best feasible
        # set containing it; equal-objective results are genuine tied alternatives.
        forced = optimize(edges, terms["municipal_discount_budget_cents"],
                          terms["operator_discount_budget_cents"], objective,
                          required=frozenset({index}))
        if forced.feasible and forced.objective_vector(objective) == optimal.objective_vector(objective):
            alternative = sorted(edges[i].key for i in forced.selected)
            if alternative not in tied_alternatives and alternative != chosen_keys:
                tied_alternatives.append(alternative)
    lex_rule["selected_edge_keys"] = [list(key) for key in chosen_keys]
    lex_rule["tied_alternatives_rejected"] = [[list(key) for key in alt]
                                              for alt in tied_alternatives]
    lex_rule["deterministic"] = True
    return lex_rule
