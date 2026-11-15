"""Explicit-offset validation and same-source offset drift detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import Issue, RecordContext, local_text, utc_text
from ..offsets import format_offset
from ..rulebook import ZoneRules
from ..timestamp import ParsedTimestamp
from .common import make_issue


@dataclass(frozen=True)
class Observation:
    source: str
    ctx: RecordContext
    actual: timedelta
    expected: timedelta
    instant: datetime


@dataclass
class OffsetTracker:
    """Collect offset evidence per source so drift is reported once."""

    observations: dict[str, list[Observation]] = field(default_factory=dict)

    def observe(
        self,
        ctx: RecordContext,
        actual: timedelta,
        expected: timedelta,
        instant: datetime,
    ) -> None:
        source = _source_name(ctx.source)
        self.observations.setdefault(source, []).append(
            Observation(
                source=source,
                ctx=ctx,
                actual=actual,
                expected=expected,
                instant=instant,
            )
        )

    def finalize(self) -> list[Issue]:
        issues: list[Issue] = []
        for source in sorted(self.observations):
            rows = self.observations[source]
            mismatches = sorted(
                (row for row in rows if row.actual != row.expected),
                key=lambda row: (row.ctx.file, row.ctx.line, row.ctx.column, row.ctx.raw),
            )
            observed = sorted({format_offset(row.actual) for row in rows})
            if len(observed) < 2 or not mismatches:
                continue
            expected = sorted({format_offset(row.expected) for row in rows})
            first = mismatches[0]
            details = {
                "source": source,
                "observed_offsets": observed,
                "expected_offsets": expected,
                "observation_count": len(rows),
                "mismatch_count": len(mismatches),
                "first_mismatch_utc": utc_text(first.instant),
            }
            message = (
                f"来源 {source} 在同一时间列混用了多个偏移量 {', '.join(observed)}，"
                f"其中 {len(mismatches)} 条与规则簿不一致"
            )
            issues.append(
                make_issue(
                    first.ctx,
                    code="TZ-OFF-DRIFT",
                    severity="warning",
                    check="offset",
                    message=message,
                    details=details,
                )
            )
        return issues


def check_offset_aware(
    zone: ZoneRules,
    parsed: ParsedTimestamp,
    ctx: RecordContext,
    tracker: OffsetTracker | None = None,
) -> list[Issue]:
    if parsed.instant_utc is None or parsed.offset is None:
        return []
    expected = zone.offset_at_utc(parsed.instant_utc)
    if tracker is not None:
        tracker.observe(ctx, parsed.offset, expected, parsed.instant_utc)
    if parsed.offset == expected:
        return []
    local_value = parsed.instant_utc + expected
    details = {
        "declared_offset": format_offset(parsed.offset),
        "expected_offset": format_offset(expected),
        "utc_instant": utc_text(parsed.instant_utc),
        "local_time_in_zone": local_text(local_value.replace(tzinfo=None)),
        "zone": zone.name,
        "source": ctx.source,
    }
    message = (
        f"显式偏移 {format_offset(parsed.offset)} 与规则簿中 {zone.name} "
        f"在该时刻的偏移 {format_offset(expected)} 不一致"
    )
    return [
        make_issue(
            ctx,
            code="TZ-OFF-MISMATCH",
            severity="error",
            check="offset",
            message=message,
            details=details,
        )
    ]


def _source_name(source: str | None) -> str:
    if source is None:
        return "(未声明来源)"
    if not source:
        return "(空来源)"
    return source
