"""Flat issue table for spreadsheets and diff-friendly archival."""

from __future__ import annotations

import csv
import io

from ..models import RunResult

HEADER = (
    "issue_id",
    "code",
    "severity",
    "dataset",
    "check",
    "file",
    "line",
    "column",
    "raw",
    "zone",
    "message",
)


def render_issues_csv(run: RunResult) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    for issue in run.issues:
        writer.writerow(
            (
                issue.issue_id,
                issue.code,
                issue.severity,
                issue.dataset,
                issue.check,
                issue.file,
                issue.line,
                issue.column,
                issue.raw,
                issue.zone,
                issue.message,
            )
        )
    return buffer.getvalue()
