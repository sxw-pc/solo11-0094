from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tzgate.errors import OutputError
from tzgate.io_utils import sha256_file
from tzgate.pipeline import write_outputs

ARTIFACTS = ("report.json", "summary.md", "issues.csv", "report.html", "ledger.sqlite3")

def test_repeated_runs_are_byte_identical(tmp_path: Path, run_result):
    first = write_outputs(run_result, tmp_path / "a")
    second = write_outputs(run_result, tmp_path / "b")
    for name in ARTIFACTS:
        assert (first.out_dir / name).read_bytes() == (second.out_dir / name).read_bytes()

def test_hash_seed_does_not_change_outputs(tmp_path: Path, repo_root: Path):
    outputs = []
    for seed, directory in (("1", "seed-1"), ("987654", "seed-2")):
        out = tmp_path / directory
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tzgate",
                "verify",
                "--contract",
                "contracts/default.yaml",
                "--root",
                ".",
                "--out",
                str(out),
                "--run-id",
                "seed-check",
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(out)
    for name in ("report.json", "summary.md", "issues.csv", "report.html"):
        assert (outputs[0] / name).read_bytes() == (outputs[1] / name).read_bytes()

def test_output_inside_dataset_is_rejected(run_result, repo_root: Path):
    target = repo_root / "fixtures" / "lake" / "orders_local" / "not-created"
    with pytest.raises(OutputError):
        write_outputs(run_result, target)
    assert not target.exists()

def test_output_inside_fixture_lake_is_rejected(run_result, repo_root: Path):
    target = repo_root / "fixtures" / "lake" / "not-created"
    with pytest.raises(OutputError):
        write_outputs(run_result, target)
    assert not target.exists()

def test_fixtures_are_read_only(run_result, repo_root: Path, tmp_path: Path):
    lake = repo_root / "fixtures" / "lake"
    before = { path.relative_to(lake).as_posix(): sha256_file(path) for path in sorted(lake.rglob("*")) if path.is_file() }
    write_outputs(run_result, tmp_path / "out")
    after = { path.relative_to(lake).as_posix(): sha256_file(path) for path in sorted(lake.rglob("*")) if path.is_file() }
    assert before == after

