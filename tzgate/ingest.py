"""CSV row ingestion for one-clock and two-clock contracts."""

from __future__ import annotations

import csv
from datetime import date

from .checks import (
    OffsetTracker,
    check_dual_partition,
    check_local_wall,
    check_offset_aware,
    check_partition,
)
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
    arrival_zone: ZoneRules | None = None,
) -> int:
    event_spec, arrival_spec = spec.time.event, spec.time.arrival
    count, reader = 0, None
    try:
        with file_ref.path.open("r", encoding=spec.encoding, newline="") as handle:
            reader = csv.reader(handle, delimiter=spec.delimiter)
            header = next(reader, None)
            if header is None:
                issues.append(_file_issue(spec, file_ref, 1, "", "TZ-SHAPE-001", "文件为空，缺少表头"))
                return 0
            normalized = _normalized(header)
            clocks = ([("事件时间", event_spec), ("到达时间", arrival_spec)]
                      if arrival_spec is not None else [("时间", event_spec)])
            missing = [(label, item) for label, item in clocks if item.column not in normalized]
            if missing:
                for label, item in missing:
                    issues.append(_file_issue(
                        spec, file_ref, 1, item.column, spec.delimiter.join(header),
                        "TZ-SHAPE-001", f"表头缺少{label}列 {item.column}"))
                return 0
            event_index = normalized.index(event_spec.column)
            arrival_index = normalized.index(arrival_spec.column) if arrival_spec else None
            source_index = None
            if spec.source_column in normalized:
                source_index = normalized.index(spec.source_column)
            elif spec.source_column is not None:
                issues.append(_file_issue(
                    spec, file_ref, 1, spec.source_column, spec.delimiter.join(header),
                    "TZ-SHAPE-001", f"表头缺少来源列 {spec.source_column}"))
            indices = [event_index] + ([arrival_index] if arrival_index is not None else [])
            for row in reader:
                if not row or all(cell.strip() == "" for cell in row):
                    continue
                count += 1
                line_no = reader.line_num
                if max(indices) >= len(row):
                    message = (
                        "数据行列数不足，缺少时间字段" if arrival_index is None
                        else "数据行列数不足，缺少事件或到达时间字段")
                    issues.append(_file_issue(
                        spec, file_ref, line_no, event_spec.column,
                        spec.delimiter.join(row), "TZ-SHAPE-002", message))
                    continue
                raw = row[event_index]
                if not raw.strip() and not event_spec.required and arrival_spec is None:
                    continue
                event = _parse(
                    spec, file_ref, line_no, event_spec, raw,
                    "时间" if arrival_spec is None else "事件时间", issues, observed_offsets)
                arrival = None
                if arrival_spec is not None:
                    arrival = _parse(
                        spec, file_ref, line_no, arrival_spec, row[arrival_index],
                        "到达时间", issues, observed_offsets)
                if event is None or (arrival_spec is not None and arrival is None):
                    continue
                source = (
                    row[source_index].strip() if source_index is not None
                    and source_index < len(row) else None)
                ctx = RecordContext(
                    dataset=spec.name, file=file_ref.label, line=line_no,
                    column=event_spec.column, raw=raw, zone=zone.name,
                    partition_date=file_ref.partition_date, source=source,
                )
                issues.extend(_check_record(
                    spec, zone, arrival_zone, event, arrival, ctx, tracker))
    except UnicodeError as exc:
        issues.append(_file_issue(
            spec, file_ref, 1, "", "TZ-IO-001",
            f"文件不是有效的 {spec.encoding} 文本：{exc}",
        ))
    except csv.Error as exc:
        line_no = reader.line_num if reader is not None else 1
        issues.append(_file_issue(
            spec, file_ref, line_no, "", "TZ-IO-001", f"CSV 解析失败：{exc}"
        ))
    except OSError as exc:
        reason = exc.strerror or exc.__class__.__name__
        issues.append(_file_issue(
            spec, file_ref, 1, "", "TZ-IO-001", f"文件读取失败：{reason}"
        ))
    return count


def _parse(spec, file_ref, line_no, column, raw, label, issues, observed):
    if not raw.strip() and not column.required:
        return None
    try:
        parsed = parse_timestamp(raw, column.format)
    except TimestampParseError as exc:
        issues.append(_file_issue(
            spec, file_ref, line_no, column.column, raw, "TZ-PARSE-001",
            f"{label}字段无法按契约格式解析：{exc}",
        ))
        return None
    if parsed.offset is not None:
        observed.add(format_offset(parsed.offset))
    return parsed


def _check_record(spec, zone, arrival_zone, event, arrival, ctx, tracker):
    event_spec = spec.time.event
    semantics = event_spec.semantics
    results: list[Issue] = []
    if semantics == "local_wall":
        if event.instant_utc is not None:
            results.append(make_issue(
                ctx, code="TZ-SEM-001", severity="warning", check="semantics",
                message="local_wall 语义的时间字段不应携带显式偏移量",
                details={"offset": format_offset(event.offset) if event.offset else None},
            ))
        if arrival is None:
            if spec.check_enabled("partition"):
                results.extend(check_partition(zone, event, semantics, ctx))
            if spec.check_enabled("dst"):
                results.extend(check_local_wall(zone, event, ctx))
            return results
        resolution = zone.resolve_local(event.wall)
        if resolution.kind == "gap" and spec.check_enabled("dst"):
            # A gap is invalid input, not a partition mismatch. In dual mode a
            # fold is resolved by the arrival clock, so no AMBIG issue is raised.
            results.extend(check_local_wall(zone, event, ctx))
    elif event.instant_utc is None:
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
    if arrival is not None:
        arrival_spec = spec.time.arrival
        assert arrival_zone is not None and spec.time.max_arrival_lag is not None
        if arrival.instant_utc is None:
            arrival_ctx = RecordContext(
                dataset=ctx.dataset, file=ctx.file, line=ctx.line,
                column=arrival_spec.column, raw=arrival.text,
                zone=arrival_zone.name, partition_date=ctx.partition_date,
                source=ctx.source,
            )
            results.append(make_issue(
                arrival_ctx, code="TZ-SEM-002", severity="error", check="semantics",
                message=f"{arrival_spec.semantics} 语义要求到达时间字段包含 Z 或显式偏移量",
                details={"semantics": arrival_spec.semantics, "clock": "arrival"},
            ))
            return results
        if spec.check_enabled("partition"):
            results.extend(check_dual_partition(
                zone, arrival_zone, event, semantics, arrival, ctx,
                spec.partition.basis, spec.time.max_arrival_lag,
            ))
    elif spec.check_enabled("partition"):
        results.extend(check_partition(zone, event, semantics, ctx))
    if semantics == "offset_aware" and spec.check_enabled("offset"):
        results.extend(check_offset_aware(zone, event, ctx, tracker))
    return results


def plain_issue(spec, file_label, line, column, raw, *, code, severity, check, message, details) -> Issue:
    ctx = RecordContext(
        dataset=spec.name, file=file_label, line=line, column=column, raw=raw,
        zone=spec.time.zone, partition_date=_EPOCH,
    )
    return make_issue(ctx, code=code, severity=severity, check=check, message=message, details=details)


def _normalized(header: list[str]) -> list[str]:
    return [cell.lstrip("\N{ZERO WIDTH NO-BREAK SPACE}").strip() for cell in header]


def _file_issue(spec, file_ref, line, column, raw, code, message) -> Issue:
    check = "shape" if code.startswith("TZ-SHAPE") else "parse" if code.startswith("TZ-PARSE") else "io"
    return plain_issue(
        spec, file_ref.label, line, column, raw, code=code, severity="error",
        check=check, message=message, details={"partition": file_ref.partition_label},
    )
