"""Pre-registered sampling and hypergeometric population certificate tests."""
from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

from tzgate.metadata_certificate import draw_frame, load_frame, preview_metadata, seal_commitment
from tzgate.pipeline import execute_verification
from tzgate.population_certificate import evaluate_commitment
from tzgate.sampling import MAX_FRAME_SIZE, MAX_SAMPLE_SIZE, hypergeom_tail, parse_fraction, population_lower_bound

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/metadata_certificate.yaml"
FRAME = ROOT / "fixtures/metadata_cert/frame.json"
HEADER = ["event_time", "source_id", "document_id", "title", "owner", "updated", "license"]
ROWS = {
    "a": ["2024-07-01T12:00:00Z", "archive-1", "a", "Document A", "PL", "2024-07-01", "CC0"],
    "b": ["2024-07-01T12:00:00Z", "archive-1", "b", "Document B", "PL", "2024-07-01", "CC0"],
    "c": ["2024-07-01T12:00:00Z", "archive-1", "c", "Document C", "PL", "2024-07-01", "CC0"],
    "d": ["2024-07-01T12:00:00Z", "archive-1", "d", "Document D", "PL", "2024-07-01", "CC0"],
    "e": ["2024-07-01T12:00:00Z", "archive-1", "e", "Document E", "PL", "2024-07-01", ""],
    "f": ["2024-07-01T12:00:00Z", "archive-1", "f", "Document F", "PL", "2024-07-01", "CC0"],
}

def _write_lake(base: Path, order=None, extra=(), patched=None):
    part = base / "lake" / "dt=2024-07-01"
    part.mkdir(parents=True)
    shutil.copy(ROOT / "contracts" / "timezone_rules.yaml", base)
    text = (ROOT / "contracts" / "metadata_certificate.yaml").read_text(encoding="utf-8")
    (base / "contract.yaml").write_text(text.replace("fixtures/metadata_cert/lake", "lake"), "utf-8")
    lines = [HEADER] + [ROWS[d] for d in (order or ROWS)] + list(extra)
    target = part / "part-0001.csv"
    with target.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(lines)
    if patched:
        target.write_bytes(target.read_bytes().replace(*patched))
    return base / "contract.yaml"

def _frame(base: Path, **overrides) -> Path:
    data = json.loads(FRAME.read_text(encoding="utf-8"))
    data.update(overrides)
    path = base / "frame.json"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return path

def _by_name(evaluation):
    return {item["contract_name"]: item for item in evaluation["certificates"]}

def _evaluate(base: Path, contract: Path, run_id: str):
    frame_path = _frame(base)
    return evaluate_commitment(contract, base, load_frame(frame_path), frame_path, run_id)

def test_draw_is_fixed_without_replacement_order_independent():
    frame = load_frame(FRAME)
    assert draw_frame(frame) == ["b", "d", "f", "e"]
    shuffled = dict(frame, document_ids=list(reversed(frame["document_ids"])))
    assert draw_frame(shuffled) == draw_frame(frame)

def test_commitment_sealed_before_content_covers_full_plan_and_seed_binding():
    frame = load_frame(FRAME)
    commitment = seal_commitment(frame, FRAME, "metadata-check")
    assert commitment["document_ids"] == list("abcdef")
    assert commitment["selected_ids"] == ["b", "d", "f", "e"]
    assert (commitment["frame_size"], commitment["sample_size"]) == (6, 4)
    assert {c["name"] for c in commitment["contracts"]} == {"full_metadata", "public_index"}
    assert len(commitment["commitment_sha256"]) == 64
    payload = json.loads(commitment["commitment_payload"])
    assert payload["run_id"] == "metadata-check" and payload["seed"] == frame["seed"]
    assert seal_commitment(frame, FRAME, "metadata-check")["commitment_sha256"] == commitment["commitment_sha256"]
    rolled = dict(frame, seed="tampered-seed")
    assert seal_commitment(rolled, FRAME, "metadata-check")["commitment_sha256"] != commitment["commitment_sha256"]

def test_dual_contracts_reach_different_legal_conclusions():
    evaluation = evaluate_commitment(CONTRACT, ROOT, load_frame(FRAME), FRAME, "metadata-check")
    full, public = _by_name(evaluation)["full_metadata"], _by_name(evaluation)["public_index"]
    assert (full["frame_size"], full["sample_size"], full["observed_successes"]) == (6, 4, 3)
    assert full["lower_bound_fraction"] == "3/6" and full["decision"] == "insufficient_evidence"
    assert (public["frame_size"], public["sample_size"], public["observed_successes"]) == (6, 4, 4)
    assert public["lower_bound_fraction"] == "5/6" and public["decision"] == "issued"
    e_row = next(r for r in full["evidence"] if r["document_id"] == "e")
    assert not e_row["conforms"] and e_row["fields"] == { "title": True, "owner": True, "updated": True, "license": False}
    assert not e_row["defects"] and evaluation["dataset_defects"] == []
    assert evaluation["contract_sha256"] and evaluation["rulebook_sha256"]
    assert evaluation["files"][0]["sha256"]

def test_tail_table_uses_exact_integer_fractions():
    bound = population_lower_bound(6, 4, 4)
    table = {r.population_successes: r.tail_text for r in bound.tail_table}
    assert table == {0: "0/1", 1: "0/1", 2: "0/1", 3: "0/1", 4: "1/15", 5: "1/3", 6: "1/1"}
    assert hypergeom_tail(6, 3, 4, 3) == Fraction(1, 5)
    assert bound.lower_bound == 5 and population_lower_bound(6, 4, 3).lower_bound == 3

def test_preview_keeps_counts_and_now_carries_certificates():
    preview = preview_metadata(CONTRACT, ROOT, FRAME, "metadata-run")
    assert (preview["frame_size"], preview["sample_size"]) == (6, 4)
    assert (preview["full_metadata_count"], preview["public_index_count"]) == (3, 4)
    assert {c["contract_name"] for c in preview["population_certificate"]} == { "full_metadata", "public_index"}
    assert preview["commitment"]["selected_ids"] == ["b", "d", "f", "e"]

def test_post_hoc_seed_change_changes_draw_and_pipeline_blocks_reuse(tmp_path):
    base = tmp_path / "corpus"
    contract = _write_lake(base)
    original = _frame(base)
    first = evaluate_commitment(contract, base, load_frame(original), original, "run-seed")
    rolled = _frame(base, seed="tampered-seed")
    second = evaluate_commitment(contract, base, load_frame(rolled), rolled, "run-seed")
    assert second["selected_ids"] != first["selected_ids"]
    assert second["commitment"]["commitment_sha256"] != first["commitment"]["commitment_sha256"]
    out = tmp_path / "out"
    execute_verification(contract, base, out, "run-seed", None, original)
    with pytest.raises(Exception, match="sealed"):
        execute_verification(contract, base, out, "run-seed", None, rolled)

def test_post_hoc_contract_swap_changes_sealed_commitment(tmp_path):
    base = tmp_path / "corpus"
    contract = _write_lake(base)
    frame_path = _frame(base)
    first = evaluate_commitment(contract, base, load_frame(frame_path), frame_path, "run-swap")
    swapped = _frame(base, contracts={"public_index": {
        "required_fields": ["title", "owner", "updated"], "min_population_fraction": "3/4"}})
    second = evaluate_commitment(contract, base, load_frame(swapped), swapped, "run-swap")
    assert [c["name"] for c in second["commitment"]["contracts"]] == ["public_index"]
    assert "full_metadata" in {c["name"] for c in first["commitment"]["contracts"]}
    assert second["commitment"]["commitment_sha256"] != first["commitment"]["commitment_sha256"]

def test_missing_selected_row_is_failure_not_replacement(tmp_path):
    base = tmp_path / "corpus"
    contract = _write_lake(base, order=[d for d in ROWS if d != "b"])
    evaluation = _evaluate(base, contract, "run-missing")
    full = _by_name(evaluation)["full_metadata"]
    assert (full["sample_size"], full["observed_successes"]) == (4, 2)
    b_row = next(r for r in full["evidence"] if r["document_id"] == "b")
    assert not b_row["conforms"]
    assert any(d["document_id"] == "b" and "missing" in d["reason"] for d in evaluation["dataset_defects"])

def test_duplicate_id_and_empty_field_count_as_nonconforming(tmp_path):
    base = tmp_path / "corpus"
    duplicate = ROWS["b"].copy()
    duplicate[2] = "d"
    contract = _write_lake(base, extra=[duplicate])
    evaluation = _evaluate(base, contract, "run-dup")
    full = _by_name(evaluation)["full_metadata"]
    d_row = next(r for r in full["evidence"] if r["document_id"] == "d")
    assert not d_row["conforms"] and "duplicate document_id" in d_row["defects"]
    assert any(d["document_id"] == "d" for d in evaluation["dataset_defects"])
    base2 = tmp_path / "corpus2"
    contract2 = _write_lake(base2, patched=(b",f,Document F,PL,", b",f, ,PL,"))
    public = _by_name(_evaluate(base2, contract2, "run-empty"))["public_index"]
    f_row = next(r for r in public["evidence"] if r["document_id"] == "f")
    assert not f_row["conforms"] and f_row["fields"]["title"] is False


def test_malformed_row_is_nonconforming_without_replacement(tmp_path):
    base = tmp_path / "corpus"
    # Selected document b gets a row with the wrong column count: it fails,
    # nothing else is drawn to take its place.
    contract = _write_lake(base, extra=[["2024-07-01T12:00:00Z", "archive-1", "b", "Document B"]])
    evaluation = _evaluate(base, contract, "run-bad-row")
    full = _by_name(evaluation)["full_metadata"]
    assert full["sample_size"] == 4 and full["observed_successes"] == 2
    b_row = next(r for r in full["evidence"] if r["document_id"] == "b")
    assert not b_row["conforms"] and any("column count" in d["reason"] for d in evaluation["dataset_defects"])

def test_bad_file_and_wrong_partition_reject_stale_commitment(tmp_path):
    base = tmp_path / "corpus"
    _write_lake(base, extra=[["broken", "row"]])
    with pytest.raises(ValueError, match="verification issues"):
        _evaluate(base, base / "contract.yaml", "run-bad")
    base2 = tmp_path / "corpus2"
    _write_lake(base2)
    wrong = base2 / "lake" / "dt=2024-07-02"
    wrong.mkdir(parents=True)
    shutil.copy(base2 / "lake/dt=2024-07-01/part-0001.csv", wrong / "part-0001.csv")
    with pytest.raises(ValueError, match="verification issues"):
        _evaluate(base2, base2 / "contract.yaml", "run-part")

def test_changed_file_sha_changes_qualification_and_decision(tmp_path):
    base = tmp_path / "corpus"
    contract = _write_lake(base)
    frame_path = _frame(base)
    first = evaluate_commitment(contract, base, load_frame(frame_path), frame_path, "run-sha")
    target = base / "lake/dt=2024-07-01/part-0001.csv"
    target.write_bytes(target.read_bytes().replace( b",e,Document E,PL,2024-07-01,\n", b",e,Document E,PL,2024-07-01,MIT\n"))
    second = evaluate_commitment(contract, base, load_frame(frame_path), frame_path, "run-sha2")
    assert first["files"] != second["files"]
    full = _by_name(second)["full_metadata"]
    assert full["observed_successes"] == 4 and full["lower_bound"] == 5 and full["decision"] == "issued"

def test_threshold_boundary_and_fraction_parsing():
    threshold = parse_fraction("3/4")
    assert threshold == Fraction(3, 4)
    assert Fraction(3, 6) < threshold <= Fraction(5, 6)
    assert population_lower_bound(6, 4, 4).lower_bound / 6 >= threshold
    assert population_lower_bound(6, 4, 3).lower_bound / 6 < threshold
    with pytest.raises(ValueError):
        parse_fraction("4/3")

def test_plan_limits_and_bad_frames_enforced(tmp_path):
    with pytest.raises(ValueError):
        population_lower_bound(MAX_FRAME_SIZE + 1, 4, 1)
    with pytest.raises(ValueError):
        population_lower_bound(6, MAX_SAMPLE_SIZE + 1, 1)
    with pytest.raises(ValueError):
        population_lower_bound(6, 4, 5)
    data = json.loads(FRAME.read_text(encoding="utf-8"))
    data["document_ids"][0] = "b"
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps(data), "utf-8")
    with pytest.raises(ValueError, match="frame IDs invalid"):
        load_frame(dup)
    for key, value in (("seed", ""), ("sample_size", 0), ("sample_size", 13), ("document_ids", []), ("contracts", {})):
        broken = json.loads(FRAME.read_text(encoding="utf-8"))
        broken[key] = value
        bad = tmp_path / f"bad-{key}.json"
        bad.write_text(json.dumps(broken), "utf-8")
        with pytest.raises(ValueError):
            load_frame(bad)

def test_row_reorder_does_not_change_certificate(tmp_path):
    c1 = _write_lake(tmp_path / "one", order=list(ROWS))
    c2 = _write_lake(tmp_path / "two", order=list(reversed(ROWS)))
    r1 = _evaluate(tmp_path / "one", c1, "run-order")
    r2 = _evaluate(tmp_path / "two", c2, "run-order")
    assert r1["certificates"] == r2["certificates"]

def test_pipeline_seals_immutable_outputs_and_rejects_replay(tmp_path):
    out = tmp_path / "out"
    _, outputs = execute_verification(CONTRACT, ROOT, out, "seal-run", None, FRAME)
    assert outputs.commitment_json.is_file() and outputs.certificate_json.is_file()
    certificates = json.loads(outputs.certificate_json.read_text(encoding="utf-8"))
    assert {c["decision"] for c in certificates} == {"issued", "insufficient_evidence"}
    connection = sqlite3.connect(outputs.ledger)
    assert connection.execute( "SELECT count(*) FROM sample_commitment WHERE run_id='seal-run'").fetchone()[0] == 1
    assert connection.execute( "SELECT count(*) FROM population_certificate WHERE run_id='seal-run'").fetchone()[0] == 2
    connection.close()
    with pytest.raises(Exception, match="sealed"):
        execute_verification(CONTRACT, ROOT, out, "seal-run", None, FRAME)
    fresh = tmp_path / "out2"
    execute_verification(CONTRACT, ROOT, fresh, "seal-run", None, FRAME)
    with pytest.raises(Exception, match="sealed"):
        execute_verification(CONTRACT, ROOT, fresh, "seal-run", None, FRAME)

def test_cli_verify_emits_certificate_artifacts(tmp_path):
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-B", "-m", "tzgate", "verify", "--contract", str(CONTRACT),
         "--root", str(ROOT), "--out", str(out), "--run-id", "metadata-check",
         "--frame", str(FRAME)],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "lower_bound=3/6" in result.stdout and "insufficient_evidence" in result.stdout
    assert "lower_bound=5/6" in result.stdout and "decision=issued" in result.stdout
    assert (out / "sample_commitment.json").is_file()
    assert (out / "population_certificate.json").is_file()
    ledger = sqlite3.connect(out / "ledger.sqlite3")
    assert ledger.execute("SELECT count(*) FROM population_certificate").fetchone()[0] == 2

