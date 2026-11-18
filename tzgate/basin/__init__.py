"""Verified basin monitor intake and exact joint-world attribution.

The package rebuilds every joint possible world consistent with the verified
sensor matrix, the single calibration bias shared across all slots and the
declared integer load bounds.  Legal decisions quantify over those *joint*
worlds; per-source independent bounds are never stitched into worlds that do
not exist.  A nominal single-bias preview is intentionally absent.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..discovery import discover_dataset
from ..engine import run_verification
from ..io_utils import canonical_json_text, sha256_bytes
from ..ledger import write_attribution_ledger
from .solver import AttributionError, attribute_basin
from .validation import REQUIRED_COLUMNS

__all__ = [
    "AttributionError",
    "attribute_basin",
    "load_basin_intake",
    "persist_attribution",
]


def load_basin_intake(
    contract_path: Path, root: Path, intake_path: Path, run_id: str
) -> dict[str, Any]:
    """Load intake rules and the verified monitor readings.

    Verification re-derives every hash, so a changed contract, rulebook or
    data file is observed here instead of being silently reused.
    """
    rules = json.loads(intake_path.read_text(encoding="utf-8"))
    result = run_verification(contract_path, root, run_id)
    if result.status != "pass":
        raise AttributionError("basin monitor dataset must verify cleanly")
    by_name = {dataset.spec.name: dataset for dataset in result.datasets}
    if set(by_name) != {rules["dataset"]}:
        raise AttributionError("basin dataset disagrees with verified contract")
    dataset = by_name[rules["dataset"]]
    refs = discover_dataset(dataset.spec).files
    if dataset.issue_count or len(refs) != 1:
        raise AttributionError("one clean basin monitor file required")
    ref = refs[0]
    with ref.path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        header = reader.fieldnames or []
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            raise AttributionError(
                f"verified basin file is missing columns: {', '.join(missing)}"
            )
        rows = list(reader)
    if len(rows) != dataset.record_count:
        raise AttributionError("verified row count changed under the engine")
    return {
        "run_id": run_id,
        "contract": {
            "path": result.contract.label,
            "sha256": result.contract.sha256,
        },
        "rulebook": {
            "path": result.contract.rulebook_label,
            "sha256": result.contract.rulebook_sha256,
        },
        "run_summary": result.summary,
        "file": ref.label,
        "file_sha256": sha256_bytes(ref.path.read_bytes()),
        "record_count": dataset.record_count,
        "readings": rows,
        "rules": rules,
    }


def persist_attribution(evidence: dict[str, Any], out_dir: Path) -> dict[str, Path]:
    """Write canonical JSON evidence and the attribution ledger tables."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "basin_attribution.json"
    ledger_path = out_dir / "attribution_ledger.sqlite3"
    payload = canonical_json_text(evidence)
    json_path.write_text(payload, encoding="utf-8")
    write_attribution_ledger(ledger_path, evidence, payload.encode("utf-8"))
    return {"json": json_path, "ledger": ledger_path}
