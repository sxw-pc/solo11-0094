from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tzgate.cli import main
from tzgate.io_utils import sha256_file


def _verify_args(repo_root: Path, out: Path, *extra: str) -> list[str]:
    return [
        "verify",
        "--contract",
        str(repo_root / "contracts" / "default.yaml"),
        "--root",
        str(repo_root),
        "--out",
        str(out),
        "--run-id",
        "cli-check",
        *extra,
    ]


def test_cli_verify_and_summary(tmp_path: Path, repo_root: Path, capsys):
    out = tmp_path / "out"
    assert main(_verify_args(repo_root, out)) == 0
    captured = capsys.readouterr()
    assert "status=fail" in captured.out
    assert "issues=13" in captured.out
    assert (out / "report.json").is_file()
    assert (out / "ledger.sqlite3").is_file()
    assert main(["summary", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "# 时界核验报告" in captured.out


def test_cli_fail_on_error(tmp_path: Path, repo_root: Path):
    assert main(_verify_args(repo_root, tmp_path / "out", "--fail-on", "error")) == 1


def test_cli_rejects_bad_run_id(tmp_path: Path, repo_root: Path):
    args = _verify_args(repo_root, tmp_path / "out")
    args[-1] = "bad id"
    assert main(args) == 2


def test_cli_missing_contract(tmp_path: Path, repo_root: Path):
    args = [
        "verify",
        "--contract",
        str(repo_root / "missing.yaml"),
        "--root",
        str(repo_root),
        "--out",
        str(tmp_path / "out"),
    ]
    assert main(args) == 2


def test_fixture_generator_check(repo_root: Path):
    result = subprocess.run(
        [sys.executable, "tools/generate_fixtures.py", "--check"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "fixtures: ok" in result.stdout


def test_fixture_generator_matches_committed(tmp_path: Path, repo_root: Path):
    target = tmp_path / "lake"
    result = subprocess.run(
        [sys.executable, "tools/generate_fixtures.py", "--out", str(target)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    committed = {
        path.relative_to(repo_root / "fixtures" / "lake").as_posix(): sha256_file(path)
        for path in sorted((repo_root / "fixtures" / "lake").rglob("*.csv"))
    }
    generated = {
        path.relative_to(target).as_posix(): sha256_file(path)
        for path in sorted(target.rglob("*.csv"))
    }
    assert generated == committed
    assert len(committed) == 12
