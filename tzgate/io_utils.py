"""Small deterministic I/O helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_text(value: Any) -> str:
    """Stable pretty JSON used by report.json and fixture checks."""

    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
    )
    return text + "\n"


def canonical_json_line(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to a sibling temporary file, then replace atomically.

    The temporary name is fixed rather than random. It is never content that
    appears in a report, and keeping it deterministic makes debugging easier.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        if temporary.exists():
            temporary.unlink()
        with temporary.open("wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def clip(text: str, limit: int = 512) -> str:
    """Bound evidence stored in reports while making truncation explicit."""

    if len(text) <= limit:
        return text
    return text[:limit] + "…[truncated]"


def as_posix(path: Path) -> str:
    return path.as_posix()
