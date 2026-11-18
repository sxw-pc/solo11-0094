"""DST gap and fold detection for declared local-wall timestamps."""

from __future__ import annotations

from ..models import Issue, RecordContext, local_text, utc_text
from ..offsets import format_offset
from ..rulebook import ZoneRules
from ..timestamp import ParsedTimestamp
from .common import make_issue

def check_local_wall( zone: ZoneRules, parsed: ParsedTimestamp, ctx: RecordContext ) -> list[Issue]:
    resolution = zone.resolve_local(parsed.wall)
    if resolution.kind == "normal":
        return []
    candidates = [
        {
            "utc": utc_text(candidate.instant_utc),
            "offset": format_offset(candidate.offset),
        }
        for candidate in resolution.candidates
    ]
    details = {
        "local_wall": local_text(resolution.wall),
        "zone": zone.name,
        "window_start": (
            local_text(resolution.window_start) if resolution.window_start else None
        ),
        "window_end": (
            local_text(resolution.window_end) if resolution.window_end else None
        ),
        "candidates": candidates,
        "known_offsets": list(zone.offset_strings),
    }
    if resolution.kind == "gap":
        message = ( f"本地墙钟时间 {local_text(resolution.wall)} 位于 {zone.name} " "夏令时前拨造成的空洞中，规则簿中不存在该时间" )
        return [ make_issue( ctx, code="TZ-DST-GAP", severity="error", check="dst", message=message, details=details, ) ]
    message = ( f"本地墙钟时间 {local_text(resolution.wall)} 位于 {zone.name} " "夏令时回拨窗口内，缺少显式偏移量，无法唯一消歧" )
    return [ make_issue( ctx, code="TZ-DST-AMBIG", severity="error", check="dst", message=message, details=details, ) ]

