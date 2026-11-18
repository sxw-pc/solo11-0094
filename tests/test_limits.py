from __future__ import annotations

from pathlib import Path


def test_every_python_file_stays_under_300_lines(repo_root: Path):
    paths = sorted(
        path
        for directory in ("tzgate", "tools", "tests")
        for path in (repo_root / directory).rglob("*.py")
        if path.is_file()
    )
    assert paths
    for path in paths:
        count = sum(1 for _ in path.open(encoding="utf-8"))
        assert count <= 300, f"{path.relative_to(repo_root)} has {count} lines"


def test_total_python_size_is_in_range(repo_root: Path):
    paths = sorted(
        path
        for directory in ("tzgate", "tools", "tests")
        for path in (repo_root / directory).rglob("*.py")
        if path.is_file()
    )
    total = sum(sum(1 for _ in path.open(encoding="utf-8")) for path in paths)
    assert 2500 <= total <= 6500, total
