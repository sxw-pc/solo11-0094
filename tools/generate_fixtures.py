#!/usr/bin/env python3
"""Generate or verify the committed synthetic lake fixtures.

``--check`` is read-only and is what ``make demo`` uses. Regenerating is an
explicit maintainer action and never runs as part of a verification run.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

from fixture_data import FIXTURES

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "fixtures" / "lake"

def build_content() -> dict[str, bytes]:
    content: dict[str, bytes] = {}
    for dataset in sorted(FIXTURES):
        spec = FIXTURES[dataset]
        header = spec["header"]
        for relative, rows in spec["files"]:
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)
            content[f"{dataset}/{relative}"] = buffer.getvalue().encode("utf-8")
    return content

def check(out_dir: Path) -> int:
    content = build_content()
    problems: list[str] = []
    for relative, expected in sorted(content.items()):
        path = out_dir / relative
        if not path.is_file():
            problems.append(f"missing: {relative}")
        elif path.read_bytes() != expected:
            problems.append(f"outdated: {relative}")
    if out_dir.is_dir():
        actual = { path.relative_to(out_dir).as_posix() for path in out_dir.rglob("*.csv") if path.is_file() }
        expected_names = set(content)
        for relative in sorted(actual - expected_names):
            problems.append(f"unexpected: {relative}")
    if problems:
        for problem in problems:
            print(f"fixtures: {problem}", file=sys.stderr)
        return 1
    print(f"fixtures: ok ({len(content)} files under {out_dir.name}/)")
    return 0

def generate(out_dir: Path) -> int:
    content = build_content()
    for relative, data in sorted(content.items()):
        path = out_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"fixtures: wrote {len(content)} files under {out_dir}")
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument( "--check", action="store_true", help="compare without writing; exit 1 when fixtures differ", )
    args = parser.parse_args(argv)
    if args.check:
        return check(args.out.resolve())
    return generate(args.out.resolve())

if __name__ == "__main__":
    raise SystemExit(main())

