"""Partition alignment: legacy single clock and dual event/arrival clocks.

The dual path resolves the event wall clock into every rulebook candidate
instant and pairs each with arrival before deciding the declared basis, so a
DST fold is never silently collapsed to one candidate.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..models import Issue, RecordContext, local_text, utc_text
from ..offsets import format_offset
from ..rulebook import Candidate, ZoneRules
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


def _evidence(candidate: Candidate, arrival: datetime, max_lag: timedelta) -> dict:
    lag = arrival - candidate.instant_utc
    state = "early" if lag < timedelta(0) else "late" if lag > max_lag else "ok"
    return {"utc": utc_text(candidate.instant_utc), "offset": format_offset(candidate.offset),
            "lag_seconds": int(lag.total_seconds()), "state": state}


def _verdict(evidence: list[dict]) -> str:
    states = [item["state"] for item in evidence]
    if "ok" in states:
        # A visible late candidate prevents reporting a false precise lag.
        return "indeterminate" if "late" in states else "all_compliant"
    return "all_late" if "late" in states else "order_invalid"


def evaluate_dual_partition(
    event_zone, arrival_zone, event, semantics, arrival, basis, max_lag,
) -> dict:
    """Partition decision plus per-candidate lag evidence."""
    instant = arrival.instant_utc
    if instant is None:
        raise ValueError("arrival clock must resolve to a UTC instant")
    arrival_date = (instant + arrival_zone.offset_at_utc(instant)).date()
    if semantics == "local_wall":
        resolution = event_zone.resolve_local(event.wall)
        event_date, candidates = resolution.wall.date(), resolution.candidates
        event_utc = utc_text(candidates[0].instant_utc) if len(candidates) == 1 else None
    else:
        if event.instant_utc is None:
            raise ValueError("event clock must resolve to a UTC instant")
        offset = event_zone.offset_at_utc(event.instant_utc)
        candidates = (Candidate(offset, event.instant_utc),)
        event_date = (event.instant_utc + offset).date()
        event_utc = utc_text(event.instant_utc)
    evidence = [_evidence(item, instant, max_lag) for item in candidates]
    return {
        "verdict": _verdict(evidence) if evidence else "gap", "basis": basis,
        "expected": event_date if basis == "event_local_date" else arrival_date,
        "event_date": event_date, "arrival_date": arrival_date,
        "candidates": evidence, "event_utc": event_utc, "arrival_instant": instant}


def check_dual_partition(
    event_zone: ZoneRules, arrival_zone: ZoneRules, event: ParsedTimestamp,
    semantics: str, arrival: ParsedTimestamp, ctx: RecordContext,
    basis: str, max_lag: timedelta,
) -> list[Issue]:
    result = evaluate_dual_partition(
        event_zone, arrival_zone, event, semantics, arrival, basis, max_lag)
    verdict = result["verdict"]
    if verdict == "gap":
        return []  # the DST check owns unresolved input
    expected = result["expected"]
    local = result["event_date"]
    details = {
        "partition_date": ctx.partition_date.isoformat(), "partition_basis": basis,
        "expected_local_date": expected.isoformat() if expected else None,
        "event_local_date": local.isoformat() if local else None,
        "arrival_local_date": result["arrival_date"].isoformat(),
        "event_column": ctx.column, "event_zone": event_zone.name,
        "event_utc": result["event_utc"], "arrival_zone": arrival_zone.name,
        "arrival_utc": utc_text(result["arrival_instant"]),
        "max_arrival_lag_seconds": int(max_lag.total_seconds()),
        "lag_verdict": verdict, "candidates": result["candidates"]}
    if verdict == "order_invalid":
        return [_issue(ctx, "TZ-LAG-ORDER", "warning", "arrival", details,
                       "到达时刻早于所有候选事件时刻，时钟顺序无效；不据此判定分区归属")]
    if expected != ctx.partition_date:
        return [_issue(ctx, "TZ-PART-001", "error", "partition", details,
                       f"记录按分区依据 {basis} 应落在 {expected.isoformat()}，"
                       f"与分区日期 {ctx.partition_date.isoformat()} 不一致")]
    if verdict == "all_compliant":
        return []

    late = verdict == "all_late"
    limit = int(max_lag.total_seconds())
    suffix = f"所有候选事件时刻的到达滞后均超过契约上限 {limit} 秒"
    arrival_route = basis == "arrival_local_date"
    if arrival_route:
        code = "TZ-PART-LAG-001" if late else "TZ-PART-LAG-002"
        message = (f"到达日分区未获授权：{suffix}" if late else
                   "到达日分区无法唯一判定：候选事件时刻的到达滞后部分合规、部分超时")
    else:
        code = "TZ-LAG-LATE" if late else "TZ-LAG-INDETERMINATE"
        message = (f"事件日分区正确，但{suffix}" if late else
                   "事件日分区正确，但候选事件时刻的到达滞后无法唯一判定")
    return [_issue(
        ctx, code, "error" if late and arrival_route else "warning",
        "partition" if arrival_route else "arrival", details, message,
    )]


def _issue(ctx, code, severity, check, details, message) -> Issue:
    return make_issue(ctx, code=code, severity=severity, check=check,
                      message=message, details=details)
