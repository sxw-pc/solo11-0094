from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tzgate.io_utils import sha256_file
from tzgate.replica_availability import (
    load_replica_intake,
    plan_service_commitment,
    preview_scenario_hardening,
    validate_replica_data,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "replica_availability.yaml"
INTAKE = ROOT / "fixtures" / "replica_availability" / "intake.json"


@pytest.fixture(scope="module")
def commitment() -> tuple[dict, dict]:
    data = load_replica_intake(CONTRACT, ROOT, INTAKE, "replica-run")
    return data, plan_service_commitment(data)


def test_verified_files_drive_scenario_preview_trap(commitment):
    data, _ = commitment
    assert [r["region_id"] for r in data["regions"]] == ["ca", "eu", "us"]
    assert all(r["sha256"] and r["size_bytes"] > 0 for r in data["regions"])
    preview = preview_scenario_hardening(data)
    assert preview == {"west": {"chosen_node": "A", "harden_cost": 2},
                       "east": {"chosen_node": "C", "harden_cost": 2}}
    # The two cheap picks disagree, so they cannot be one pre-fault commitment.
    assert preview["west"]["chosen_node"] != preview["east"]["chosen_node"]


def test_fixture_generator_includes_verified_replica_partitions():
    from tools.fixture_data import FIXTURES
    assert "replica_orders" in FIXTURES
    assert len(FIXTURES["replica_orders"]["files"]) == 3


def test_continuous_partition_signs_only_with_node_b(commitment):
    plan = commitment[1]["plans"]["continuous_partition"]
    assert (plan["hardened_node"], plan["harden_cost"], plan["total_cost"]) == ("B", 6, 6)
    assert plan["worst_scenario_cost"] == 0
    assert all(not s["initial_outages"] and not s["compensation"]
               and s["repaired_node"] is None and all(s["initial_readable"].values())
               and all(s["recovered_readable"].values()) for s in plan["scenarios"].values())
    # A and C (the per-scenario preview picks) cannot fake continuous cover.
    rejected = {c["hardened_node"]: c["reason"]
                for c in plan["certificate"]["candidates"] if not c["feasible"]}
    assert set(rejected) == {None, "A", "C"}
    assert "east" in rejected["A"] and "eu" in rejected["A"]
    assert "west" in rejected["C"] and "ca" in rejected["C"]


def test_eu_repair_window_signs_a_with_worst_cost_five(commitment):
    plan = commitment[1]["plans"]["eu_repair_window"]
    assert (plan["hardened_node"], plan["harden_cost"], plan["total_cost"]) == ("A", 2, 5)
    assert plan["worst_scenario_cost"] == 3
    west, east = plan["scenarios"]["west"], plan["scenarios"]["east"]
    assert west["initial_outages"] == [] and west["repaired_node"] is None
    # The initial eu outage is recorded and paid for; the post-reveal repair
    # restores eu but never rewrites the outage as if it had not happened.
    assert east["initial_readable"]["eu"] is False
    assert east["initial_outages"] == ["eu"] and east["compensation"] == 2
    assert (east["repaired_node"], east["repair_cost"]) == ("B", 1)
    assert all(east["recovered_readable"].values())


def test_commitment_binds_verified_identities(commitment):
    data, plan = commitment
    assert plan["contract_sha256"] == sha256_file(CONTRACT)
    assert plan["rulebook_sha256"] == sha256_file(ROOT / "contracts" / "timezone_rules.yaml")
    assert plan["run_id"] == "replica-run" and plan["dataset"] == "replica_orders"
    files = {entry["file"]: entry for entry in plan["files"]}
    assert set(files) == {f"dt=2024-07-0{day}/part-0001.csv" for day in (1, 2, 3)}
    for entry in files.values():
        assert entry["size_bytes"] > 0
        assert entry["sha256"] == sha256_file(ROOT / "fixtures/lake/replica_orders" / entry["file"])
    verified = {r["file"]: r["sha256"] for r in data["regions"]}
    assert {f["file"]: f["sha256"] for f in plan["files"]} == verified


def test_certificate_proves_optimality(commitment):
    for plan in commitment[1]["plans"].values():
        candidates = plan["certificate"]["candidates"]
        assert plan["certificate"]["optimal"] is True
        assert [c["hardened_node"] for c in candidates] == [None, "A", "B", "C"]
        feasible = [c for c in candidates if c["feasible"]]
        assert plan["total_cost"] == min(c["total_cost"] for c in feasible)
    eu = {c["hardened_node"]: c for c in commitment[1]["plans"]["eu_repair_window"]["certificate"]["candidates"]}
    assert eu["A"]["total_cost"] == 5 and eu["B"]["total_cost"] == 6
    assert eu[None]["feasible"] is False and eu["C"]["feasible"] is False


def test_plan_is_invariant_to_input_reordering(commitment):
    data, plan = commitment
    shuffled = {**data, "nodes": data["nodes"][::-1],
                "regions": [{**r, "replicas": r["replicas"][::-1]} for r in data["regions"][::-1]],
                "scenarios": [{**s, "failed_nodes": s["failed_nodes"][::-1]} for s in data["scenarios"][::-1]],
                "contracts": dict(reversed(list(data["contracts"].items())))}
    assert plan_service_commitment(shuffled) == plan


def _valid_data() -> dict:
    return {"dataset": "replica_orders",
            "regions": [{"region_id": "g", "file": "dt=1/part-0001.csv", "replicas": ["A", "B"]}],
            "nodes": [{"node_id": "A", "harden_cost": 9, "repair_cost": 1},
                      {"node_id": "B", "harden_cost": 9, "repair_cost": 1}],
            "scenarios": [{"scenario_id": "s1", "failed_nodes": ["A", "B"]}],
            "contracts": {"continuous": {"temporary_outage_region": None, "compensation": 0},
                          "window": {"temporary_outage_region": "g", "compensation": 3}}}


def test_tie_breaks_pick_smallest_node_ids():
    plans = plan_service_commitment(_valid_data())["plans"]
    assert plans["continuous"]["hardened_node"] == "A"
    assert plans["continuous"]["total_cost"] == 9
    assert plans["window"]["hardened_node"] is None
    assert plans["window"]["scenarios"]["s1"]["repaired_node"] == "A"
    assert plans["window"]["total_cost"] == 4


def test_unrecoverable_outage_is_refused_not_signed():
    data = _valid_data()
    data["regions"] += [{"region_id": "h", "file": "f2", "replicas": ["C", "D"]},
                        {"region_id": "k", "file": "f3", "replicas": ["E", "F"]}]
    data["nodes"] += [{"node_id": n, "harden_cost": 1, "repair_cost": 1} for n in "CDEF"]
    data["scenarios"] = [{"scenario_id": "s1", "failed_nodes": list("ABCDEF")}]
    data["contracts"] = {"window": {"temporary_outage_region": "g", "compensation": 1}}
    with pytest.raises(ValueError, match="cannot be signed"):
        plan_service_commitment(data)


@pytest.mark.parametrize("mutate, pattern", [
    (lambda d: d["regions"][0]["replicas"].append("Z"), "unknown node"),
    (lambda d: d["scenarios"][0]["failed_nodes"].append("Z"), "unknown node"),
    (lambda d: d["contracts"]["window"].update(temporary_outage_region="nowhere"), "unknown temporary"),
    (lambda d: d["nodes"].append({"node_id": "A", "harden_cost": 1, "repair_cost": 1}), "duplicate node_id"),
    (lambda d: d["regions"].append({"region_id": "g", "file": "f9", "replicas": ["A"]}), "duplicate region_id"),
    (lambda d: d["scenarios"].append({"scenario_id": "s1", "failed_nodes": []}), "duplicate scenario_id"),
    (lambda d: d["nodes"][0].update(harden_cost=-1), "invalid harden_cost"),
    (lambda d: d["nodes"][0].update(repair_cost=-2), "invalid repair_cost"),
    (lambda d: d["contracts"]["window"].update(compensation=-3), "invalid compensation"),
    (lambda d: d.update(nodes=[{"node_id": f"N{i}", "harden_cost": 1, "repair_cost": 1} for i in range(9)]), "1..8"),
    (lambda d: d.update(regions=d["regions"] + [{"region_id": f"r{i}", "file": "f", "replicas": ["A"]} for i in range(5)]), "1..5"),
    (lambda d: d.update(scenarios=d["scenarios"] + [{"scenario_id": f"s{i}", "failed_nodes": []} for i in range(5)]), "1..4"),
])
def test_validate_replica_data_rejects_bad_input(mutate, pattern):
    data = _valid_data()
    mutate(data)
    with pytest.raises(ValueError, match=pattern):
        validate_replica_data(data)


def _stage_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "root"
    shutil.copytree(ROOT / "fixtures", root / "fixtures")
    shutil.copytree(ROOT / "contracts", root / "contracts")
    shutil.copy(INTAKE, root / "intake.json")
    return root, root / "contracts" / "replica_availability.yaml", root / "intake.json"


def test_missing_or_added_partition_file_changes_signing(tmp_path):
    root, contract, intake = _stage_tree(tmp_path / "missing")
    (root / "fixtures/lake/replica_orders/dt=2024-07-02/part-0001.csv").unlink()
    with pytest.raises(ValueError, match="region file missing"):
        load_replica_intake(contract, root, intake, "replica-run")
    root, contract, intake = _stage_tree(tmp_path / "added")
    extra = root / "fixtures/lake/replica_orders/dt=2024-07-04"
    extra.mkdir()
    (extra / "part-0001.csv").write_text(
        "order_id,event_time,source_id\no-x,2024-07-04T12:00:00Z,us\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not all verified files mapped"):
        load_replica_intake(contract, root, intake, "replica-run")


def test_wrong_partition_or_remapped_file_is_refused(tmp_path):
    root, contract, intake = _stage_tree(tmp_path / "content")
    target = root / "fixtures/lake/replica_orders/dt=2024-07-01/part-0001.csv"
    target.write_text("order_id,event_time,source_id\no-ca,2024-07-02T12:00:00Z,ca\n",
                      encoding="utf-8")
    with pytest.raises(ValueError, match="must verify cleanly"):
        load_replica_intake(contract, root, intake, "replica-run")
    root, contract, intake = _stage_tree(tmp_path / "remapped")
    data = json.loads(INTAKE.read_text(encoding="utf-8"))
    data["regions"][0]["file"] = "dt=2024-07-02/part-0001.csv"
    intake.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="not all verified files mapped"):
        load_replica_intake(contract, root, intake, "replica-run")
    data["regions"][0]["file"] = "dt=2024-07-01/part-0001.csv"
    data["dataset"] = "other_orders"
    intake.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="must verify cleanly"):
        load_replica_intake(contract, root, intake, "replica-run")
