"""Pre-registered sampling commitments for document metadata certificates.

The frame lists every document in the verified lake table *before* any
sampled row content is inspected. Sealing a commitment freezes the full
document id list, the seed, the without-replacement draw, the schema
contracts and the run/tool identifiers. After sealing, the seed cannot be
re-rolled, contracts cannot be swapped and the sample cannot be topped up.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io_utils import canonical_json_line, sha256_bytes
from .sampling import MAX_FRAME_SIZE, check_plan_limits
from .version import __version__

@dataclass(frozen=True)
class SchemaContract:
    name: str
    required_fields: tuple[str, ...]
    min_population_fraction: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "required_fields": list(self.required_fields), "min_population_fraction": self.min_population_fraction}

def load_frame(path: Path) -> dict[str, Any]:
    try:
        frame = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read frame {path.name}: {exc}") from exc
    if not isinstance(frame, dict):
        raise ValueError("frame must be a JSON object")
    ids = frame.get("document_ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_FRAME_SIZE:
        raise ValueError("document frame IDs invalid")
    if any(not isinstance(doc, str) or not doc.strip() for doc in ids):
        raise ValueError("document frame IDs invalid: ids must be non-empty strings")
    if len(ids) != len(set(ids)):
        raise ValueError("document frame IDs invalid")
    sample_size = frame.get("sample_size")
    if type(sample_size) is not int:
        raise ValueError("sample plan invalid: sample_size must be an integer")
    check_plan_limits(len(ids), sample_size)
    if not isinstance(frame.get("seed"), str) or not frame["seed"].strip():
        raise ValueError("sample plan invalid: seed must be a non-empty string")
    if not isinstance(frame.get("dataset"), str) or not frame["dataset"].strip():
        raise ValueError("sample plan invalid: dataset must be a non-empty string")
    contracts = frame.get("contracts")
    if not isinstance(contracts, dict) or not contracts:
        raise ValueError("sample plan invalid: contracts must be a non-empty mapping")
    for name, raw in contracts.items():
        _parse_schema_contract(name, raw)
    return frame

def _parse_schema_contract(name: str, raw: Any) -> SchemaContract:
    if not isinstance(name, str) or not name.strip() or not isinstance(raw, dict):
        raise ValueError(f"sample plan invalid: contract {name!r} must be a named mapping")
    fields = raw.get("required_fields")
    if not isinstance(fields, list) or not fields or len(fields) != len(set(fields)):
        raise ValueError(f"sample plan invalid: contract {name} needs unique required_fields")
    if any(not isinstance(field, str) or not field.strip() for field in fields):
        raise ValueError(f"sample plan invalid: contract {name} fields must be strings")
    threshold = raw.get("min_population_fraction")
    if not isinstance(threshold, str) or not threshold.strip():
        raise ValueError(f"sample plan invalid: contract {name} needs a fraction threshold")
    return SchemaContract(name, tuple(fields), threshold.strip())

def schema_contracts(frame: dict[str, Any]) -> tuple[SchemaContract, ...]:
    return tuple(_parse_schema_contract(name, raw) for name, raw in sorted(frame["contracts"].items()))

def draw_frame(frame: dict[str, Any]) -> list[str]:
    """Deterministic without-replacement draw; independent of input row order."""

    seed = frame["seed"]
    return sorted(
        frame["document_ids"],
        key=lambda doc: (hashlib.sha256(f"{seed}|{doc}".encode("utf-8")).hexdigest(), doc),
    )[: frame["sample_size"]]

def seal_commitment(frame: dict[str, Any], frame_path: Path, run_id: str) -> dict[str, Any]:
    """Freeze the full sampling plan before any sampled row content is read."""

    payload = {
        "run_id": run_id,
        "tool_name": "tzgate",
        "tool_version": __version__,
        "dataset": frame["dataset"],
        "seed": frame["seed"],
        "frame_size": len(frame["document_ids"]),
        "sample_size": frame["sample_size"],
        "document_ids": list(frame["document_ids"]),
        "selected_ids": draw_frame(frame),
        "contracts": [item.to_dict() for item in schema_contracts(frame)],
    }
    encoded = canonical_json_line(payload).encode("utf-8")
    return {
        **payload,
        "frame_path": frame_path.as_posix(),
        "frame_sha256": sha256_bytes(frame_path.read_bytes()),
        "commitment_sha256": sha256_bytes(encoded),
        "commitment_payload": canonical_json_line(payload),
    }

def read_documents(files: tuple[Any, ...], encoding: str, delimiter: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Read every verified file; malformed rows are returned, never dropped."""

    records: list[dict[str, str]] = []
    bad_rows: list[dict[str, str]] = []
    for ref in files:
        with ref.path.open("r", encoding=encoding, newline="") as stream:
            reader = csv.reader(stream, delimiter=delimiter)
            header = next(reader, None)
            if header is None:
                bad_rows.append({"file": ref.label, "line": "1", "document_id": "", "reason": "empty file: missing header"})
                continue
            for row in reader:
                if not row or all(cell.strip() == "" for cell in row):
                    continue
                if len(row) != len(header):
                    bad_rows.append({"file": ref.label, "line": str(reader.line_num),
                                     "document_id": _cell(row, header, "document_id"),
                                     "reason": "column count does not match header"})
                    continue
                records.append(dict(zip(header, row)))
    return records, bad_rows

def _cell(row: list[str], header: list[str], column: str) -> str:
    try:
        return row[header.index(column)].strip()
    except ValueError:
        return ""

def preview_metadata(contract_path: Path, root: Path, frame_path: Path, run_id: str) -> dict[str, Any]:
    """Backwards-compatible preview plus the sealed population certificates."""

    from .population_certificate import evaluate_commitment

    frame = load_frame(frame_path)
    evaluated = evaluate_commitment(contract_path, root, frame, frame_path, run_id)
    by_name = {item["contract_name"]: item for item in evaluated["certificates"]}
    return {
        "run_id": run_id,
        "contract_sha256": evaluated["contract_sha256"],
        "rulebook_sha256": evaluated["rulebook_sha256"],
        "files": evaluated["files"],
        "frame_size": evaluated["frame_size"],
        "sample_size": evaluated["sample_size"],
        "selected_ids": evaluated["selected_ids"],
        "full_metadata_count": by_name["full_metadata"]["observed_successes"],
        "public_index_count": by_name["public_index"]["observed_successes"],
        "population_certificate": evaluated["certificates"],
        "commitment": evaluated["commitment"],
    }

