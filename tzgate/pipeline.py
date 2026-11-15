"""End-to-end verification and artifact writing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .engine import run_verification
from .errors import OutputError
from .io_utils import (
    atomic_write_bytes,
    atomic_write_text,
    canonical_json_text,
    sha256_bytes,
)
from .ledger import write_ledger
from .models import RunResult
from .report import build_report, render_html, render_issues_csv, render_summary_md


@dataclass(frozen=True)
class Outputs:
    out_dir: Path
    report_json: Path
    summary_md: Path
    issues_csv: Path
    report_html: Path
    ledger: Path

    def files(self) -> tuple[Path, ...]:
        return (
            self.report_json,
            self.summary_md,
            self.issues_csv,
            self.report_html,
            self.ledger,
        )


def execute_verification(
    contract_path: Path,
    root: Path,
    out_dir: Path,
    run_id: str,
    generated_at: str | None = None,
) -> tuple[RunResult, Outputs]:
    run = run_verification(contract_path, root, run_id, generated_at)
    outputs = write_outputs(run, out_dir)
    return run, outputs


def write_outputs(run: RunResult, out_dir: Path) -> Outputs:
    target = out_dir.resolve()
    _guard_output(target, run)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"cannot create output directory: {exc}") from exc
    if not target.is_dir():
        raise OutputError("--out must be a directory")

    issues_text = render_issues_csv(run)
    summary_text = render_summary_md(run)
    html_text = render_html(run)
    issues_bytes = issues_text.encode("utf-8")
    summary_bytes = summary_text.encode("utf-8")
    html_bytes = html_text.encode("utf-8")

    issues_path = target / "issues.csv"
    summary_path = target / "summary.md"
    html_path = target / "report.html"
    report_path = target / "report.json"
    ledger_path = target / "ledger.sqlite3"

    atomic_write_bytes(issues_path, issues_bytes)
    atomic_write_bytes(summary_path, summary_bytes)
    atomic_write_bytes(html_path, html_bytes)

    artifacts = [
        _artifact("issues.csv", issues_bytes),
        _artifact("summary.md", summary_bytes),
        _artifact("report.html", html_bytes),
    ]
    report = build_report(run, artifacts)
    write_ledger(ledger_path, report)
    atomic_write_text(report_path, canonical_json_text(report))
    return Outputs(
        out_dir=target,
        report_json=report_path,
        summary_md=summary_path,
        issues_csv=issues_path,
        report_html=html_path,
        ledger=ledger_path,
    )


def _artifact(name: str, data: bytes) -> dict[str, object]:
    return {"name": name, "sha256": sha256_bytes(data), "bytes": len(data)}


def _guard_output(target: Path, run: RunResult) -> None:
    protected = (run.contract.root / "fixtures" / "lake").resolve()
    try:
        target.relative_to(protected)
    except ValueError:
        pass
    else:
        raise OutputError("--out must not be inside fixtures/lake")
    for spec in run.contract.datasets:
        root = spec.root_path.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        raise OutputError(
            f"--out must not be inside dataset root {spec.root_label}"
        )
