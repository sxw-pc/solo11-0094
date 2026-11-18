"""CSV row ingestion and per-record checks."""

from __future__ import annotations

import csv
from datetime import date

from .checks import ( OffsetTracker, check_local_wall, check_offset_aware, check_partition, )
from .checks.common import make_issue
from .discovery import FileRef
from .errors import TimestampParseError
from .models import DatasetSpec, Issue, RecordContext
from .offsets import format_offset
from .rulebook import ZoneRules
from .timestamp import parse_timestamp

_EPOCH = date(1970, 1, 1)

def process_file(
    spec: DatasetSpec,
    zone: ZoneRules,
    file_ref: FileRef,
    issues: list[Issue],
    observed_offsets: set[str],
    tracker: OffsetTracker | None,
) -> int:
    count = 0
    reader: csv.reader | None = None
    try:
        with file_ref.path.open("r", encoding=spec.encoding, newline="") as handle:
            reader = csv.reader(handle, delimiter=spec.delimiter)
            header = next(reader, None)
            if header is None:
                issues.append( _file_issue(spec, file_ref, 1, "", "TZ-SHAPE-001", "文件为空，缺少表头") )
                return 0
            normalized = [ cell.lstrip("\N{ZERO WIDTH NO-BREAK SPACE}").strip() for cell in header ]
            if spec.time.column not in normalized:
                issues.append(
                    _file_issue(
                        spec,
                        file_ref,
                        1,
                        spec.time.column,
                        spec.delimiter.join(header),
                        "TZ-SHAPE-001",
                        f"表头缺少时间列 {spec.time.column}",
                    )
                )
                return 0
            time_index = normalized.index(spec.time.column)
            source_index: int | None = None
            if spec.source_column is not None:
                if spec.source_column in normalized:
                    source_index = normalized.index(spec.source_column)
                else:
                    issues.append(
                        _file_issue(
                            spec,
                            file_ref,
                            1,
                            spec.source_column,
                            spec.delimiter.join(header),
                            "TZ-SHAPE-001",
                            f"表头缺少来源列 {spec.source_column}",
                        )
                    )
            for row in reader:
                if not row or all(cell.strip() == "" for cell in row):
                    continue
                count += 1
                line_no = reader.line_num
                if time_index >= len(row):
                    issues.append(
                        _file_issue(
                            spec,
                            file_ref,
                            line_no,
                            spec.time.column,
                            spec.delimiter.join(row),
                            "TZ-SHAPE-002",
                            "数据行列数不足，缺少时间字段",
                        )
                    )
                    continue
                raw = row[time_index]
                if not raw.strip() and not spec.time.required:
                    continue
                try:
                    parsed = parse_timestamp(raw, spec.time.format)
                except TimestampParseError as exc:
                    issues.append( _file_issue( spec, file_ref, line_no, spec.time.column, raw, "TZ-PARSE-001", f"时间字段无法按契约格式解析：{exc}", ) )
                    continue
                if parsed.offset is not None:
                    observed_offsets.add(format_offset(parsed.offset))
                source = None
                if source_index is not None and source_index < len(row):
                    source = row[source_index].strip()
                ctx = RecordContext(
                    dataset=spec.name,
                    file=file_ref.label,
                    line=line_no,
                    column=spec.time.column,
                    raw=raw,
                    zone=zone.name,
                    partition_date=file_ref.partition_date,
                    source=source,
                )
                issues.extend(_check_record(spec, zone, parsed, ctx, tracker))
    except UnicodeError as exc:
        issues.append( _file_issue( spec, file_ref, 1, "", "", "TZ-IO-001", f"文件不是有效的 {spec.encoding} 文本：{exc}", ) )
    except csv.Error as exc:
        line_no = reader.line_num if reader is not None else 1
        issues.append( _file_issue( spec, file_ref, line_no, "", "", "TZ-IO-001", f"CSV 解析失败：{exc}" ) )
    except OSError as exc:
        reason = exc.strerror or exc.__class__.__name__
        issues.append( _file_issue( spec, file_ref, 1, "", "", "TZ-IO-001", f"文件读取失败：{reason}" ) )
    return count

def plain_issue(
    spec: DatasetSpec,
    file_label: str,
    line: int,
    column: str,
    raw: str,
    *,
    code: str,
    severity: str,
    check: str,
    message: str,
    details,
) -> Issue:
    ctx = RecordContext(
        dataset=spec.name,
        file=file_label,
        line=line,
        column=column,
        raw=raw,
        zone=spec.time.zone,
        partition_date=_EPOCH,
    )
    return make_issue( ctx, code=code, severity=severity, check=check, message=message, details=details, )

def _check_record(spec, zone, parsed, ctx, tracker) -> list[Issue]:
    semantics = spec.time.semantics
    if semantics == "local_wall":
        issues: list[Issue] = []
        if parsed.instant_utc is not None:
            issues.append(
                make_issue(
                    ctx,
                    code="TZ-SEM-001",
                    severity="warning",
                    check="semantics",
                    message="local_wall 语义的时间字段不应携带显式偏移量",
                    details={
                        "offset": format_offset(parsed.offset) if parsed.offset else None
                    },
                )
            )
        if spec.check_enabled("partition"):
            issues.extend(check_partition(zone, parsed, semantics, ctx))
        if spec.check_enabled("dst"):
            issues.extend(check_local_wall(zone, parsed, ctx))
        return issues
    if parsed.instant_utc is None:
        return [
            make_issue(
                ctx,
                code="TZ-SEM-002",
                severity="error",
                check="semantics",
                message=f"{semantics} 语义要求时间字段包含 Z 或显式偏移量",
                details={"semantics": semantics},
            )
        ]
    issues = []
    if spec.check_enabled("partition"):
        issues.extend(check_partition(zone, parsed, semantics, ctx))
    if semantics == "offset_aware" and spec.check_enabled("offset"):
        issues.extend(check_offset_aware(zone, parsed, ctx, tracker))
    return issues

def _file_issue( spec: DatasetSpec, file_ref: FileRef, line: int, column: str, raw: str, code: str, message: str, ) -> Issue:
    if code.startswith("TZ-SHAPE"):
        check = "shape"
    elif code.startswith("TZ-PARSE"):
        check = "parse"
    else:
        check = "io"
    return plain_issue(
        spec,
        file_ref.label,
        line,
        column,
        raw,
        code=code,
        severity="error",
        check=check,
        message=message,
        details={"partition": file_ref.partition_label},
    )

