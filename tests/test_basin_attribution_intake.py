"""Invalid inputs, evidence invalidation, deterministic replay and the ledger."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

import pytest

from tzgate.basin import (
    AttributionError,
    attribute_basin,
    load_basin_intake,
    persist_attribution,
)
from tzgate.io_utils import canonical_json_text, sha256_bytes

from basin_helpers import (
    CONTRACT,
    INTAKE,
    ROOT,
    T,
    T2,
    attribute,
    rows,
    rules,
    workspace,
    write_csv,
)


def test_missing_duplicate_and_impossible_readings_are_rejected(tmp_path):
    with pytest.raises(AttributionError, match="missing sensor readings"):
        attribute(tmp_path / "m", rows({T: {"north": 7, "total": 10}}))
    dup = rows({T: {"north": 7, "south": 6, "total": 10}})
    dup.append(("dup", T, "basin", "north", "7"))
    with pytest.raises(AttributionError, match="duplicate reading"):
        attribute(tmp_path / "d", dup)
    impossible = rows({
        T: {"north": 7, "south": 6, "total": 10},
        T2: {"north": 8, "south": 6, "total": 99},
    })
    with pytest.raises(AttributionError, match="no feasible world"):
        attribute(tmp_path / "i", impossible)
    with pytest.raises(AttributionError, match="no feasible world"):
        attribute(tmp_path / "o",
                  rows({T: {"north": 21, "south": 6, "total": 23}}))


def test_illegal_matrix_sources_and_rules_are_rejected(tmp_path):
    bad_sensors = {"north": {"coefficients": [1, 1], "bias_coefficient": 1}}
    with pytest.raises(AttributionError, match="coefficients must align"):
        attribute(tmp_path, rows({T: {"north": 7}}), rules(sensors=bad_sensors))
    bad_rules = rules()
    bad_rules["contracts"]["individual_fine"]["decision"] = "guess_a_bias"
    with pytest.raises(AttributionError, match="unknown decision rule"):
        attribute(tmp_path / "b",
                  rows({T: {"north": 7, "south": 6, "total": 10}}), bad_rules)
    with pytest.raises(AttributionError, match="load_bounds"):
        attribute(tmp_path / "z",
                  rows({T: {"north": 7, "south": 6, "total": 10}}),
                  rules(bounds=(0, 11)))


def test_changed_source_file_changes_worlds_and_decisions(tmp_path):
    data = rows({
        T: {"north": 7, "south": 6, "total": 10},
        T2: {"north": 8, "south": 6, "total": 11},
    })
    root, contract, intake = workspace(tmp_path / "a", data, rules())
    first = attribute_basin(load_basin_intake(contract, root, intake, "r1"))
    assert first["joint_world_count"] == 3
    assert first["legal_decisions"]["collective_remediation"]["conclusions"][0][
        "outcome"] == "order"
    csv_path = next((root / "fixtures").rglob("part-0001.csv"))
    write_csv(csv_path, rows({
        T: {"north": 0, "south": 0, "total": 0},
        T2: {"north": 0, "south": 0, "total": 0},
    }))
    second = attribute_basin(load_basin_intake(contract, root, intake, "r2"))
    assert second["source_file"]["sha256"] != first["source_file"]["sha256"]
    assert second["feasible_biases"] == [0]
    assert second["joint_world_count"] == 1
    conclusion = second["legal_decisions"][
        "collective_remediation"]["conclusions"][0]
    assert conclusion["outcome"] == "no_order"
    assert conclusion["counterexample_world"] is not None


def test_contract_and_rulebook_changes_invalidate_old_attribution(tmp_path):
    data = rows({T: {"north": 7, "south": 6, "total": 10}})
    root, contract, intake = workspace(tmp_path / "c", data, rules(biases=(0,)))
    first = attribute_basin(load_basin_intake(contract, root, intake, "r1"))
    contract.write_text(contract.read_text("utf-8") + "# amended\n", "utf-8")
    second = attribute_basin(load_basin_intake(contract, root, intake, "r2"))
    assert second["contract"]["sha256"] != first["contract"]["sha256"]
    # Rulebook tamper defense: the contract pins the rulebook SHA and the
    # rulebook loader refuses a file whose bytes no longer match that pin.
    root2, contract2, intake2 = workspace(tmp_path / "rb", data, rules(biases=(0,)))
    from tzgate.contract import load_contract
    from tzgate.errors import RulebookError
    from tzgate.rulebook import load_rulebook
    contract_obj = load_contract(contract2, root2)
    rulebook = root2 / "contracts/timezone_rules.yaml"
    rulebook.write_bytes(rulebook.read_bytes() + b"\n# rulebook edit\n")
    with pytest.raises(RulebookError, match="rulebook changed after the contract"):
        load_rulebook(rulebook, contract_obj.rulebook_sha256)
    stale = rules(biases=(0,))
    stale["dataset"] = "other_lake"
    root3, contract3, intake3 = workspace(tmp_path / "s", data, stale)
    with pytest.raises(AttributionError, match="basin dataset disagrees"):
        load_basin_intake(contract3, root3, intake3, "r4")


def test_replay_is_deterministic_and_row_order_invariant(tmp_path):
    data = rows({
        T: {"north": 7, "south": 6, "total": 10},
        T2: {"north": 8, "south": 6, "total": 11},
    })
    root, contract, intake = workspace(tmp_path / "d", data, rules())
    first = attribute_basin(load_basin_intake(contract, root, intake, "det"))
    payload = canonical_json_text(first)
    assert canonical_json_text(
        attribute_basin(load_basin_intake(contract, root, intake, "det"))
    ) == payload
    csv_path = next((root / "fixtures").rglob("part-0001.csv"))
    write_csv(csv_path, [data[0]] + list(reversed(data[1:])))
    reordered = attribute_basin(load_basin_intake(contract, root, intake, "det"))
    a, b = json.loads(payload), json.loads(canonical_json_text(reordered))
    assert a["source_file"].pop("sha256") != b["source_file"].pop("sha256")
    assert a == b
    out1 = persist_attribution(first, tmp_path / "o1")
    out2 = persist_attribution(first, tmp_path / "o2")
    for name in ("basin_attribution.json", "attribution_ledger.sqlite3"):
        assert (tmp_path / "o1" / name).read_bytes() == (
            tmp_path / "o2" / name).read_bytes()


def test_attribution_ledger_records_run_and_decisions(tmp_path):
    evidence = attribute(tmp_path / "l", rows({
        T: {"north": 7, "south": 6, "total": 10},
        T2: {"north": 8, "south": 6, "total": 11},
    }))
    outputs = persist_attribution(evidence, tmp_path / "out")
    with sqlite3.connect(outputs["ledger"]) as conn:
        run = conn.execute(
            "SELECT joint_world_count, worlds_materialized, evidence_sha256, "
            "feasible_biases_json FROM attribution_runs").fetchone()
        decisions = dict(conn.execute(
            "SELECT contract_name, outcome FROM attribution_decisions"))
    assert run[:2] == (3, 1)
    assert run[2] == sha256_bytes(outputs["json"].read_bytes())
    assert json.loads(run[3]) == [-1, 0, 1]
    assert decisions == {"collective_remediation": "order",
                         "individual_fine": "no_fines"}


def test_cli_attribute_end_to_end(tmp_path):
    out = tmp_path / "cli"
    result = subprocess.run(
        [sys.executable, "-B", "-m", "tzgate", "attribute", "--contract",
         str(CONTRACT), "--root", str(ROOT), "--intake", str(INTAKE),
         "--out", str(out), "--run-id", "basin-check"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "joint_worlds=3" in result.stdout
    assert "collective_remediation: order" in result.stdout
    assert "individual_fine: no_fine" in result.stdout
    evidence = json.loads((out / "basin_attribution.json").read_text("utf-8"))
    assert evidence["run_id"] == "basin-check"
    assert (out / "attribution_ledger.sqlite3").is_file()


def test_verify_ledger_keeps_attribution_tables(tmp_path):
    from tzgate.engine import run_verification
    from tzgate.pipeline import write_outputs
    outputs = write_outputs(
        run_verification(CONTRACT, ROOT, "v"), tmp_path / "v")
    with sqlite3.connect(outputs.ledger) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"attribution_runs", "attribution_decisions"} <= tables
