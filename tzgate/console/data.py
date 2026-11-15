"""Read-only access to the SQLite ledger."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..errors import LedgerError

_ISSUE_COLUMNS = (
    'issue_id, code, severity, dataset, "check" AS check_name, file, line, '
    "column_name, raw, zone, message"
)
_ORDER = (
    " ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 "
    "ELSE 2 END, dataset, file, line, issue_id"
)


class Ledger:
    def __init__(self, path: Path):
        self.path = path.resolve()
        if not self.path.is_file():
            raise LedgerError(
                f"ledger not found: {self.path.name}; run the verify command first"
            )
        uri = self.path.as_uri() + "?mode=ro&immutable=1"
        try:
            self._connection = sqlite3.connect(
                uri, uri=True, check_same_thread=False
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("SELECT 1 FROM run LIMIT 1")
        except sqlite3.Error as exc:
            raise LedgerError(f"cannot open ledger read-only: {exc}") from exc
        self._lock = threading.Lock()

    def run(self) -> dict[str, Any]:
        rows = self._query("SELECT * FROM run LIMIT 1")
        if not rows:
            raise LedgerError("ledger has no run record")
        return rows[0]

    def datasets(self) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM datasets ORDER BY name")

    def issues(
        self,
        dataset: str | None = None,
        code: str | None = None,
        severity: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[str] = []
        if dataset:
            clauses.append("dataset = ?")
            params.append(dataset)
        if code:
            clauses.append("code = ?")
            params.append(code)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self._query(
            f"SELECT {_ISSUE_COLUMNS} FROM issues{where}{_ORDER}", params
        )

    def issue(self, issue_id: str) -> dict[str, Any] | None:
        rows = self._query(
            f"SELECT {_ISSUE_COLUMNS}, details_json FROM issues WHERE issue_id = ?",
            [issue_id],
        )
        if not rows:
            return None
        row = rows[0]
        try:
            row["details"] = json.loads(row.pop("details_json"))
        except (TypeError, ValueError):
            row["details"] = {}
        return row

    def facets(self) -> dict[str, list[str]]:
        return {
            "datasets": [
                row["dataset"]
                for row in self._query(
                    "SELECT DISTINCT dataset FROM issues ORDER BY dataset"
                )
            ],
            "codes": [
                row["code"]
                for row in self._query(
                    "SELECT DISTINCT code FROM issues ORDER BY code"
                )
            ],
            "severities": [
                row["severity"]
                for row in self._query(
                    "SELECT DISTINCT severity FROM issues ORDER BY severity"
                )
            ],
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _query(
        self, sql: str, params: list[str] | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            try:
                cursor = self._connection.execute(sql, params or [])
                return [dict(row) for row in cursor.fetchall()]
            except sqlite3.Error as exc:
                raise LedgerError(f"ledger query failed: {exc}") from exc
