from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tzgate.engine import run_verification  # noqa: E402
from tzgate.pipeline import write_outputs  # noqa: E402

@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT

@pytest.fixture(scope="session")
def contract_path() -> Path:
    return ROOT / "contracts" / "default.yaml"

@pytest.fixture(scope="session")
def run_result(contract_path: Path):
    return run_verification(contract_path, ROOT, "test-run")

@pytest.fixture()
def outputs(tmp_path: Path, run_result):
    return write_outputs(run_result, tmp_path / "out")

