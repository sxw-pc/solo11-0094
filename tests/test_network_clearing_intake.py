import csv
import json
import shutil
import sqlite3
from fractions import Fraction
from pathlib import Path

import pytest
from tools.fixture_data import FIXTURES
from tzgate.network_clearing_intake import (
    ClearingInputError, Obligation, build_obligations, clearing_map,
    clear_network, load_clearing_intake, preview_gross_liabilities,
    run_clearing, write_clearing_outputs,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/network_clearing.yaml"
INTAKE = ROOT / "fixtures/network_clearing/intake.json"
HEADER = FIXTURES["clearing_debts"]["header"]
ROWS = FIXTURES["clearing_debts"]["files"][0][1]
RULES = json.loads(INTAKE.read_text(encoding="utf-8"))
META = {"run_id": "t", "contract_sha256": "c", "rulebook_sha256": "r",
        "file": "f", "file_sha256": "s"}
RULES_SMALL = {"dataset": "clearing_debts", "parties": ["A", "B", "C"],
               "external_cash_cents": {"A": 0, "B": 60, "C": 0},
               "regimes": RULES["regimes"],
               "limits": {"max_parties": 5, "max_debts": 8, "max_priority_levels": 2}}


def load_data(run_id="clear-run"):
    return load_clearing_intake(CONTRACT, ROOT, INTAKE, run_id)


@pytest.fixture(scope="module")
def report():
    return run_clearing(load_data())


def paid(rep, regime):
    return {p["debt_id"]: p["amount_exact"] for p in rep["regimes"][regime]["payments"]}


def test_intake_and_gross_preview():
    data = load_data()
    assert len(data["debts"]) == 6 and data["file_sha256"]
    assert data["rules"]["regimes"]["senior_first"]["fixed_point"] == "greatest"
    assert preview_gross_liabilities(data) == {"A": 200, "B": 100, "C": 50, "D": 30, "E": 30}
    obligations = build_obligations(data)
    assert [o.debt_id for o in obligations] == ["ab", "ac", "ba", "cb", "de", "ed"]


def test_pari_passu_fixture_clearing(report):
    assert paid(report, "pari_passu") == {
        "ab": "50", "ac": "50", "ba": "100", "cb": "50", "de": "30", "ed": "30"}
    debtors = report["regimes"]["pari_passu"]["debtors"]
    assert debtors["A"]["available"] == "100" and debtors["A"]["status"] == "funds_exhausted"
    assert debtors["B"]["retained"] == "60"  # B 最终留存现金 60
    assert debtors["C"]["available"] == "50"  # C 从 A 实收 50


def test_senior_first_fixture_clearing(report):
    assert paid(report, "senior_first") == {
        "ab": "0", "ac": "100", "ba": "100", "cb": "50", "de": "30", "ed": "30"}
    debtors = report["regimes"]["senior_first"]["debtors"]
    assert debtors["B"]["retained"] == "10"  # B 最终留存现金 10
    assert debtors["C"]["available"] == "100"  # C 从 A 实收 100
    assert paid(report, "senior_first")["ac"] != paid(report, "pari_passu")["ac"]


def test_report_pins_contract_run_rulebook_and_file(report):
    data = load_data("pin")
    assert report["run_id"] == "clear-run"
    for key in ("contract_sha256", "rulebook_sha256", "file_sha256"):
        assert report[key] == data[key]
    assert report["file"].endswith("part-0001.csv")
    assert sorted(report["regimes"]) == ["pari_passu", "senior_first"]
    for regime in report["regimes"].values():
        for key in ("run_id", "contract_sha256", "rulebook_sha256", "file", "file_sha256"):
            assert regime[key] == report[key]


def test_fixed_point_and_conservation_evidence(report):
    data = load_data("evidence")
    obligations = build_obligations(data)
    cash = data["rules"]["external_cash_cents"]
    for name, regime in report["regimes"].items():
        evidence = regime["evidence"]
        assert evidence["fixed_point_residual"] == "0"
        assert evidence["maximality"]["supergradient_m_matrix"] is True
        assert evidence["maximality"]["defaulting_debtors"] == ["A"]
        assert evidence["retained_total_exact"] == str(evidence["external_cash_total_cents"])
        vector = {p: Fraction(a) for p, a in regime["fixed_point_vector"].items()}
        assert clearing_map(report["parties"], cash, obligations, name, vector) == vector
        for entry in regime["debtors"].values():
            assert Fraction(entry["paid"]) + Fraction(entry["retained"]) == Fraction(entry["available"])
            assert Fraction(entry["paid"]) <= Fraction(entry["total_debt"])
        for payment in regime["payments"]:
            assert Fraction(payment["amount_exact"]) == payment["amount_cents"]


def test_zero_cash_cycle_selects_greatest_fixed_point():
    obligations = (Obligation("de", "D", "E", 30, 1), Obligation("ed", "E", "D", 30, 1))
    cash = {"D": 0, "E": 0}
    zero = {"D": Fraction(0), "E": Fraction(0)}
    full = {"D": Fraction(30), "E": Fraction(30)}
    assert clearing_map(("D", "E"), cash, obligations, "pari_passu", zero) == zero
    assert clearing_map(("D", "E"), cash, obligations, "pari_passu", full) == full
    result = clear_network(("D", "E"), cash, obligations, "pari_passu")
    assert result["paid"] == full
    assert result["claim_payments"] == {"de": Fraction(30), "ed": Fraction(30)}


def test_insufficient_cash_pays_only_available():
    result = clear_network(("A", "B"), {"A": 30, "B": 0}, (Obligation("ab", "A", "B", 100, 1),), "pari_passu")
    assert result["paid"]["A"] == Fraction(30)
    assert result["claim_payments"]["ab"] == Fraction(30)
    assert result["defaulting"] == ("A",)


def test_senior_first_same_level_pro_rata():
    obligations = (Obligation("ab", "A", "B", 100, 1), Obligation("ac", "A", "C", 50, 1),
                   Obligation("ad", "A", "D", 100, 2))
    result = clear_network(("A", "B", "C", "D"), {"A": 90, "B": 0, "C": 0, "D": 0}, obligations, "senior_first")
    assert result["claim_payments"]["ab"] == Fraction(60)
    assert result["claim_payments"]["ac"] == Fraction(30)
    assert result["claim_payments"]["ad"] == Fraction(0)


def test_exact_fractions_and_deterministic_cent_rounding():
    obligations = (Obligation("ab", "A", "B", 100, 1), Obligation("ac", "A", "C", 50, 1))
    result = clear_network(("A", "B", "C"), {"A": 100, "B": 0, "C": 0}, obligations, "pari_passu")
    assert result["claim_payments"]["ab"] == Fraction(200, 3)
    assert result["claim_payments"]["ac"] == Fraction(100, 3)
    assert sum(result["claim_payments"].values()) == Fraction(100)
    assert result["claim_payments_cents"] == {"ab": 67, "ac": 33}


def test_mutual_debt_and_isolated_party():
    obligations = (Obligation("ab", "A", "B", 40, 1), Obligation("ba", "B", "A", 40, 1),
                   Obligation("aw", "A", "W", 10, 1))
    cash = {"A": 10, "B": 0, "W": 0, "Z": 25}
    result = clear_network(("A", "B", "W", "Z"), cash, obligations, "pari_passu")
    assert result["paid"]["A"] == Fraction(50) and result["paid"]["B"] == Fraction(40)
    assert result["claim_payments"]["aw"] == Fraction(10)
    assert result["paid"]["Z"] == Fraction(0) and result["available"]["Z"] == Fraction(25)
    assert result["paid"]["W"] == Fraction(0) and result["available"]["W"] == Fraction(10)


def test_row_order_does_not_change_clearing():
    rows = [{"debt_id": d, "debtor": a, "creditor": b, "amount_cents": str(x), "priority": str(p)}
            for d, a, b, x, p in (("ab", "A", "B", 100, 2), ("ac", "A", "C", 100, 1), ("ba", "B", "A", 100, 1))]
    forward = run_clearing({**META, "rules": RULES_SMALL, "debts": rows})
    backward = run_clearing({**META, "rules": RULES_SMALL, "debts": list(reversed(rows))})
    for name in forward["regimes"]:
        assert forward["regimes"][name]["payments"] == backward["regimes"][name]["payments"]
        assert forward["regimes"][name]["debtors"] == backward["regimes"][name]["debtors"]


def _row(debt_id, debtor, creditor, amount, priority):
    return {"debt_id": debt_id, "debtor": debtor, "creditor": creditor,
            "amount_cents": amount, "priority": priority}


@pytest.mark.parametrize("rows", (
    [_row("ab", "A", "B", "100", "1"), _row("ab", "A", "C", "5", "1")],  # 重复 debt_id
    [_row("aa", "A", "A", "100", "1")],  # 自欠
    [_row("az", "A", "Z", "100", "1")],  # 未知债权人
    [_row("za", "Z", "A", "100", "1")],  # 未知债务人
    [_row("ab", "A", "B", "0", "1")], [_row("ab", "A", "B", "-5", "1")],
    [_row("ab", "A", "B", "abc", "1")], [_row("ab", "A", "B", "", "1")],  # 非正数金额
    [_row("ab", "A", "B", "100", "0")], [_row("ab", "A", "B", "100", "3")],
    [_row("ab", "A", "B", "100", "x")],  # 非法优先级
))
def test_invalid_debt_rows_are_rejected(rows):
    with pytest.raises(ClearingInputError):
        build_obligations({**META, "rules": RULES, "debts": rows})


def test_too_many_debts_rejected():
    rows = [_row(f"d{i}", "A", "B", "1", "1") for i in range(9)]
    with pytest.raises(ClearingInputError, match="too many debts"):
        build_obligations({**META, "rules": RULES, "debts": rows})


@pytest.mark.parametrize("rules", (
    {**RULES, "external_cash_cents": {"A": 0, "B": -1, "C": 0, "D": 0, "E": 0}},  # 负现金
    {**RULES, "external_cash_cents": {"A": 0, "B": "60", "C": 0, "D": 0, "E": 0}},  # 非整数现金
    {**RULES, "external_cash_cents": {"A": 0, "B": 60, "C": 0, "D": 0, "E": 0, "Z": 1}},  # 未知主体
    {**RULES, "external_cash_cents": {"A": 0, "B": 60, "C": 0, "D": 0}},  # 缺少主体
    {**RULES, "parties": ["A", "B", "C", "D", "E", "F"]},  # 主体超限
    {**RULES, "regimes": {"pari_passu": {"within_debtor": "pro_rata_all_claims", "fixed_point": "least"}}},
    {**RULES, "regimes": {"odd": {"within_debtor": "random", "fixed_point": "greatest"}}},
))
def test_invalid_rules_are_rejected(rules):
    with pytest.raises(ClearingInputError):
        build_obligations({**META, "rules": rules, "debts": [_row("ab", "A", "B", "100", "1")]})


def make_root(tmp_path, rows=ROWS, contract_text=None):
    root = tmp_path / "root"
    (root / "contracts").mkdir(parents=True)
    (root / "contracts" / "network_clearing.yaml").write_text(
        contract_text or CONTRACT.read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copy(ROOT / "contracts" / "timezone_rules.yaml", root / "contracts")
    partition = root / "fixtures" / "lake" / "clearing_debts" / "dt=2024-07-01"
    partition.mkdir(parents=True)
    with (partition / "part-0001.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)
    (root / "fixtures" / "network_clearing").mkdir(parents=True)
    (root / "fixtures" / "network_clearing" / "intake.json").write_text(
        json.dumps(RULES, indent=2) + "\n", encoding="utf-8")
    return root


def clear_tmp_root(root):
    data = load_clearing_intake(root / "contracts" / "network_clearing.yaml", root,
                                root / "fixtures" / "network_clearing" / "intake.json", "tmp-run")
    return run_clearing(data)


def edit_rows(**changes):
    return tuple((d, ts, s, dr, cr, changes.get(d, (a, p))[0], changes.get(d, (a, p))[1])
                 for d, ts, s, dr, cr, a, p in ROWS)


def test_row_permutation_keeps_payments(tmp_path):
    reference = run_clearing(load_data("ref"))
    replayed = clear_tmp_root(make_root(tmp_path, rows=tuple(reversed(ROWS))))
    assert replayed["file_sha256"] != reference["file_sha256"]
    assert replayed["obligations"] == reference["obligations"]
    for name in reference["regimes"]:
        assert replayed["regimes"][name]["payments"] == reference["regimes"][name]["payments"]


def test_replay_is_byte_identical(tmp_path):
    first, second = run_clearing(load_data("replay")), run_clearing(load_data("replay"))
    assert first == second
    out_a, out_b = write_clearing_outputs(first, tmp_path / "a"), write_clearing_outputs(second, tmp_path / "b")
    assert out_a["clearing_json"].read_bytes() == out_b["clearing_json"].read_bytes()


def test_amount_change_drives_clearing_not_just_hashes(tmp_path):
    changed = clear_tmp_root(make_root(tmp_path, rows=edit_rows(cb=("20", "1"))))
    reference = run_clearing(load_data("ref2"))
    assert changed["file_sha256"] != reference["file_sha256"]
    assert paid(changed, "pari_passu")["cb"] == "20"
    senior = paid(changed, "senior_first")
    assert (senior["ba"], senior["ac"], senior["ab"], senior["cb"]) == ("80", "80", "0", "20")


def test_priority_change_drives_senior_first_clearing(tmp_path):
    changed = clear_tmp_root(make_root(tmp_path, rows=edit_rows(ab=("100", "1"))))
    senior = paid(changed, "senior_first")
    assert (senior["ab"], senior["ac"]) == ("50", "50")  # 同级后按比例，不再先偿 ac


def test_contract_change_recomputes_and_rebinds_hashes(tmp_path):
    text = CONTRACT.read_text(encoding="utf-8").replace("合成互负债务的清算湖表。", "合成互负债务的清算湖表（变体）。")
    changed = clear_tmp_root(make_root(tmp_path, contract_text=text))
    reference = run_clearing(load_data("ref3"))
    assert changed["contract_sha256"] != reference["contract_sha256"]
    assert paid(changed, "pari_passu") == paid(reference, "pari_passu")


def test_source_issue_invalidates_the_intake(tmp_path):
    rows = tuple((d, "not-a-timestamp" if d == "ab" else ts, s, dr, cr, a, p)
                 for d, ts, s, dr, cr, a, p in ROWS)
    root = make_root(tmp_path, rows=rows)
    with pytest.raises(ValueError, match="verify cleanly"):
        load_clearing_intake(root / "contracts" / "network_clearing.yaml", root,
                             root / "fixtures" / "network_clearing" / "intake.json", "bad")


def test_missing_source_file_invalidates_the_intake(tmp_path):
    root = make_root(tmp_path)
    (root / "fixtures" / "lake" / "clearing_debts" / "dt=2024-07-01" / "part-0001.csv").unlink()
    with pytest.raises(ValueError):
        load_clearing_intake(root / "contracts" / "network_clearing.yaml", root,
                             root / "fixtures" / "network_clearing" / "intake.json", "gone")


def test_clearing_outputs_write_json_and_sqlite(report, tmp_path):
    paths = write_clearing_outputs(report, tmp_path / "clearing")
    assert json.loads(paths["clearing_json"].read_text(encoding="utf-8")) == report
    connection = sqlite3.connect(paths["clearing_ledger"])
    try:
        runs = connection.execute("SELECT regime, defaulting FROM clearing_run ORDER BY regime").fetchall()
        assert runs == [("pari_passu", "A"), ("senior_first", "A")]
        assert connection.execute("SELECT COUNT(*) FROM clearing_debtor").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM clearing_payment").fetchone()[0] == 12
        row = connection.execute(
            "SELECT amount_exact, amount_cents, unpaid_exact FROM clearing_payment "
            "WHERE regime='senior_first' AND debt_id='ac'").fetchone()
        assert row == ("100", 100, "0")
    finally:
        connection.close()
