"""Deterministic discovery of Hive-style date partitions and data files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .errors import DiscoveryError
from .models import DatasetSpec


@dataclass(frozen=True)
class FileRef:
    path: Path
    label: str
    partition_date: date
    partition_label: str


@dataclass(frozen=True)
class Partition:
    date: date
    name: str
    label: str
    files: tuple[FileRef, ...]


@dataclass(frozen=True)
class DiscoveryResult:
    partitions: tuple[Partition, ...]
    files: tuple[FileRef, ...]
    invalid: tuple[tuple[str, str], ...]


def discover_dataset(spec: DatasetSpec) -> DiscoveryResult:
    root = spec.root_path
    if not root.is_dir():
        raise DiscoveryError(f"dataset root is not a directory: {spec.root_label}")
    regex = _partition_regex(spec.partition.pattern)
    partitions: list[Partition] = []
    invalid: list[tuple[str, str]] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise DiscoveryError(f"cannot list {spec.root_label}: {exc}") from exc
    for child in children:
        if not child.is_dir() or child.is_symlink():
            if child.is_dir() and child.is_symlink():
                invalid.append((_label(child, root, spec), "symbolic-link partition ignored"))
            continue
        match = regex.fullmatch(child.name)
        if match is None:
            continue
        partition_date = _parse_partition_date(match.group("date"), spec.partition.date_format)
        if partition_date is None:
            invalid.append((_label(child, root, spec), "partition name is not a valid date"))
            continue
        files, file_invalid = _discover_files(spec, child, partition_date)
        invalid.extend(file_invalid)
        partitions.append(
            Partition(
                date=partition_date,
                name=child.name,
                label=_label(child, root, spec),
                files=tuple(files),
            )
        )
    flattened = tuple(
        file_ref for partition in partitions for file_ref in partition.files
    )
    return DiscoveryResult(
        partitions=tuple(partitions),
        files=flattened,
        invalid=tuple(invalid),
    )


def _discover_files(
    spec: DatasetSpec, partition_dir: Path, partition_date: date
) -> tuple[list[FileRef], list[tuple[str, str]]]:
    files: list[FileRef] = []
    invalid: list[tuple[str, str]] = []
    try:
        matches = sorted(
            partition_dir.glob(spec.file_glob),
            key=lambda item: item.relative_to(partition_dir).as_posix(),
        )
    except OSError as exc:
        raise DiscoveryError(
            f"cannot list partition {_label(partition_dir, spec.root_path, spec)}: {exc}"
        ) from exc
    for path in matches:
        if not path.is_file() or path.is_symlink():
            if path.is_file() and path.is_symlink():
                invalid.append((_label(path, spec.root_path, spec), "symbolic-link file ignored"))
            continue
        try:
            resolved = path.resolve()
            resolved.relative_to(spec.root_path)
        except (OSError, ValueError):
            invalid.append((_label(path, spec.root_path, spec), "file escapes dataset root"))
            continue
        files.append(
            FileRef(
                path=resolved,
                label=_label(path, spec.root_path, spec),
                partition_date=partition_date,
                partition_label=_label(partition_dir, spec.root_path, spec),
            )
        )
    return files, invalid


def _partition_regex(pattern: str) -> re.Pattern[str]:
    prefix, suffix = pattern.split("{date}")
    return re.compile(
        "^" + re.escape(prefix) + r"(?P<date>[^/\\]+)" + re.escape(suffix) + "$"
    )


def _parse_partition_date(value: str, date_format: str) -> date | None:
    try:
        parsed = datetime.strptime(value, date_format).date()
    except ValueError:
        return None
    if parsed.strftime(date_format) != value:
        return None
    return parsed


def _label(path: Path, root: Path, spec: DatasetSpec) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return f"{spec.root_label}/{path.name}"
    return f"{spec.root_label}/{relative}"
