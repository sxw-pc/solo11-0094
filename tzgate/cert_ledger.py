"""Immutable SQLite storage for pre-registered commitments and certificates.

The main verifier ledger (``ledger.py``) is rewritten atomically on every
run. Publication evidence has the opposite requirement: a commitment is
sealed *before* sampled row contents are read and must never be overwritten,
so it lives in dedicated tables whose plain ``INSERT`` statements fail on
the primary/unique keys instead of silently replacing the sealed evidence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .errors import LedgerError
from .io_utils import canonical_json_line

CERT_SCHEMA = """
CREATE TABLE IF NOT EXISTS sample_commitment (
    run_id TEXT PRIMARY KEY, commitment_sha256 TEXT NOT NULL UNIQUE,
    dataset TEXT NOT NULL, seed TEXT NOT NULL, frame_size INTEGER NOT NULL,
    sample_size INTEGER NOT NULL, document_ids_json TEXT NOT NULL,
    selected_ids_json TEXT NOT NULL, contracts_json TEXT NOT NULL,
    frame_sha256 TEXT NOT NULL, tool_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS population_certificate (
    run_id TEXT NOT NULL, contract_name TEXT NOT NULL,
    commitment_sha256 TEXT NOT NULL, required_fields_json TEXT NOT NULL,
    frame_size INTEGER NOT NULL, sample_size INTEGER NOT NULL,
    observed_successes INTEGER NOT NULL, lower_bound INTEGER NOT NULL,
    threshold_text TEXT NOT NULL, decision TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (run_id, contract_name),
    FOREIGN KEY (run_id) REFERENCES sample_commitment(run_id)
);
"""
_COMMITMENT_COLUMNS = (
    ("run_id", "run_id"), ("commitment_sha256", "commitment_sha256"),
    ("dataset", "dataset"), ("seed", "seed"), ("frame_size", "frame_size"),
    ("sample_size", "sample_size"), ("document_ids", "document_ids_json"),
    ("selected_ids", "selected_ids_json"), ("contracts", "contracts_json"),
    ("frame_sha256", "frame_sha256"), ("tool_version", "tool_version"),
)
_CERTIFICATE_COLUMNS = (
    ("run_id", "run_id"), ("contract_name", "contract_name"),
    ("commitment_sha256", "commitment_sha256"),
    ("required_fields", "required_fields_json"), ("frame_size", "frame_size"),
    ("sample_size", "sample_size"), ("observed_successes", "observed_successes"),
    ("lower_bound", "lower_bound"), ("threshold_text", "threshold_text"),
    ("decision", "decision"), ("evidence", "evidence_json"),
)
_JSON_COLUMNS = {"document_ids", "selected_ids", "contracts", "required_fields", "evidence"}

def open_cert_ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(path)
        connection.executescript(CERT_SCHEMA)
        connection.commit()
        return connection
    except sqlite3.Error as exc:
        raise LedgerError(f"cannot open certificate ledger: {exc}") from exc

def _insert(connection: sqlite3.Connection, table: str, payload: dict[str, Any],
            columns: tuple[tuple[str, str], ...], message: str) -> None:
    values = [ canonical_json_line(payload[key]) if key in _JSON_COLUMNS else payload[key] for key, _ in columns ]
    names = ", ".join(column for _, column in columns)
    placeholders = ", ".join("?" for _ in columns)
    try:
        connection.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", values)
        connection.commit()
    except sqlite3.IntegrityError as exc:
        raise LedgerError(message) from exc
    except sqlite3.Error as exc:
        raise LedgerError(f"cannot write {table}: {exc}") from exc

def insert_commitment(connection: sqlite3.Connection, commitment: dict[str, Any]) -> None:
    _insert(
        connection, "sample_commitment", commitment, _COMMITMENT_COLUMNS,
        f"sample commitment for run {commitment['run_id']} already sealed; "
        "re-running must not present the same frame as a new sample",
    )

def insert_certificate(connection: sqlite3.Connection, certificate: dict[str, Any]) -> None:
    _insert(
        connection, "population_certificate", certificate, _CERTIFICATE_COLUMNS,
        f"certificate {certificate['contract_name']} for run "
        f"{certificate['run_id']} already exists",
    )

def fetch_commitment(connection: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    try:
        cursor = connection.execute( "SELECT * FROM sample_commitment WHERE run_id = ?", (run_id,))
    except sqlite3.Error as exc:
        raise LedgerError(f"cannot read sample commitment: {exc}") from exc
    row = cursor.fetchone()
    if row is None:
        return None
    return {key: value for (key, _), value in zip(_COMMITMENT_COLUMNS, row)}

