"""End-to-end verification and artifact writing."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from .cert_ledger import ( fetch_commitment, insert_certificate, insert_commitment, open_cert_ledger, )
from .engine import run_verification
from .errors import OutputError
from .io_utils import ( atomic_write_bytes, atomic_write_text, canonical_json_text, sha256_bytes, )
from .ledger import write_ledger
from .metadata_certificate import load_frame
from .models import RunResult
from .population_certificate import evaluate_commitment
from .report import build_report, render_html, render_issues_csv, render_summary_md

@dataclass(frozen=True)
class Outputs:
    out_dir: Path
    report_json: Path
    summary_md: Path
    issues_csv: Path
    report_html: Path
    ledger: Path
    commitment_json: Path | None = None
    certificate_json: Path | None = None

    def files(self) -> tuple[Path, ...]:
        base = ( self.report_json, self.summary_md, self.issues_csv, self.report_html, self.ledger, )
        extras = tuple( path for path in (self.commitment_json, self.certificate_json) if path is not None )
        return base + extras

def execute_verification(
    contract_path: Path,
    root: Path,
    out_dir: Path,
    run_id: str,
    generated_at: str | None = None,
    frame_path: Path | None = None,
) -> tuple[RunResult, Outputs]:
    if frame_path is not None:
        _guard_replay(out_dir, run_id)
    run = run_verification(contract_path, root, run_id, generated_at)
    outputs = write_outputs(run, out_dir)
    if frame_path is not None:
        outputs = write_certificate_outputs( contract_path, root, out_dir, run_id, frame_path, outputs )
    return run, outputs

def _guard_replay(out_dir: Path, run_id: str) -> None:
    target = out_dir.resolve()
    commitment_path = target / "sample_commitment.json"
    if commitment_path.exists():
        raise OutputError(
            f"{commitment_path.name} for run {run_id} is already sealed; the "
            "commitment cannot be overwritten and the sample cannot be re-rolled"
        )
    ledger_path = target / "ledger.sqlite3"
    if ledger_path.exists():
        connection = open_cert_ledger(ledger_path)
        try:
            if fetch_commitment(connection, run_id) is not None:
                raise OutputError(
                    f"sample commitment for run {run_id} is already sealed in "
                    "the ledger; the sample cannot be re-rolled or re-used"
                )
        finally:
            connection.close()

def write_certificate_outputs(
    contract_path: Path,
    root: Path,
    out_dir: Path,
    run_id: str,
    frame_path: Path,
    outputs: Outputs,
) -> Outputs:
    target = out_dir.resolve()
    commitment_path = target / "sample_commitment.json"
    certificate_path = target / "population_certificate.json"
    frame = load_frame(frame_path)
    evaluated = evaluate_commitment(contract_path, root, frame, frame_path, run_id)
    connection = open_cert_ledger(outputs.ledger)
    try:
        insert_commitment(connection, evaluated["commitment"])
        for certificate in evaluated["certificates"]:
            insert_certificate(connection, certificate)
    finally:
        connection.close()
    atomic_write_text(commitment_path, canonical_json_text(evaluated["commitment"]))
    atomic_write_text(certificate_path, canonical_json_text(evaluated["certificates"]))
    return dataclasses.replace( outputs, commitment_json=commitment_path, certificate_json=certificate_path)

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

    artifacts = [ _artifact("issues.csv", issues_bytes), _artifact("summary.md", summary_bytes), _artifact("report.html", html_bytes), ]
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
        raise OutputError( f"--out must not be inside dataset root {spec.root_label}" )

