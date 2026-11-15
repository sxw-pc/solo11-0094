from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

from tzgate.report import build_report, render_html, render_issues_csv, render_summary_md


def test_all_artifacts_exist(outputs):
    for path in outputs.files():
        assert path.is_file()
        assert path.stat().st_size > 0


def test_report_json_and_artifact_hashes(outputs):
    report = json.loads(outputs.report_json.read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.0"
    assert report["tool"]["name"] == "tzgate"
    assert report["run"] == {"run_id": "test-run"}
    assert report["summary"]["issues_total"] == 13
    assert report["summary"]["status"] == "fail"
    assert report["contract"]["path"] == "contracts/default.yaml"
    names = {item["name"] for item in report["artifacts"]}
    assert names == {"issues.csv", "summary.md", "report.html"}
    for artifact in report["artifacts"]:
        data = (outputs.out_dir / artifact["name"]).read_bytes()
        assert artifact["bytes"] == len(data)
        assert artifact["sha256"] == hashlib.sha256(data).hexdigest()


def test_issue_csv(outputs):
    rows = list(csv.reader(io.StringIO(outputs.issues_csv.read_text(encoding="utf-8"))))
    assert rows[0] == (
        "issue_id,code,severity,dataset,check,file,line,column,raw,zone,message".split(",")
    )
    assert len(rows) == 14
    assert {row[1] for row in rows[1:]} == {
        "TZ-DST-AMBIG", "TZ-DST-GAP", "TZ-OFF-DRIFT",
        "TZ-OFF-MISMATCH", "TZ-PARSE-001", "TZ-PART-001",
    }


def test_markdown_summary(outputs, run_result):
    text = outputs.summary_md.read_text(encoding="utf-8")
    assert "FAIL" in text
    assert "TZ-DST-GAP" in text
    assert "contracts/default.yaml" in text
    assert run_result.contract.sha256 in text
    assert "python -m tzgate verify" in text


def test_html_is_self_contained(outputs):
    text = outputs.report_html.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
    assert '<html lang="zh-CN">' in text
    assert "<style>" in text
    assert "TZ-DST-GAP" in text
    for forbidden in ("http://", "https://", "<link ", "src=\"http", "@import"):
        assert forbidden not in text


def test_html_escapes_evidence(run_result):
    bad_issue = replace(
        run_result.issues[0],
        raw="<script>alert(1)</script>",
        message="<img src=x onerror=alert(1)>",
    )
    bad_run = replace(run_result, issues=(bad_issue,))
    text = render_html(bad_run)
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<img src=x" not in text


def test_markdown_escapes_html(run_result):
    bad_issue = replace(run_result.issues[0], raw="<script>x</script>")
    text = render_summary_md(replace(run_result, issues=(bad_issue,)))
    assert "<script>x</script>" not in text
    assert "&lt;script&gt;x&lt;/script&gt;" in text


def test_report_omits_absolute_paths(run_result, repo_root: Path):
    report = build_report(run_result)
    serialized = json.dumps(report, ensure_ascii=False)
    assert str(repo_root) not in serialized
    assert "D:\\" not in serialized
    assert "file://" not in serialized
    assert "generated_at" not in report["run"]


def test_generated_at_is_injected_only_when_provided(run_result):
    injected = replace(run_result, generated_at="2024-01-01T00:00:00Z")
    report = build_report(injected)
    assert report["run"]["generated_at"] == "2024-01-01T00:00:00Z"
    assert "generated_at" not in build_report(run_result)["run"]


def test_csv_renderer_is_pure(run_result):
    first = render_issues_csv(run_result)
    second = render_issues_csv(run_result)
    assert first == second
    assert first.endswith("\n")
