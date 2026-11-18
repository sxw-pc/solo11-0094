"""Helpers shared by checks and the engine."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from ..io_utils import clip
from ..models import Issue, RecordContext

def make_issue(
    ctx: RecordContext,
    *,
    code: str,
    severity: str,
    check: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> Issue:
    payload = "\x1f".join( (code, ctx.dataset, ctx.file, str(ctx.line), ctx.column, ctx.raw) )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return Issue(
        issue_id=f"iss-{digest}",
        code=code,
        severity=severity,
        dataset=ctx.dataset,
        check=check,
        file=ctx.file,
        line=ctx.line,
        column=ctx.column,
        raw=clip(ctx.raw),
        zone=ctx.zone,
        message=clip(message, 1000),
        details=dict(details or {}),
    )

