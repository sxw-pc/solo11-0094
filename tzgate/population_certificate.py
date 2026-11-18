"""Evaluate pre-registered sample commitments against verified lake data.

The same file set verified by ``tzgate.engine`` supplies the actual sample.
Every defect (missing row, malformed row, duplicate id, missing or empty
field) makes the corresponding document non-conforming: it is never removed
from the denominator. A changed file SHA or any engine verification issue
invalidates the sealed commitment outright instead of being footnoted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .discovery import discover_dataset
from .engine import run_verification
from .io_utils import sha256_bytes
from .metadata_certificate import SchemaContract, read_documents, schema_contracts, seal_commitment
from .sampling import parse_fraction, population_lower_bound

def evaluate_commitment(contract_path: Path, root: Path, frame: dict[str, Any], frame_path: Path, run_id: str) -> dict[str, Any]:
    commitment = seal_commitment(frame, frame_path, run_id)
    result = run_verification(contract_path, root, run_id)
    datasets = [item for item in result.datasets if item.spec.name == frame["dataset"]]
    if len(datasets) != 1:
        raise ValueError("frame dataset must match exactly one contract dataset")
    dataset = datasets[0]
    if result.status != "pass" or dataset.issue_count:
        raise ValueError("verified file set carries verification issues; stale commitment rejected")
    spec = dataset.spec
    files = discover_dataset(spec).files
    fingerprints = [{"file": ref.label, "sha256": sha256_bytes(ref.path.read_bytes())} for ref in sorted(files, key=lambda ref: ref.label)]
    records, bad_rows = read_documents(files, spec.encoding, spec.delimiter)
    observed_ids = [row.get("document_id", "").strip() for row in records]
    duplicates = sorted({doc for doc in observed_ids if observed_ids.count(doc) > 1})
    index = {row.get("document_id", "").strip(): row for row in reversed(records) if row.get("document_id", "").strip()}
    contracts = schema_contracts(frame)
    defects = _frame_defects(frame, observed_ids, bad_rows, duplicates)
    rows = _sample_rows(commitment["selected_ids"], index, bad_rows, duplicates, contracts)
    certificates = [_certificate(commitment, item, rows, frame["document_ids"]) for item in contracts]
    return {
        "run_id": run_id, "commitment": commitment,
        "contract_sha256": result.contract.sha256,
        "rulebook_sha256": result.contract.rulebook_sha256,
        "files": fingerprints, "frame_size": len(frame["document_ids"]),
        "sample_size": len(commitment["selected_ids"]),
        "selected_ids": commitment["selected_ids"],
        "dataset_defects": defects, "sample_rows": rows, "certificates": certificates,
    }

def _defect(doc: str, reason: str, file: str = "", line: str = "") -> dict[str, str]:
    return {"file": file, "line": line, "document_id": doc, "reason": reason}

def _frame_defects(frame: dict[str, Any], observed_ids: list[str],
                   bad_rows: list[dict[str, str]], duplicates: list[str]) -> list[dict[str, str]]:
    defects = [dict(item) for item in bad_rows]
    seen = set(observed_ids)
    defects.extend(_defect(doc, "frame document missing from verified files") for doc in frame["document_ids"] if doc not in seen)
    defects.extend(_defect(doc, "verified document not declared in frame") for doc in sorted(seen - set(frame["document_ids"])))
    defects.extend(_defect(doc, "duplicate document_id in verified files") for doc in duplicates)
    return sorted(defects, key=lambda item: (item["document_id"], item["reason"]))

def _sample_rows(selected: list[str], index: dict[str, dict[str, str]],
                 bad_rows: list[dict[str, str]], duplicates: list[str],
                 contracts: tuple[SchemaContract, ...]) -> list[dict[str, Any]]:
    rows = []
    for doc in selected:
        bad = [item for item in bad_rows if item["document_id"] == doc]
        if doc in duplicates:
            reasons = ["duplicate document_id"]
        elif bad:
            reasons = sorted({item["reason"] for item in bad})
        elif doc not in index:
            reasons = ["frame document missing from verified files"]
        else:
            reasons = []
        row = index.get(doc, {})
        field_checks = {contract.name: {field: bool(row.get(field, "").strip())
                                        for field in contract.required_fields}
                        for contract in contracts}
        conforms = {contract.name: not reasons and all(field_checks[contract.name].values()) for contract in contracts}
        rows.append({"document_id": doc, "defects": reasons, "fields": field_checks, "conforms": conforms})
    return rows

def _certificate(commitment: dict[str, Any], contract: SchemaContract, rows: list[dict[str, Any]], frame_ids: list[str]) -> dict[str, Any]:
    x = sum(1 for row in rows if row["conforms"][contract.name])
    bound = population_lower_bound(len(frame_ids), len(rows), x)
    threshold = parse_fraction(contract.min_population_fraction)
    publish = bound.lower_bound / bound.frame_size >= threshold
    decision = "issued" if publish else "insufficient_evidence"
    relation = "meets" if publish else "is below"
    return {
        "run_id": commitment["run_id"], "contract_name": contract.name,
        "commitment_sha256": commitment["commitment_sha256"],
        "required_fields": list(contract.required_fields),
        "threshold_text": contract.min_population_fraction,
        "frame_size": bound.frame_size, "sample_size": len(rows),
        "observed_successes": x, "lower_bound": bound.lower_bound,
        "lower_bound_fraction": bound.lower_fraction_text,
        "decision": decision,
        "reason": f"90% one-sided lower bound {bound.lower_fraction_text} {relation} "
                  f"contract minimum {contract.min_population_fraction}",
        "tail_table": [row.to_dict() for row in bound.tail_table],
        "evidence": [
            {"document_id": row["document_id"], "conforms": row["conforms"][contract.name],
             "defects": list(row["defects"]), "fields": row["fields"][contract.name]}
            for row in rows
        ],
    }

