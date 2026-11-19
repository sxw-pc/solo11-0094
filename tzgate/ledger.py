"""SQLite ledger written next to the reports and read by the console."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from .io_utils import canonical_json_line

SCHEMA = """
CREATE TABLE run (
    run_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
    tool_name TEXT NOT NULL, tool_version TEXT NOT NULL, generated_at TEXT,
    contract_path TEXT NOT NULL, contract_sha256 TEXT NOT NULL,
    rulebook_path TEXT NOT NULL, rulebook_sha256 TEXT NOT NULL,
    status TEXT NOT NULL, datasets INTEGER NOT NULL, partitions INTEGER NOT NULL,
    files_scanned INTEGER NOT NULL, records_scanned INTEGER NOT NULL,
    issues_total INTEGER NOT NULL, errors INTEGER NOT NULL,
    warnings INTEGER NOT NULL, infos INTEGER NOT NULL
);
CREATE TABLE datasets (
    name TEXT PRIMARY KEY, root TEXT NOT NULL, zone TEXT NOT NULL,
    semantics TEXT NOT NULL, time_column TEXT NOT NULL,
    partitions INTEGER NOT NULL, files INTEGER NOT NULL,
    records INTEGER NOT NULL, issues INTEGER NOT NULL,
    observed_offsets TEXT NOT NULL
);
CREATE TABLE issues (
    issue_id TEXT PRIMARY KEY, code TEXT NOT NULL, severity TEXT NOT NULL,
    dataset TEXT NOT NULL, "check" TEXT NOT NULL, file TEXT NOT NULL,
    line INTEGER NOT NULL, column_name TEXT NOT NULL, raw TEXT NOT NULL,
    zone TEXT NOT NULL, message TEXT NOT NULL, details_json TEXT NOT NULL
);
CREATE TABLE artifacts (
    name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL
);
"""

STOWAGE_SCHEMA = """
CREATE TABLE stowage_run (
    run_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
    contract_path TEXT NOT NULL, contract_sha256 TEXT NOT NULL,
    rulebook_path TEXT NOT NULL, rulebook_sha256 TEXT NOT NULL,
    manifest_file TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
    intake_path TEXT NOT NULL, intake_sha256 TEXT NOT NULL,
    containers INTEGER NOT NULL, stacks INTEGER NOT NULL, ports INTEGER NOT NULL
);
CREATE TABLE stowage_plan (
    contract TEXT PRIMARY KEY, carried_json TEXT NOT NULL,
    rejected_json TEXT NOT NULL, slots_json TEXT NOT NULL,
    initial_mass_json TEXT NOT NULL, post_departure_mass_json TEXT NOT NULL,
    port_calls_json TEXT NOT NULL, rehandles INTEGER NOT NULL,
    freight_cents INTEGER NOT NULL, rehandle_cost_cents INTEGER NOT NULL,
    net_cents INTEGER NOT NULL, evidence_json TEXT NOT NULL
);
"""


def _replace_atomically(path: Path, fill) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        temporary.unlink()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary)
        fill(connection)
        connection.commit()
        connection.close()
        connection = None
        os.replace(temporary, path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    finally:
        if connection is not None:
            connection.close()
            if temporary.exists():
                temporary.unlink()


def write_stowage_ledger(path: Path, document: dict[str, Any]) -> None:
    """Persist the stowage decision document into its own SQLite ledger."""

    def fill(connection: sqlite3.Connection) -> None:
        connection.executescript(STOWAGE_SCHEMA)
        connection.execute(
            """INSERT INTO stowage_run (
                run_id, schema_version, contract_path, contract_sha256,
                rulebook_path, rulebook_sha256, manifest_file, manifest_sha256,
                intake_path, intake_sha256, containers, stacks, ports
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                document["run_id"], document["schema_version"],
                document["contract"]["path"], document["contract"]["sha256"],
                document["rulebook"]["path"], document["rulebook"]["sha256"],
                document["manifest"]["file"], document["manifest"]["sha256"],
                document["intake"]["path"], document["intake"]["sha256"],
                len(document["containers"]), len(document["stacks"]),
                len(document["port_order"]),
            ),
        )
        for name in sorted(document["plans"]):
            plan = document["plans"][name]
            connection.execute(
                """INSERT INTO stowage_plan (
                    contract, carried_json, rejected_json, slots_json,
                    initial_mass_json, post_departure_mass_json, port_calls_json,
                    rehandles, freight_cents, rehandle_cost_cents, net_cents,
                    evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name, canonical_json_line(plan["carried"]),
                    canonical_json_line(plan["rejected"]),
                    canonical_json_line(plan["slots"]),
                    canonical_json_line(plan["initial_mass"]),
                    canonical_json_line(plan["post_departure_mass"]),
                    canonical_json_line(plan["port_calls"]),
                    plan["rehandles"], plan["freight_cents"],
                    plan["rehandle_cost_cents"], plan["net_cents"],
                    canonical_json_line(plan["evidence"]),
                ),
            )

    _replace_atomically(path, fill)


def write_ledger(path: Path, report: dict[str, Any]) -> None:
    def fill(connection: sqlite3.Connection) -> None:
        connection.executescript(SCHEMA)
        _insert_run(connection, report)
        _insert_datasets(connection, report)
        _insert_issues(connection, report)
        _insert_artifacts(connection, report)

    _replace_atomically(path, fill)


def _insert_run(connection: sqlite3.Connection, report: dict[str, Any]) -> None:
    summary = report["summary"]
    severity = summary["by_severity"]
    run = report["run"]
    contract = report["contract"]
    rulebook = report["rulebook"]
    tool = report["tool"]
    connection.execute(
        """INSERT INTO run (
            run_id, schema_version, tool_name, tool_version, generated_at,
            contract_path, contract_sha256, rulebook_path, rulebook_sha256,
            status, datasets, partitions, files_scanned, records_scanned,
            issues_total, errors, warnings, infos
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run["run_id"],
            report["schema_version"],
            tool["name"],
            tool["version"],
            run.get("generated_at"),
            contract["path"],
            contract["sha256"],
            rulebook["path"],
            rulebook["sha256"],
            summary["status"],
            summary["datasets"],
            summary["partitions"],
            summary["files_scanned"],
            summary["records_scanned"],
            summary["issues_total"],
            severity["error"],
            severity["warning"],
            severity["info"],
        ),
    )


def _insert_datasets(connection: sqlite3.Connection, report: dict[str, Any]) -> None:
    for dataset in sorted(report["datasets"], key=lambda item: item["name"]):
        connection.execute(
            """INSERT INTO datasets (
                name, root, zone, semantics, time_column, partitions, files,
                records, issues, observed_offsets
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dataset["name"],
                dataset["root"],
                dataset["zone"],
                dataset["semantics"],
                dataset["time_column"],
                dataset["partitions"],
                dataset["files"],
                dataset["records"],
                dataset["issues"],
                ",".join(dataset["observed_offsets"]),
            ),
        )


def _insert_issues(connection: sqlite3.Connection, report: dict[str, Any]) -> None:
    for issue in report["issues"]:
        connection.execute(
            """INSERT INTO issues (
                issue_id, code, severity, dataset, "check", file, line,
                column_name, raw, zone, message, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                issue["issue_id"],
                issue["code"],
                issue["severity"],
                issue["dataset"],
                issue["check"],
                issue["file"],
                issue["line"],
                issue["column"],
                issue["raw"],
                issue["zone"],
                issue["message"],
                canonical_json_line(issue["details"]),
            ),
        )


def _insert_artifacts(connection: sqlite3.Connection, report: dict[str, Any]) -> None:
    for artifact in sorted(report.get("artifacts", []), key=lambda item: item["name"]):
        row = (artifact["name"], artifact["sha256"], artifact["bytes"])
        connection.execute("INSERT INTO artifacts (name, sha256, bytes) VALUES (?, ?, ?)", row)
