from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools.fixture_data import FIXTURES
from tzgate.io_utils import canonical_json_text, sha256_bytes
from tzgate.vessel_stowage_intake import (
    _evaluate,
    build_stowage_decision,
    load_stowage_intake,
    preview_gross_manifest,
    write_stowage_outputs,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "vessel_stowage.yaml"
INTAKE = ROOT / "fixtures" / "vessel_stowage" / "intake.json"
TS = "2024-07-01T10:00:00Z"
ROWS = [
    ("a", TS, "terminal", "4", "P1", "8"),
    ("b", TS, "terminal", "4", "P2", "7"),
    ("c", TS, "terminal", "2", "P1", "5"),
    ("d", TS, "terminal", "2", "P2", "4"),
]


def row(cid="x", mass="2", port="P1", freight="1", source="terminal", ts=TS):
    return (cid, ts, source, mass, port, freight)


def make_root(tmp_path, rows=ROWS, intake=None):
    root = tmp_path / "root"
    (root / "contracts").mkdir(parents=True)
    lake = root / "fixtures" / "lake" / "cargo_manifest" / "dt=2024-07-01"
    lake.mkdir(parents=True)
    (root / "fixtures" / "vessel_stowage").mkdir(parents=True)
    shutil.copy(ROOT / "contracts" / "timezone_rules.yaml", root / "contracts" / "timezone_rules.yaml")
    shutil.copy(CONTRACT, root / "contracts" / "vessel_stowage.yaml")
    rules = json.loads(INTAKE.read_text(encoding="utf-8"))
    if intake:
        rules.update(intake)
    (root / "fixtures" / "vessel_stowage" / "intake.json").write_text(
        json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    header = ",".join(FIXTURES["cargo_manifest"]["header"])
    (lake / "part-0001.csv").write_text("\n".join([header] + [",".join(r) for r in rows]) + "\n", encoding="utf-8")
    return root


def decide(root, run_id="stow-test"):
    return build_stowage_decision(root / "contracts" / "vessel_stowage.yaml", root,
                                  root / "fixtures" / "vessel_stowage" / "intake.json", run_id)


@pytest.fixture(scope="module")
def decision():
    return build_stowage_decision(CONTRACT, ROOT, INTAKE, "stow-test")


def evaluate(assignment, containers, max_rehandles=0):
    stacks = {"L": ["bottom", "top"], "R": ["bottom", "top"]}
    limits = {"initial": 2, "post": 4}
    return _evaluate(assignment, containers, stacks, ["P1", "P2"], limits, max_rehandles)


def box(cid, mass, port, freight=1):
    return {"container_id": cid, "mass_tons": mass, "unload_port": port,
            "freight_cents": freight, "source_id": "terminal"}


def test_verified_manifest_is_only_gross_preview():
    data = load_stowage_intake(ROOT / "contracts/vessel_stowage.yaml", ROOT,
                               ROOT / "fixtures/vessel_stowage/intake.json", "stow-run")
    assert len(data["containers"]) == 4
    assert data["file_sha256"]
    assert preview_gross_manifest(data) == {"count": 4, "mass_tons": 12, "freight_cents": 24}
    assert data["rules"]["contracts"]["economy_rehandle"]["max_rehandles_at_P1"] == 1
    assert data["rules"]["max_post_P1_mass_imbalance_tons"] == 4
    assert len(FIXTURES["cargo_manifest"]["files"][0][1]) == 4


def test_express_direct_plan(decision):
    plan = decision["plans"]["express_direct"]
    assert plan["carried"] == ["a", "b", "c"]
    assert plan["slots"] == {"L": {"bottom": "a", "top": None}, "R": {"bottom": "b", "top": "c"}}
    assert plan["initial_mass"] == {"per_stack": {"L": 4, "R": 6}, "imbalance": 2, "limit": 2}
    assert plan["post_departure_mass"] == {"port": "P1", "per_stack": {"L": 0, "R": 4}, "imbalance": 4, "limit": 4}
    assert (plan["rehandles"], plan["freight_cents"], plan["rehandle_cost_cents"], plan["net_cents"]) == (0, 20, 0, 20)
    p1, p2 = plan["port_calls"]
    assert [(a["action"], a["container"], a["stack"], a["layer"]) for a in p1["actions"]] == [
        ("unload", "c", "R", "top"), ("unload", "a", "L", "bottom")]
    assert [(a["action"], a["container"]) for a in p2["actions"]] == [("unload", "b")]
    assert plan["rejected"] == [
        {"container_id": "d", "reason": "承运该箱的最优可行方案净收益17分，低于中选方案20分"}]


def test_economy_rehandle_plan(decision):
    plan = decision["plans"]["economy_rehandle"]
    assert plan["carried"] == ["a", "b", "c", "d"]
    assert plan["slots"] == {"L": {"bottom": "a", "top": "d"}, "R": {"bottom": "b", "top": "c"}}
    assert plan["initial_mass"] == {"per_stack": {"L": 6, "R": 6}, "imbalance": 0, "limit": 2}
    assert plan["post_departure_mass"] == {"port": "P1", "per_stack": {"L": 2, "R": 4}, "imbalance": 2, "limit": 4}
    assert (plan["rehandles"], plan["freight_cents"], plan["rehandle_cost_cents"], plan["net_cents"]) == (1, 24, 1, 23)
    p1, p2 = plan["port_calls"]
    assert [(a["action"], a["container"], a["stack"], a["layer"]) for a in p1["actions"]] == [
        ("unload", "c", "R", "top"), ("lift_to_hold", "d", "L", "top"),
        ("unload", "a", "L", "bottom"), ("restow", "d", "L", "bottom")]
    assert [(a["action"], a["container"], a["stack"], a["layer"]) for a in p2["actions"]] == [
        ("unload", "d", "L", "bottom"), ("unload", "b", "R", "bottom")]


def test_express_all_four_infeasible_evidence(decision):
    full = decision["plans"]["express_direct"]["evidence"]["full_manifest_set"]
    assert full["ids"] == ["a", "b", "c", "d"]
    assert full["assignments_evaluated"] == 24
    assert full["feasible"] is False and full["feasible_assignments"] == 0
    counts = full["violation_counts"]
    assert counts["P1倒箱次数超限"] >= 1
    assert counts["P1离港后左右质量差超限"] >= 1
    assert counts["初航左右质量差超限"] >= 1
    economy_full = decision["plans"]["economy_rehandle"]["evidence"]["full_manifest_set"]
    assert economy_full["feasible"] is True


def test_action_sequence_proves_no_bad_lift(decision):
    ports = {c["container_id"]: c["unload_port"] for c in decision["containers"]}
    for plan in decision["plans"].values():
        assert plan["evidence"]["action_checks"]["ok"] is True
        assert plan["evidence"]["action_checks"]["problems"] == []
        for call in plan["port_calls"]:
            for action in call["actions"]:
                if action["action"] == "unload":
                    assert ports[action["container"]] == call["port"]


def test_layout_violations_detected():
    containers = [box("x", 2, "P1"), box("y", 4, "P2")]
    kinds, _ = evaluate({("L", 0): 0, ("L", 1): 1}, containers)
    assert "顶层重于底层" in kinds
    kinds, _ = evaluate({("L", 1): 0}, containers)
    assert "舱位悬空" in kinds
    kinds, _ = evaluate({("L", 0): 1, ("L", 1): 0}, containers)
    assert "初航左右质量差超限" in kinds  # 4+2 同堆，另一侧为空


def test_port_and_rehandle_violations_detected():
    containers = [box("a", 4, "P1"), box("b", 4, "P2"), box("c", 2, "P1"), box("d", 2, "P2")]
    kinds, _ = evaluate({("L", 0): 0, ("L", 1): 2, ("R", 0): 1, ("R", 1): 3}, containers)
    assert kinds == {"P1离港后左右质量差超限"}  # a/c 同堆、b/d 同堆：初航6/6，离港0/6
    trio = [box("a", 4, "P1"), box("b", 4, "P2"), box("d", 2, "P2")]
    kinds, _ = evaluate({("L", 0): 0, ("L", 1): 2, ("R", 0): 1}, trio, max_rehandles=0)
    assert kinds == {"P1倒箱次数超限"}
    kinds, detail = evaluate({("L", 0): 0, ("L", 1): 2, ("R", 0): 1}, trio, max_rehandles=1)
    assert kinds == set() and detail["restow"] == {2: ("L", 0)}


def test_port_order_drives_plan(tmp_path):
    doc = decide(make_root(tmp_path, intake={"port_order": ["P2", "P1"]}))
    plan = doc["plans"]["express_direct"]
    assert plan["carried"] == ["a", "b", "d"]
    assert plan["net_cents"] == 19


def test_equal_freight_tie_break(tmp_path):
    rows = [row("p", mass="2", port="P1", freight="5"),
            row("q", mass="2", port="P1", freight="5"),
            row("z", mass="2", port="P2", freight="5")]
    doc = decide(make_root(tmp_path, rows=rows, intake={"max_initial_mass_imbalance_tons": 0}))
    express = doc["plans"]["express_direct"]
    # {p,q}、{p,z}、{q,z} 净收益同为10分：按箱号与舱位稳定裁决为 {p,q} 且 p 在 L 底。
    assert express["carried"] == ["p", "q"]
    assert express["slots"] == {"L": {"bottom": "p", "top": None}, "R": {"bottom": "q", "top": None}}
    assert express["net_cents"] == 10
    again = decide(make_root(tmp_path / "again", rows=rows, intake={"max_initial_mass_imbalance_tons": 0}))
    assert again["plans"]["express_direct"] == express


@pytest.mark.parametrize("rows", [
    [row("a"), row("a")],
    [row("a", port="P9")],
    [row("a", mass="0")],
    [row("a", mass="x")],
    [row("a", mass="2.5")],
    [row("a", freight="-1")],
    [row("a", freight="x")],
    [row("a", source="")],
], ids=["duplicate-id", "bad-port", "zero-mass", "text-mass", "decimal-mass",
        "negative-freight", "text-freight", "missing-source"])
def test_invalid_manifest_refused(tmp_path, rows):
    with pytest.raises(ValueError):
        decide(make_root(tmp_path, rows=rows))


def test_verification_failure_refuses(tmp_path):
    with pytest.raises(ValueError):
        decide(make_root(tmp_path, rows=[row("a", ts="not-a-timestamp")]))


def test_manifest_data_change_drives_plan(tmp_path):
    rows = [row("a", mass="4", freight="8"), row("b", mass="4", port="P2", freight="7"),
            row("c", mass="3", freight="5"), row("d", mass="2", port="P2", freight="4")]
    doc = decide(make_root(tmp_path, rows=rows))
    # c 加重到3吨后 abc 初航差3超限，acd 须倒箱：express 最优降为 a/b 15分。
    assert doc["plans"]["express_direct"]["carried"] == ["a", "b"]
    assert doc["plans"]["express_direct"]["net_cents"] == 15
    assert doc["plans"]["economy_rehandle"]["net_cents"] == 23
    rows = [row("a", mass="4", freight="8"), row("b", mass="4", port="P2", freight="9"),
            row("c", mass="2", freight="5"), row("d", mass="2", port="P2", freight="4")]
    doc = decide(make_root(tmp_path / "second", rows=rows))
    assert doc["plans"]["express_direct"]["net_cents"] == 22
    assert doc["plans"]["economy_rehandle"]["net_cents"] == 25


def test_contract_terms_change_drives_plan(tmp_path):
    intake = json.loads(INTAKE.read_text(encoding="utf-8"))
    intake["contracts"]["economy_rehandle"]["max_rehandles_at_P1"] = 0
    doc = decide(make_root(tmp_path, intake=intake))
    plan = doc["plans"]["economy_rehandle"]
    assert plan["carried"] == ["a", "b", "c"] and plan["net_cents"] == 20
    assert doc["intake"]["sha256"] != sha256_bytes(INTAKE.read_bytes())


def test_replay_is_byte_identical(tmp_path):
    first = build_stowage_decision(CONTRACT, ROOT, INTAKE, "stow-replay")
    second = build_stowage_decision(CONTRACT, ROOT, INTAKE, "stow-replay")
    assert canonical_json_text(first) == canonical_json_text(second)
    out_a = write_stowage_outputs(first, tmp_path / "a", ROOT)
    out_b = write_stowage_outputs(second, tmp_path / "b", ROOT)
    assert out_a[0].read_bytes() == out_b[0].read_bytes()
    assert out_a[1].read_bytes() == out_b[1].read_bytes()


def test_row_reorder_keeps_decision(tmp_path, decision):
    doc = decide(make_root(tmp_path, rows=list(reversed(ROWS))))
    assert doc["manifest"]["sha256"] != decision["manifest"]["sha256"]
    for name, plan in decision["plans"].items():
        got = {k: v for k, v in doc["plans"][name].items() if k != "file_sha256"}
        want = {k: v for k, v in plan.items() if k != "file_sha256"}
        assert got == want


def test_ledger_wiring(tmp_path):
    doc = build_stowage_decision(CONTRACT, ROOT, INTAKE, "stow-ledger")
    _, ledger_path = write_stowage_outputs(doc, tmp_path / "out", ROOT)
    connection = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    run = connection.execute("SELECT run_id, containers, stacks, ports FROM stowage_run").fetchone()
    assert run == ("stow-ledger", 4, 2, 2)
    plans = dict(connection.execute("SELECT contract, net_cents FROM stowage_plan").fetchall())
    assert plans == {"express_direct": 20, "economy_rehandle": 23}
    carried = connection.execute(
        "SELECT carried_json FROM stowage_plan WHERE contract='express_direct'").fetchone()[0]
    assert json.loads(carried) == ["a", "b", "c"]
    connection.close()


def test_cli_stowage(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "tzgate", "stowage", "--contract", "contracts/vessel_stowage.yaml",
         "--root", ".", "--out", str(tmp_path / "out"), "--run-id", "stow-check"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "express_direct: carried=a,b,c" in result.stdout
    assert "economy_rehandle: carried=a,b,c,d" in result.stdout
    assert "net=20" in result.stdout and "net=23" in result.stdout
    assert (tmp_path / "out" / "stowage.json").is_file()
    assert (tmp_path / "out" / "stowage.sqlite3").is_file()


def test_cli_stowage_refuses_invalid_manifest(tmp_path):
    root = make_root(tmp_path, rows=[row("a", port="P9")])
    result = subprocess.run(
        [sys.executable, "-m", "tzgate", "stowage", "--contract", str(root / "contracts/vessel_stowage.yaml"),
         "--root", str(root), "--intake", str(root / "fixtures/vessel_stowage/intake.json"),
         "--out", str(tmp_path / "out")],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "refused" in result.stderr


def test_verify_command_on_stowage_contract(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", "-m", "tzgate", "verify", "--contract", "contracts/vessel_stowage.yaml",
         "--root", ".", "--out", str(tmp_path / "out"), "--run-id", "stow-check"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "status=pass" in result.stdout
