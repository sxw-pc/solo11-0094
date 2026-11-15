"""Core data structures shared by the verifier, reports and ledger."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

SEVERITIES = ("error", "warning", "info")
SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}


@dataclass(frozen=True)
class TimeSpec:
    column: str
    format: str
    semantics: str
    zone: str
    required: bool = True


@dataclass(frozen=True)
class PartitionSpec:
    pattern: str
    date_format: str
    basis: str = "zone_local_date"


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    root_label: str
    root_path: Path
    partition: PartitionSpec
    time: TimeSpec
    source_column: str | None
    file_glob: str
    delimiter: str
    encoding: str
    checks: frozenset[str]

    def check_enabled(self, name: str) -> bool:
        return name in self.checks


@dataclass(frozen=True)
class Contract:
    label: str
    sha256: str
    root: Path
    rulebook_label: str
    rulebook_path: Path
    rulebook_sha256: str
    datasets: tuple[DatasetSpec, ...]


@dataclass(frozen=True)
class RecordContext:
    dataset: str
    file: str
    line: int
    column: str
    raw: str
    zone: str
    partition_date: date
    source: str | None = None


@dataclass(frozen=True)
class Issue:
    issue_id: str
    code: str
    severity: str
    dataset: str
    check: str
    file: str
    line: int
    column: str
    raw: str
    zone: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.dataset,
            self.file,
            self.line,
            self.column,
            self.code,
            self.issue_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "code": self.code,
            "severity": self.severity,
            "dataset": self.dataset,
            "check": self.check,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "raw": self.raw,
            "zone": self.zone,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass
class DatasetRun:
    spec: DatasetSpec
    partition_count: int
    file_count: int
    record_count: int
    issues: list[Issue] = field(default_factory=list)
    observed_offsets: set[str] = field(default_factory=set)

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    def summary(self) -> dict[str, Any]:
        observed = sorted(self.observed_offsets)
        return {
            "name": self.spec.name,
            "root": self.spec.root_label,
            "zone": self.spec.time.zone,
            "semantics": self.spec.time.semantics,
            "time_column": self.spec.time.column,
            "partitions": self.partition_count,
            "files": self.file_count,
            "records": self.record_count,
            "issues": self.issue_count,
            "observed_offsets": observed,
        }


@dataclass
class RunResult:
    run_id: str
    generated_at: str | None
    contract: Contract
    datasets: tuple[DatasetRun, ...]
    issues: tuple[Issue, ...]
    files_scanned: int
    records_scanned: int

    @property
    def by_severity(self) -> dict[str, int]:
        counts = Counter(issue.severity for issue in self.issues)
        return {severity: counts.get(severity, 0) for severity in SEVERITIES}

    @property
    def by_code(self) -> dict[str, int]:
        return dict(sorted(Counter(issue.code for issue in self.issues).items()))

    @property
    def status(self) -> str:
        if self.by_severity["error"]:
            return "fail"
        if self.by_severity["warning"]:
            return "warn"
        return "pass"

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "datasets": len(self.datasets),
            "partitions": sum(item.partition_count for item in self.datasets),
            "files_scanned": self.files_scanned,
            "records_scanned": self.records_scanned,
            "issues_total": len(self.issues),
            "by_severity": self.by_severity,
            "by_code": self.by_code,
        }


def utc_text(value: datetime) -> str:
    """Render a UTC instant without relying on the host locale."""

    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def local_text(value: datetime) -> str:
    """Render a naive local wall time without a timezone suffix."""

    return value.strftime("%Y-%m-%dT%H:%M:%S")
