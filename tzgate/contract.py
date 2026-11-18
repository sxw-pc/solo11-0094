"""Load and validate the declarative verification contract."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml

from .errors import ContractError
from .io_utils import sha256_bytes
from .models import Contract, DatasetSpec, PartitionSpec, TimeSpec

SEMANTICS = {"local_wall", "utc_instant", "offset_aware"}
CHECK_NAMES = {"partition", "dst", "offset"}
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

def load_contract(path: Path, root: Path) -> Contract:
    root = root.resolve()
    if not root.is_dir():
        raise ContractError(f"--root is not a directory: {root.name}")
    contract_path = _inside(path.resolve(), root, "contract")
    try:
        data = contract_path.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read contract {contract_path.name}: {exc}") from exc
    try:
        raw = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ContractError(f"contract is not valid UTF-8 YAML: {exc}") from exc
    document = _mapping(raw, "contract")
    _only_keys(document, {"version", "rulebook", "datasets", "description"}, "contract")
    if document.get("version") != 1:
        raise ContractError("contract version must be 1")
    rulebook_ref = _relative_ref(document.get("rulebook"), "contract.rulebook")
    rulebook_path = _inside(contract_path.parent / rulebook_ref, root, "rulebook")
    try:
        rulebook_bytes = rulebook_path.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read rulebook {rulebook_path.name}: {exc}") from exc
    datasets_raw = document.get("datasets")
    if not isinstance(datasets_raw, list) or not datasets_raw:
        raise ContractError("contract.datasets must be a non-empty list")
    datasets = tuple( _parse_dataset(item, index, root) for index, item in enumerate(datasets_raw) )
    names = [item.name for item in datasets]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ContractError(f"duplicate dataset names: {', '.join(duplicates)}")
    return Contract(
        label=_label(contract_path, root), sha256=sha256_bytes(data), root=root,
        rulebook_label=_label(rulebook_path, root), rulebook_path=rulebook_path,
        rulebook_sha256=sha256_bytes(rulebook_bytes), datasets=datasets,
    )

def validate_zone_references(contract: Contract, zone_names: set[str]) -> None:
    missing = sorted( {item.time.zone for item in contract.datasets if item.time.zone not in zone_names} )
    if missing:
        raise ContractError(f"rulebook has no zone definition for: {', '.join(missing)}")

def _parse_dataset(raw: Any, index: int, root: Path) -> DatasetSpec:
    label = f"datasets[{index}]"
    item = _mapping(raw, label)
    _only_keys(
        item,
        {
            "name",
            "root",
            "partition",
            "time",
            "source",
            "file_glob",
            "delimiter",
            "encoding",
            "checks",
            "description",
        },
        label,
    )
    name = _string(item.get("name"), f"{label}.name")
    if _NAME_RE.fullmatch(name) is None:
        raise ContractError(f"{label}.name must match {_NAME_RE.pattern}")
    root_label = _relative_ref(item.get("root"), f"{label}.root")
    root_path = _inside(root / root_label, root, f"{label}.root")
    if not root_path.is_dir():
        raise ContractError(f"{label}.root is not a directory: {root_label}")
    partition = _parse_partition(item.get("partition"), f"{label}.partition")
    time = _parse_time(item.get("time"), f"{label}.time")
    source_column = _parse_source(item.get("source"), f"{label}.source")
    file_glob = _relative_glob(item.get("file_glob", "part-*.csv"), f"{label}.file_glob")
    delimiter = item.get("delimiter", ",")
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise ContractError(f"{label}.delimiter must be one character")
    encoding = item.get("encoding", "utf-8")
    if not isinstance(encoding, str) or not encoding.strip():
        raise ContractError(f"{label}.encoding must be a non-empty string")
    checks = _parse_checks(item.get("checks"), f"{label}.checks")
    return DatasetSpec(
        name=name, root_label=root_label, root_path=root_path, partition=partition,
        time=time, source_column=source_column, file_glob=file_glob,
        delimiter=delimiter, encoding=encoding, checks=checks,
    )

def _parse_partition(raw: Any, label: str) -> PartitionSpec:
    if raw is None:
        raw = {}
    item = _mapping(raw, label)
    _only_keys(item, {"pattern", "date_format", "basis", "description"}, label)
    pattern = item.get("pattern", "dt={date}")
    if not isinstance(pattern, str) or not pattern:
        raise ContractError(f"{label}.pattern must be a non-empty string")
    if "/" in pattern or "\\" in pattern:
        raise ContractError(f"{label}.pattern must be a single path component")
    if pattern.count("{date}") != 1 or pattern.replace("{date}", "").count("{") or pattern.replace("{date}", "").count("}"):
        raise ContractError(f"{label}.pattern must contain exactly one {{date}} placeholder")
    date_format = item.get("date_format", "%Y-%m-%d")
    if not isinstance(date_format, str) or not date_format:
        raise ContractError(f"{label}.date_format must be a non-empty string")
    try:
        sample = datetime(2024, 1, 2).strftime(date_format)
        datetime.strptime(sample, date_format)
    except ValueError as exc:
        raise ContractError(f"{label}.date_format is invalid: {exc}") from exc
    basis = item.get("basis", "zone_local_date")
    if basis != "zone_local_date":
        raise ContractError(f"{label}.basis only supports zone_local_date")
    return PartitionSpec(pattern=pattern, date_format=date_format, basis=basis)

def _parse_time(raw: Any, label: str) -> TimeSpec:
    item = _mapping(raw, label)
    _only_keys(item, {"column", "format", "semantics", "zone", "required"}, label)
    column = _string(item.get("column"), f"{label}.column")
    date_format = item.get("format", "iso8601")
    if not isinstance(date_format, str) or not date_format:
        raise ContractError(f"{label}.format must be a non-empty string")
    if date_format.lower() != "iso8601" and "%" not in date_format:
        raise ContractError(f"{label}.format must be iso8601 or a strptime pattern")
    semantics = _string(item.get("semantics"), f"{label}.semantics")
    if semantics not in SEMANTICS:
        raise ContractError(f"{label}.semantics must be one of {sorted(SEMANTICS)}")
    zone = _string(item.get("zone"), f"{label}.zone")
    required = item.get("required", True)
    if not isinstance(required, bool):
        raise ContractError(f"{label}.required must be true or false")
    return TimeSpec( column=column, format=date_format, semantics=semantics, zone=zone, required=required, )

def _parse_source(raw: Any, label: str) -> str | None:
    if raw is None:
        return None
    item = _mapping(raw, label)
    _only_keys(item, {"column"}, label)
    return _string(item.get("column"), f"{label}.column")

def _parse_checks(raw: Any, label: str) -> frozenset[str]:
    if raw is None:
        return frozenset(CHECK_NAMES)
    item = _mapping(raw, label)
    _only_keys(item, CHECK_NAMES, label)
    enabled: set[str] = set()
    for name, value in item.items():
        if not isinstance(value, bool):
            raise ContractError(f"{label}.{name} must be true or false")
        if value:
            enabled.add(name)
    return frozenset(enabled)

def _mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a mapping")
    return value

def _only_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ContractError(f"{label} has unknown keys: {', '.join(unknown)}")

def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    return value.strip()

def _relative_ref(value: Any, label: str) -> str:
    text = _string(value, label)
    if "\\" in text:
        raise ContractError(f"{label} must use forward slashes")
    if text.startswith("//") or _DRIVE_RE.match(text):
        raise ContractError(f"{label} must be relative")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ContractError(f"{label} must not escape --root")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise ContractError(f"{label} must name a subdirectory")
    return normalized

def _relative_glob(value: Any, label: str) -> str:
    text = _string(value, label)
    if "\\" in text or text.startswith("/"):
        raise ContractError(f"{label} must be a relative POSIX glob")
    if any(part == ".." for part in PurePosixPath(text).parts):
        raise ContractError(f"{label} must not escape the dataset root")
    return text

def _inside(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContractError(f"{label} must stay inside --root") from exc
    return resolved

def _label(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root).as_posix()

