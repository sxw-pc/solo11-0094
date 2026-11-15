"""Verification engine: discover files, run checks and aggregate results."""

from __future__ import annotations

from itertools import chain
from pathlib import Path

from .checks import OffsetTracker
from .contract import load_contract, validate_zone_references
from .discovery import discover_dataset
from .ingest import plain_issue, process_file
from .models import DatasetRun, DatasetSpec, Issue, RunResult
from .rulebook import ZoneRules, load_rulebook


def run_verification(
    contract_path: Path,
    root: Path,
    run_id: str,
    generated_at: str | None = None,
) -> RunResult:
    contract = load_contract(contract_path, root)
    rulebook = load_rulebook(contract.rulebook_path, contract.rulebook_sha256)
    validate_zone_references(contract, set(rulebook.zones))
    dataset_runs: list[DatasetRun] = []
    for spec in sorted(contract.datasets, key=lambda item: item.name):
        zone = rulebook.zones[spec.time.zone]
        dataset_runs.append(_verify_dataset(spec, zone))
    issues = tuple(
        sorted(
            chain.from_iterable(item.issues for item in dataset_runs),
            key=lambda item: item.sort_key,
        )
    )
    return RunResult(
        run_id=run_id,
        generated_at=generated_at,
        contract=contract,
        datasets=tuple(dataset_runs),
        issues=issues,
        files_scanned=sum(item.file_count for item in dataset_runs),
        records_scanned=sum(item.record_count for item in dataset_runs),
    )


def _verify_dataset(spec: DatasetSpec, zone: ZoneRules) -> DatasetRun:
    discovery = discover_dataset(spec)
    issues: list[Issue] = []
    for file_label, reason in discovery.invalid:
        issues.append(
            plain_issue(
                spec,
                file_label,
                0,
                "",
                file_label.rsplit("/", 1)[-1],
                code="TZ-DISC-001",
                severity="warning",
                check="discovery",
                message=f"发现不可核验的输入路径：{reason}",
                details={"reason": reason},
            )
        )
    if not discovery.partitions:
        issues.append(
            plain_issue(
                spec,
                spec.root_label,
                0,
                "",
                "",
                code="TZ-DISC-002",
                severity="warning",
                check="discovery",
                message="数据集目录下没有匹配分区模式的日期分区",
                details={"pattern": spec.partition.pattern},
            )
        )
    tracker = (
        OffsetTracker()
        if spec.source_column is not None and spec.check_enabled("offset")
        else None
    )
    observed_offsets: set[str] = set()
    record_count = 0
    for file_ref in discovery.files:
        record_count += process_file(
            spec, zone, file_ref, issues, observed_offsets, tracker
        )
    if tracker is not None:
        issues.extend(tracker.finalize())
    return DatasetRun(
        spec=spec,
        partition_count=len(discovery.partitions),
        file_count=len(discovery.files),
        record_count=record_count,
        issues=issues,
        observed_offsets=observed_offsets,
    )
