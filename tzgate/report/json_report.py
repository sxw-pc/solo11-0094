"""Machine-readable report assembly."""

from __future__ import annotations

from typing import Any

from ..models import RunResult
from ..version import __version__


def build_report(
    run: RunResult, artifacts: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    run_meta: dict[str, Any] = {"run_id": run.run_id}
    if run.generated_at is not None:
        run_meta["generated_at"] = run.generated_at
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "tool": {"name": "tzgate", "version": __version__},
        "run": run_meta,
        "contract": {
            "path": run.contract.label,
            "sha256": run.contract.sha256,
        },
        "rulebook": {
            "path": run.contract.rulebook_label,
            "sha256": run.contract.rulebook_sha256,
        },
        "summary": run.summary,
        "datasets": [dataset.summary() for dataset in run.datasets],
        "issues": [issue.to_dict() for issue in run.issues],
    }
    if artifacts is not None:
        report["artifacts"] = sorted(artifacts, key=lambda item: item["name"])
    return report
