"""Exact integer optimizer for transfer-subsidy edge selection.

The clearing problem is a weighted independent-set problem on a conflict
graph whose edges only join consecutive candidate transfers of the same
rider (no tap may be used twice, and a skipped middle edge ends a rider's
redemption run), additionally constrained by one municipal budget and one
budget per second-leg operator. Inputs are bounded to 30 taps / 10 riders /
3 operators per clearing day, so a memoized depth-first branch search with
explicit objective comparison is exact, uses no floating point and returns a
machine-checkable optimum.

The search walks each rider's temporally ordered chain contiguously, which
makes every conflict a local decision. The deterministic tie-break is the
global lexicographic order of the selected ``(from_tap, to_tap)`` set; each
edge carries that global rank and the DP keeps the smallest rank tuple among
objective-equal selections.
"""

from __future__ import annotations

from dataclasses import dataclass

OBJECTIVES = ("aggregate_relief", "coverage_first")
_INFEASIBLE = (-1, -1, ())


@dataclass(frozen=True)
class TransferEdge:
    rider_id: str
    from_tap: str
    to_tap: str
    from_time: str
    to_time: str
    second_operator: str
    gap_minutes: int
    discount_cents: int
    partition_date: str

    @property
    def key(self) -> tuple[str, str]:
        return self.from_tap, self.to_tap


@dataclass(frozen=True)
class OptimalResult:
    feasible: bool
    selected: tuple[int, ...]
    savings_cents: int
    riders_served: int
    served_mask: int
    states_explored: int
    memoized_states: int

    def objective_vector(self, objective: str) -> tuple[int, int]:
        if objective == "aggregate_relief":
            return self.savings_cents, self.riders_served
        return self.riders_served, self.savings_cents


def ordered_edges(
    edges: tuple[TransferEdge, ...],
) -> tuple[tuple[TransferEdge, ...], tuple[int, ...]]:
    """Order edges for the search and return global lex ranks.

    The search order is ``(rider_id, from_time, from_tap, to_tap)`` so each
    rider's chain is contiguous and tap-sharing edges are immediate
    neighbours. ``ranks[i]`` is the global ``(from_tap, to_tap)`` rank of the
    edge at ordered position ``i``, used only for tie-breaking.
    """

    ordered = tuple(sorted(
        edges,
        key=lambda edge: (edge.rider_id, edge.from_time, edge.from_tap, edge.to_tap),
    ))
    global_order = sorted(range(len(ordered)), key=lambda i: ordered[i].key)
    ranks = [0] * len(ordered)
    for rank, index in enumerate(global_order):
        ranks[index] = rank
    return ordered, tuple(ranks)


def _index_of(edges: tuple[TransferEdge, ...], target: TransferEdge) -> int:
    for index, edge in enumerate(edges):
        if edge == target:
            return index
    raise ValueError("edge not found in input tuple")


def _better(
    candidate: tuple[int, int, tuple[int, ...]],
    incumbent: tuple[int, int, tuple[int, ...]],
    ranks: tuple[int, ...],
    objective: str,
) -> bool:
    """Compare two full selections: objective vector, then lex edge set."""

    cand_savings, cand_mask, cand_selected = candidate
    inc_savings, inc_mask, inc_selected = incumbent
    cand_served = cand_mask.bit_count()
    inc_served = inc_mask.bit_count()
    if objective == "aggregate_relief":
        cand_vector = (cand_savings, cand_served)
        inc_vector = (inc_savings, inc_served)
    else:
        cand_vector = (cand_served, cand_savings)
        inc_vector = (inc_served, inc_savings)
    if cand_vector != inc_vector:
        return cand_vector > inc_vector
    cand_ranks = tuple(sorted(ranks[index] for index in cand_selected))
    inc_ranks = tuple(sorted(ranks[index] for index in inc_selected))
    return cand_ranks < inc_ranks


def optimize(
    edges: tuple[TransferEdge, ...],
    municipal_budget: int,
    operator_budgets: dict[str, int],
    objective: str,
    required: frozenset[int] = frozenset(),
) -> OptimalResult:
    """Return the globally optimal feasible selection for one clearing day.

    ``required`` are indices into the caller's ``edges`` tuple forced into the
    selection. An infeasible forced set yields ``OptimalResult(feasible=False)``.
    """

    if objective not in OBJECTIVES:
        raise ValueError(f"unknown objective: {objective}")
    ordered, ranks = ordered_edges(edges)
    if any(index < 0 or index >= len(ordered) for index in required):
        return OptimalResult(False, (), 0, 0, 0, 0, 0)
    required_ordered = frozenset(
        position for position, edge in enumerate(ordered)
        if _index_of(edges, edge) in required
    )
    rider_names = sorted({edge.rider_id for edge in ordered})
    rider_bit = {rider: 1 << index for index, rider in enumerate(rider_names)}
    operators = tuple(sorted(operator_budgets))
    operator_slot = {name: slot for slot, name in enumerate(operators)}
    stats = {"states": 0, "memo": 0}
    memo: dict[tuple, tuple[int, int, tuple[int, ...]]] = {}

    def search(
        index: int,
        last_taken: int,
        rem_municipal: int,
        rem_operator: tuple[int, ...],
    ) -> tuple[int, int, tuple[int, ...]]:
        """Best (savings, served-mask union, selected ordered indices) suffix.

        ``last_taken`` is the ordered position of the redeemed edge for the
        current rider, or -1 when none has been redeemed. A rider can never
        redeem a second edge: the immediate chain predecessor shares a tap
        (the reuse limit is one), while a skipped middle edge means the two
        edges are no longer consecutive. Hence inclusion is allowed only while
        ``last_taken == -1``. The marker resets to -1 at each rider boundary
        before the memo key is built, so distinct histories never share state.
        """

        stats["states"] += 1
        if index == len(ordered):
            return 0, 0, ()
        if index > 0 and ordered[index].rider_id != ordered[index - 1].rider_id:
            last_taken = -1
        memo_key = (index, last_taken, rem_municipal, rem_operator)
        cached = memo.get(memo_key)
        if cached is not None:
            stats["memo"] += 1
            return cached
        edge = ordered[index]
        slot = operator_slot[edge.second_operator]
        affordable = (
            edge.discount_cents <= rem_municipal
            and edge.discount_cents <= rem_operator[slot]
        )
        # A rider's redeemed edges form one contiguous run. Once any edge of
        # the rider has been redeemed, the immediate predecessor shares a tap
        # (cannot reuse it) and a skipped middle edge closed the run, so a new
        # edge is includable only when no edge of this rider was taken yet.
        can_include = last_taken == -1 and affordable
        if index in required_ordered and not can_include:
            memo[memo_key] = _INFEASIBLE
            return _INFEASIBLE
        best: tuple[int, int, tuple[int, ...]] | None = None
        if index not in required_ordered:
            best = search(index + 1, last_taken, rem_municipal, rem_operator)
            if best[0] < 0:
                memo[memo_key] = _INFEASIBLE
                return _INFEASIBLE
        if can_include:
            next_rem_operator = list(rem_operator)
            next_rem_operator[slot] -= edge.discount_cents
            sub = search(
                index + 1,
                index,
                rem_municipal - edge.discount_cents,
                tuple(next_rem_operator),
            )
            if sub[0] >= 0:
                include = (
                    sub[0] + edge.discount_cents,
                    sub[1] | rider_bit[edge.rider_id],
                    (index,) + sub[2],
                )
                if best is None or _better(include, best, ranks, objective):
                    best = include
        if best is None:
            memo[memo_key] = _INFEASIBLE
            return _INFEASIBLE
        memo[memo_key] = best
        return best

    result = search(0, -1, municipal_budget,
                    tuple(operator_budgets[name] for name in operators))
    if result[0] < 0:
        return OptimalResult(False, (), 0, 0, 0, stats["states"], stats["memo"])
    savings, served_mask, selected_ordered = result
    selected = tuple(sorted(_index_of(edges, ordered[index]) for index in selected_ordered))
    return OptimalResult(
        feasible=True,
        selected=selected,
        savings_cents=savings,
        riders_served=served_mask.bit_count(),
        served_mask=served_mask,
        states_explored=stats["states"],
        memoized_states=stats["memo"],
    )
