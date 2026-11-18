"""Verified synthetic debt network intake and exact network clearing.

The intake pins one verified debt file through the tzgate engine and builds
deterministic debtor→creditor obligations from it.  Clearing then solves
the simultaneous network equations for the *greatest* non-negative fixed
point with exact rational arithmetic (see :mod:`tzgate.clearing`), under
the two claim regimes declared by the intake rules: ``pari_passu`` and
``senior_first``.  Nothing here nets bilateral pairs, pays in CSV row
order, or accepts just any fixed point.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

from .clearing import Obligation, clearing_map, clear_network
from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import atomic_write_text, canonical_json_text, sha256_bytes
from .ledger import write_clearing_ledger

__all__ = [
    "ClearingInputError", "Obligation", "build_obligations", "clear_network",
    "clearing_map", "load_clearing_intake", "preview_gross_liabilities",
    "run_clearing", "validate_rules", "write_clearing_outputs",
]

_MODES = {
    "pro_rata_all_claims": "pari_passu",
    "lowest_priority_number_first_then_pro_rata": "senior_first",
}
# Hard bounds for the offline exact solver: assignment enumeration is
# exponential in the number of debtors, so the contract pins tiny inputs.
_LIMITS = {"max_parties": 5, "max_debts": 8, "max_priority_levels": 2}
_ROUNDING_NOTE = (
    "分币采用最大余数法：每个债务人先将其各债权的精确付款向下取整，再把总额取整后"
    "剩余的分币按分数余量降序、debt_id 升序依次补一分；不足一分的零头留存于付款人，"
    "现金既不创造也不销毁。"
)
_CONSERVATION_NOTE = (
    "每个债务人 paid + retained == available，且 available == 外部现金 + 实收流入；"
    "收款方入账与付款方出账是同一批边级款项；全体留存之和 == 外部现金总额。"
)
_MAXIMALITY_NOTE = (
    "枚举单调分段线性清算映射的全部分段指派并精确求解，取逐分量最大的一致固定点；"
    "违约债务人集合上的右导数超梯度构成 M-矩阵（逆非负），故任何清算向量逐分量"
    "不超过该解，即合同约定的最大清算固定点。"
)


class ClearingInputError(ValueError):
    """Raised when the intake rules or the verified debt rows are invalid."""


def load_clearing_intake(
    contract_path: Path, root: Path, intake_path: Path, run_id: str
) -> dict:
    rules = json.loads(intake_path.read_text(encoding="utf-8"))
    result = run_verification(contract_path, root, run_id)
    if result.status != "pass":
        raise ValueError("clearing debt dataset must verify cleanly")
    by_name = {d.spec.name: d for d in result.datasets}
    if set(by_name) != {rules["dataset"]}:
        raise ValueError("clearing dataset disagrees")
    dataset = by_name[rules["dataset"]]
    refs = discover_dataset(dataset.spec).files
    if dataset.issue_count or len(refs) != 1:
        raise ValueError("one clean clearing file required")
    ref = refs[0]
    with ref.path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != dataset.record_count:
        raise ValueError("verified row count changed")
    return {
        "run_id": run_id,
        "contract_sha256": result.contract.sha256,
        "rulebook_sha256": result.contract.rulebook_sha256,
        "file": ref.label,
        "file_sha256": sha256_bytes(ref.path.read_bytes()),
        "debts": rows,
        "rules": rules,
    }


def preview_gross_liabilities(data: dict) -> dict:
    """Nominal debt totals; no estate clearing or cycle settlement."""
    totals = defaultdict(int)
    for debt in data["debts"]:
        totals[debt["debtor"]] += int(debt["amount_cents"])
    return dict(sorted(totals.items()))


def validate_rules(rules: object) -> dict:
    """Validate the intake JSON and return a normalized, solver-ready copy."""
    if not isinstance(rules, dict):
        raise ClearingInputError("intake rules must be a JSON object")
    dataset = rules.get("dataset")
    if not isinstance(dataset, str) or not dataset.strip():
        raise ClearingInputError("intake rules must name the dataset")
    raw_limits = rules.get("limits", _LIMITS)
    if not isinstance(raw_limits, dict):
        raise ClearingInputError("intake limits must be a JSON object")
    limits = {}
    for key, default in _LIMITS.items():
        value = raw_limits.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= default:
            raise ClearingInputError(f"intake limit {key} must be within 1..{default}")
        limits[key] = value
    raw_parties = rules.get("parties")
    if not isinstance(raw_parties, list) or not raw_parties:
        raise ClearingInputError("intake parties must be a non-empty list")
    if not all(isinstance(p, str) and p.strip() for p in raw_parties):
        raise ClearingInputError("party names must be non-empty strings")
    parties = tuple(sorted({p.strip() for p in raw_parties}))
    if len(parties) != len(raw_parties):
        raise ClearingInputError("duplicate parties")
    if len(parties) > limits["max_parties"]:
        raise ClearingInputError(f"too many parties: {len(parties)} > {limits['max_parties']}")
    raw_cash = rules.get("external_cash_cents")
    if not isinstance(raw_cash, dict) or set(raw_cash) != set(parties):
        raise ClearingInputError("external_cash_cents must name exactly the parties")
    cash = {}
    for party in parties:
        value = raw_cash[party]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ClearingInputError(f"external cash for {party} must be non-negative integer cents")
        cash[party] = value
    raw_regimes = rules.get("regimes")
    if not isinstance(raw_regimes, dict) or not raw_regimes:
        raise ClearingInputError("intake regimes must be a non-empty JSON object")
    regimes = {}
    for name in sorted(raw_regimes):
        spec = raw_regimes[name]
        within = spec.get("within_debtor") if isinstance(spec, dict) else None
        if within not in _MODES or spec.get("fixed_point") != "greatest":
            raise ClearingInputError(
                f"regime {name} needs a known within_debtor rule and the greatest fixed point"
            )
        regimes[name] = {"within_debtor": within, "mode": _MODES[within]}
    return {
        "dataset": dataset.strip(), "parties": parties, "cash": cash,
        "regimes": regimes, "limits": limits,
    }


def build_obligations(data: dict) -> tuple:
    """Validate the verified debt rows into canonical obligations.

    Rows are sorted by ``debt_id`` so CSV row order can never leak into the
    clearing equations or the reported payments.
    """
    rules = validate_rules(data["rules"])
    parties, limits = set(rules["parties"]), rules["limits"]
    rows = data["debts"]
    if len(rows) > limits["max_debts"]:
        raise ClearingInputError(f"too many debts: {len(rows)} > {limits['max_debts']}")
    seen, obligations = set(), []
    for row in rows:
        cell = {k: (v.strip() if isinstance(v, str) else "") for k, v in row.items()}
        debt_id = cell.get("debt_id", "")
        if not debt_id or debt_id in seen:
            raise ClearingInputError(f"missing or duplicate debt_id: {debt_id!r}")
        seen.add(debt_id)
        debtor, creditor = cell.get("debtor", ""), cell.get("creditor", "")
        if debtor not in parties or creditor not in parties:
            raise ClearingInputError(f"debt {debt_id}: unknown debtor or creditor party")
        if debtor == creditor:
            raise ClearingInputError(f"debt {debt_id}: self-debt is not allowed")
        amount, priority = cell.get("amount_cents", ""), cell.get("priority", "")
        if not amount.isdigit() or int(amount) <= 0:
            raise ClearingInputError(f"debt {debt_id}: amount_cents must be a positive integer")
        if not priority.isdigit() or not 1 <= int(priority) <= limits["max_priority_levels"]:
            raise ClearingInputError(f"debt {debt_id}: invalid priority level")
        obligations.append(Obligation(debt_id, debtor, creditor, int(amount), int(priority)))
    return tuple(sorted(obligations, key=lambda item: item.debt_id))


def run_clearing(data: dict) -> dict:
    """Build the full per-regime clearing report from the verified intake."""
    rules = validate_rules(data["rules"])
    obligations = build_obligations(data)
    parties, cash = rules["parties"], rules["cash"]
    regimes = {}
    for name in sorted(rules["regimes"]):
        spec = rules["regimes"][name]
        result = clear_network(parties, cash, obligations, spec["mode"])
        regimes[name] = _regime_report(parties, cash, obligations, spec, result)
        for key in ("run_id", "contract_sha256", "rulebook_sha256", "file", "file_sha256"):
            regimes[name][key] = data[key]
    return {
        "schema": "tzgate.clearing/1",
        "run_id": data["run_id"],
        "dataset": rules["dataset"],
        "contract_sha256": data["contract_sha256"],
        "rulebook_sha256": data["rulebook_sha256"],
        "file": data["file"],
        "file_sha256": data["file_sha256"],
        "parties": list(parties),
        "external_cash_cents": {party: cash[party] for party in parties},
        "obligations": [
            {
                "debt_id": o.debt_id,
                "debtor": o.debtor,
                "creditor": o.creditor,
                "amount_cents": o.amount,
                "priority": o.priority,
            }
            for o in obligations
        ],
        "regimes": regimes,
    }


def _regime_report(parties, cash, obligations, spec, result) -> dict:
    totals = defaultdict(lambda: Fraction(0))
    for obligation in obligations:
        totals[obligation.debtor] += Fraction(obligation.amount)
    debtors = {}
    for party in parties:
        total, paid = totals[party], result["paid"][party]
        available = result["available"][party]
        status = "no_obligations" if total == 0 else "paid_in_full" if paid == total else "funds_exhausted"
        debtors[party] = {
            "total_debt": _exact(total),
            "available": _exact(available),
            "paid": _exact(paid),
            "retained": _exact(available - paid),
            "unpaid": _exact(total - paid),
            "paid_cents": sum(
                result["claim_payments_cents"][o.debt_id]
                for o in obligations
                if o.debtor == party
            ),
            "status": status,
        }
    payments = [
        {
            "debt_id": o.debt_id,
            "debtor": o.debtor,
            "creditor": o.creditor,
            "priority": o.priority,
            "claim_cents": o.amount,
            "amount_exact": _exact(result["claim_payments"][o.debt_id]),
            "amount_cents": result["claim_payments_cents"][o.debt_id],
            "unpaid_exact": _exact(Fraction(o.amount) - result["claim_payments"][o.debt_id]),
            "unpaid_cents": o.amount - result["claim_payments_cents"][o.debt_id],
        }
        for o in obligations
    ]
    retained_total = sum(result["available"][p] - result["paid"][p] for p in parties)
    return {
        "within_debtor": spec["within_debtor"],
        "fixed_point": "greatest",
        "debtors": debtors,
        "payments": payments,
        "fixed_point_vector": {p: _exact(result["paid"][p]) for p in parties},
        "evidence": {
            "fixed_point_residual": _exact(result["residual"]),
            "conservation": _CONSERVATION_NOTE,
            "external_cash_total_cents": sum(cash.values()),
            "retained_total_exact": _exact(retained_total),
            "maximality": {
                "method": _MAXIMALITY_NOTE,
                "defaulting_debtors": list(result["defaulting"]),
                "supergradient_m_matrix": result["m_matrix_certified"],
                "assignments_enumerated": result["assignments_enumerated"],
                "consistent_assignments": result["consistent_assignments"],
                "fixed_points_found": [
                    {party: _exact(vector[party]) for party in parties}
                    for vector in result["fixed_points_found"]
                ],
            },
            "rounding": _ROUNDING_NOTE,
        },
    }


def _exact(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def write_clearing_outputs(report: dict, out_dir: Path) -> dict:
    """Persist the clearing report as canonical JSON and a SQLite ledger."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "clearing.json"
    ledger_path = out_dir / "clearing.sqlite3"
    atomic_write_text(json_path, canonical_json_text(report))
    write_clearing_ledger(ledger_path, report)
    return {"clearing_json": json_path, "clearing_ledger": ledger_path}
