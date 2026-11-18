"""Exact greatest-fixed-point solver for multilateral debt clearing.

Each debtor pays ``min(total_debt, external_cash + actual inflows)`` and the
payment is distributed under one of two claim regimes: ``pari_passu`` (pro
rata across all of the debtor's claims) or ``senior_first`` (lowest priority
number first, pro rata inside a level).  Cyclic debt makes the payments
simultaneous, so the clearing vector is a fixed point of a monotone
piecewise-linear map; zero-cash cycles admit many fixed points and the
contractually required one is the *greatest*.

The greatest fixed point is computed by enumerating every segment
assignment of the map (each debtor is either paying everything it has on
one linear waterfall segment, or pays its debt in full), solving each
induced linear system exactly with :class:`fractions.Fraction`, and taking
the componentwise greatest consistent fixed point.  Maximality is certified
independently of the enumeration: on the defaulting set the right-derivative
supergradient forms an M-matrix, so any clearing vector is componentwise
bounded by the computed one.  No bilateral netting, no row-order payments,
no arbitrary fixed point.
"""
from __future__ import annotations

from collections import defaultdict, namedtuple
from fractions import Fraction
from itertools import product

ZERO = Fraction(0)
Obligation = namedtuple("Obligation", "debt_id debtor creditor amount priority")
MODES = ("pari_passu", "senior_first")


def clear_network(parties, cash_cents: dict, obligations, mode: str) -> dict:
    """Solve the clearing equations for the greatest fixed point, exactly."""
    if mode not in MODES:
        raise ValueError(f"unknown clearing mode: {mode!r}")
    parties = tuple(sorted(parties))
    cash = {party: Fraction(cash_cents[party]) for party in parties}
    books = _compile_books(obligations, mode)
    found, checked, consistent = _enumerate_fixed_points(books, cash, parties)
    if not found:
        raise AssertionError("clearing equations have no consistent fixed point")
    paid = {party: max(vector[party] for vector in found) for party in parties}
    available, _ = _availability(books, cash, parties, paid)
    residual = max(
        (abs(min(books[p]["total"], available[p]) - paid[p]) for p in books),
        default=ZERO,
    )
    if residual:
        raise AssertionError(f"greatest candidate is not a fixed point: {residual}")
    defaulting, certified = _maximality_certificate(books, paid)
    claims = {
        o.debt_id: _claim_payment(books[o.debtor], o, paid[o.debtor])
        for o in obligations
    }
    vectors = tuple(sorted(found, key=lambda v: tuple(v[p] for p in parties)))
    return {
        "paid": paid,
        "available": available,
        "claim_payments": claims,
        "claim_payments_cents": _cents_allocation(obligations, claims),
        "fixed_points_found": vectors,
        "assignments_enumerated": checked,
        "consistent_assignments": consistent,
        "defaulting": defaulting,
        "m_matrix_certified": certified,
        "residual": residual,
    }


def clearing_map(parties, cash_cents: dict, obligations, mode: str, paid: dict) -> dict:
    """Apply the clearing map Φ once; fixed points satisfy Φ(p) == p."""
    parties = tuple(sorted(parties))
    cash = {party: Fraction(cash_cents[party]) for party in parties}
    books = _compile_books(obligations, mode)
    available, _ = _availability(books, cash, parties, paid)
    return {
        p: min(books[p]["total"], available[p]) if p in books else ZERO
        for p in parties
    }


def _compile_books(obligations, mode: str) -> dict:
    """Per debtor: claims, total debt and the piecewise-linear waterfall."""
    by_debtor = defaultdict(list)
    for obligation in obligations:
        by_debtor[obligation.debtor].append(obligation)
    books = {}
    for debtor in sorted(by_debtor):
        claims = tuple(sorted(by_debtor[debtor], key=lambda o: o.debt_id))
        total = Fraction(sum(o.amount for o in claims))
        full = defaultdict(lambda: ZERO)
        for claim in claims:
            full[claim.creditor] += Fraction(claim.amount)
        segments = []
        if mode == "pari_passu":
            segments.append(
                (ZERO, total, {c: a / total for c, a in full.items()}, {c: ZERO for c in full})
            )
        else:
            cumulative = ZERO
            for level in sorted({o.priority for o in claims}):
                level_total = Fraction(sum(o.amount for o in claims if o.priority == level))
                slopes, offsets = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
                for claim in claims:
                    if claim.priority < level:
                        offsets[claim.creditor] += Fraction(claim.amount)
                    elif claim.priority == level:
                        share = Fraction(claim.amount) / level_total
                        slopes[claim.creditor] += share
                        offsets[claim.creditor] -= cumulative * share
                segments.append((cumulative, cumulative + level_total, dict(slopes), dict(offsets)))
                cumulative += level_total
        books[debtor] = {
            "claims": claims, "total": total,
            "segments": tuple(segments), "full": dict(full),
        }
    return books


def _outflows(book: dict, payment: Fraction) -> dict:
    for _, hi, slopes, offsets in book["segments"]:
        if payment <= hi:
            return {
                c: offsets.get(c, ZERO) + slopes.get(c, ZERO) * payment
                for c in book["full"]
            }
    raise AssertionError(f"payment {payment} exceeds total debt {book['total']}")


def _availability(books, cash, parties, paid):
    inflow = {party: ZERO for party in parties}
    for debtor, book in books.items():
        for creditor, amount in _outflows(book, paid[debtor]).items():
            inflow[creditor] += amount
    return {p: cash[p] + inflow[p] for p in parties}, inflow


def _enumerate_fixed_points(books, cash, parties):
    debtors = sorted(books)
    options = [
        [("free", i) for i in range(len(books[d]["segments"]))] + [("full", 0)]
        for d in debtors
    ]
    found, checked, consistent = {}, 0, 0
    for combo in product(*options):
        checked += 1
        vector = _solve_assignment(books, cash, parties, debtors, combo)
        if vector is not None:
            consistent += 1
            found.setdefault(tuple(vector[p] for p in parties), vector)
    return tuple(found.values()), checked, consistent


def _solve_assignment(books, cash, parties, debtors, combo):
    free = [(d, seg) for d, (kind, seg) in zip(debtors, combo) if kind == "free"]
    index = {d: i for i, (d, _) in enumerate(free)}
    matrix = [[Fraction(i == j) for j in range(len(free))] for i in range(len(free))]
    rhs = [cash[d] for d, _ in free]
    for column, (debtor, seg) in enumerate(free):
        _, _, slopes, offsets = books[debtor]["segments"][seg]
        for creditor, slope in slopes.items():
            if creditor in index:
                matrix[index[creditor]][column] -= slope
        for creditor, offset in offsets.items():
            if creditor in index:
                rhs[index[creditor]] += offset
    for debtor, (kind, _) in zip(debtors, combo):
        if kind == "full":
            for creditor, amount in books[debtor]["full"].items():
                if creditor in index:
                    rhs[index[creditor]] += amount
    solved = _gauss_jordan(matrix, [[value] for value in rhs])
    if solved is None:
        return None
    paid = {party: ZERO for party in parties}
    for (debtor, seg), row in zip(free, solved):
        lo, hi = books[debtor]["segments"][seg][:2]
        if not lo <= row[0] <= hi:
            return None
        paid[debtor] = row[0]
    for debtor, (kind, _) in zip(debtors, combo):
        if kind == "full":
            paid[debtor] = books[debtor]["total"]
    _, inflow = _availability(books, cash, parties, paid)
    for debtor, (kind, _) in zip(debtors, combo):
        if kind == "full" and cash[debtor] + inflow[debtor] < books[debtor]["total"]:
            return None
    return paid


def _gauss_jordan(matrix, rhs):
    """Exact Gauss-Jordan solve of matrix·x = rhs; None when singular."""
    size = len(matrix)
    aug = [matrix[i][:] + list(rhs[i]) for i in range(size)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda r: abs(aug[r][col]))
        if aug[pivot][col] == 0:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        inv = 1 / aug[col][col]
        aug[col] = [value * inv for value in aug[col]]
        for row in range(size):
            if row != col and aug[row][col]:
                factor = aug[row][col]
                aug[row] = [a - factor * b for a, b in zip(aug[row], aug[col])]
    return [row[size:] for row in aug]


def _maximality_certificate(books, paid):
    defaulting = tuple(d for d in sorted(books) if paid[d] < books[d]["total"])
    if not defaulting:
        return defaulting, True
    index = {d: i for i, d in enumerate(defaulting)}
    size = len(defaulting)
    matrix = [[Fraction(i == j) for j in range(size)] for i in range(size)]
    for column, debtor in enumerate(defaulting):
        segment = next(s for s in books[debtor]["segments"] if paid[debtor] < s[1])
        for creditor, slope in segment[2].items():
            if creditor in index:
                matrix[index[creditor]][column] -= slope
    identity = [[Fraction(i == j) for j in range(size)] for i in range(size)]
    inverse = _gauss_jordan(matrix, identity)
    if inverse is None or any(value < 0 for row in inverse for value in row):
        raise AssertionError("greatest fixed point lacks an M-matrix certificate")
    return defaulting, True


def _claim_payment(book: dict, claim, payment: Fraction) -> Fraction:
    """Exact payment to one claim under the debtor's allocation rule."""
    if len(book["segments"]) == 1:
        # pari_passu, or senior_first with one level: pro rata over claims.
        return payment * Fraction(claim.amount) / book["total"]
    for level in sorted({o.priority for o in book["claims"]}):
        level_total = Fraction(
            sum(o.amount for o in book["claims"] if o.priority == level)
        )
        pay_level = min(payment, level_total)
        if claim.priority == level:
            return pay_level * Fraction(claim.amount) / level_total
        payment -= pay_level
    raise AssertionError("claim outside the waterfall")


def _cents_allocation(obligations, claim_payments) -> dict:
    """Deterministic per-debtor largest-remainder rounding to cents.

    Each debtor's cent total is the floor of its exact total; the remaining
    cents go to the claims with the largest fractional parts (debt_id order
    breaks ties).  Sub-cent remainders stay with the payer, so cash is
    neither created nor destroyed.
    """
    by_debtor = defaultdict(list)
    for obligation in obligations:
        by_debtor[obligation.debtor].append(obligation)
    cents = {}
    for debtor in sorted(by_debtor):
        claims = sorted(by_debtor[debtor], key=lambda o: o.debt_id)
        floors = {
            o.debt_id: claim_payments[o.debt_id].numerator // claim_payments[o.debt_id].denominator
            for o in claims
        }
        total = sum(claim_payments[o.debt_id] for o in claims)
        remaining = total.numerator // total.denominator - sum(floors.values())
        order = sorted(
            claims,
            key=lambda o: (floors[o.debt_id] - claim_payments[o.debt_id], o.debt_id),
        )
        for obligation in order[:remaining]:
            floors[obligation.debt_id] += 1
        cents.update(floors)
    return cents
