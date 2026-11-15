"""Partition date alignment against the record's local calendar date."""

from __future__ import annotations

from ..models import Issue, RecordContext, local_text, utc_text
from ..offsets import format_offset
from ..rulebook import ZoneRules
from ..timestamp import ParsedTimestamp
from .common import make_issue


def check_partition(
    zone: ZoneRules,
    parsed: ParsedTimestamp,
    semantics: str,
    ctx: RecordContext,
) -> list[Issue]:
    if semantics == "local_wall":
        # The partition basis is the zone-local date, and a local-wall record
        # already carries that date directly. Ambiguity/gaps do not move it.
        local_date = parsed.wall.date()
        local_value = parsed.wall
        instant_text = None
        offset_text = None
    else:
        if parsed.instant_utc is None:
            return []
        offset = zone.offset_at_utc(parsed.instant_utc)
        local_value = parsed.instant_utc + offset
        local_date = local_value.date()
        instant_text = utc_text(parsed.instant_utc)
        offset_text = format_offset(offset)
    if local_date == ctx.partition_date:
        return []
    details = {
        "partition_date": ctx.partition_date.isoformat(),
        "record_local_date": local_date.isoformat(),
        "record_local_time": local_text(local_value.replace(tzinfo=None)),
        "utc_instant": instant_text,
        "zone": zone.name,
        "zone_offset": offset_text,
    }
    message = (
        f"记录按 {zone.name} 换算后的本地日期为 {local_date.isoformat()}，"
        f"与分区日期 {ctx.partition_date.isoformat()} 不一致"
    )
    return [
        make_issue(
            ctx,
            code="TZ-PART-001",
            severity="error",
            check="partition",
            message=message,
            details=details,
        )
    ]
