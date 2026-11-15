"""Self-contained archival HTML report."""

from __future__ import annotations

import html
from typing import Any

from ..models import RunResult
from ..version import __version__
from .style import BASE_CSS

_STATUS_LABEL = {"fail": "FAIL", "warn": "WARN", "pass": "PASS"}


def render_html(run: RunResult) -> str:
    severity = run.by_severity
    status = run.status
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta name="generator" content="tzgate {_e(__version__)}">',
        f'<meta name="tzgate-run-id" content="{_e(run.run_id)}">',
        (
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; base-uri 'none'\">"
        ),
        f"<title>时界核验报告 · {_e(run.run_id)}</title>",
        f"<style>{BASE_CSS}</style>",
        "</head>",
        "<body>",
        '<header>',
        '<p class="eyebrow">tzgate / 时界核验台</p>',
        "<h1>时间语义与分区日界核验报告</h1>",
        (
            '<p class="lede">对本地湖表目录做确定性、只读的时间语义核验。'
            "结论只描述时区归属、偏移消歧、夏令时边界与分区日期，不判断业务值。</p>"
        ),
        '<div class="meta">',
        f'<span class="pill status-{status}">结论 {_STATUS_LABEL[status]}</span>',
        f'<span class="pill">run_id {_e(run.run_id)}</span>',
        f'<span class="pill">契约 {_e(run.contract.label)}</span>',
        f'<span class="pill">规则簿 {_e(run.contract.rulebook_label)}</span>',
        "</div>",
        "</header>",
        '<section class="cards" aria-label="摘要">',
        _card("问题总数", len(run.issues), "danger" if severity["error"] else ""),
        _card("Error", severity["error"], "danger" if severity["error"] else ""),
        _card("Warning", severity["warning"], "warn" if severity["warning"] else ""),
        _card("数据集", len(run.datasets), ""),
        _card("文件", run.files_scanned, ""),
        _card("记录", run.records_scanned, ""),
        "</section>",
        "<h2>数据集概览</h2>",
        _dataset_table(run),
        "<h2>问题分布</h2>",
        _code_table(run),
        "<h2>问题清单</h2>",
        (
            '<input class="filter" id="issue-filter" type="search" '
            'placeholder="过滤代码、数据集、文件、原始值或说明" '
            'aria-label="过滤问题">'
        ),
        _issue_table(run),
        "<h2>核验口径</h2>",
        "<ul>",
        "<li><code>local_wall</code>：无偏移的本地墙钟时间；夏令时空洞或回拨窗口视为错误。</li>",
        "<li><code>utc_instant</code>：必须带 <code>Z</code> 或显式偏移，按规则簿换算本地日期。</li>",
        "<li><code>offset_aware</code>：显式偏移必须匹配规则簿；同源多偏移汇总为漂移警告。</li>",
        "<li>分区日期必须等于记录在声明时区中的本地日期。</li>",
        "</ul>",
        "<script>",
        "(function(){",
        "  var input=document.getElementById('issue-filter');",
        "  var table=document.getElementById('issues-table');",
        "  if(!input||!table){return;}",
        "  input.addEventListener('input',function(){",
        "    var needle=input.value.toLowerCase();",
        "    table.querySelectorAll('tbody tr').forEach(function(row){",
        "      row.hidden=needle!==''&&row.textContent.toLowerCase().indexOf(needle)===-1;",
        "    });",
        "  });",
        "})();",
        "</script>",
        "<footer>",
        (
            f"tzgate {_e(__version__)} · 契约 sha256 <code>{_e(run.contract.sha256)}</code>"
            f" · 规则簿 sha256 <code>{_e(run.contract.rulebook_sha256)}</code>"
        ),
        "<br>本文件为自包含离线报告，不引用任何外部资源。",
        "</footer>",
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(parts)


def _card(label: str, value: Any, tone: str) -> str:
    tone_class = f" {tone}" if tone else ""
    return (
        '<div class="card">'
        f'<div class="label">{_e(label)}</div>'
        f'<div class="value{tone_class}">{_e(value)}</div>'
        "</div>"
    )


def _dataset_table(run: RunResult) -> str:
    rows: list[str] = [
        '<div class="table-wrap"><table>',
        "<thead><tr>"
        "<th>数据集</th><th>时区</th><th>语义</th>"
        '<th class="num">分区</th><th class="num">文件</th>'
        '<th class="num">记录</th><th class="num">问题</th><th>观测偏移</th>'
        "</tr></thead><tbody>",
    ]
    for dataset in run.datasets:
        offsets = ", ".join(sorted(dataset.observed_offsets)) or "—"
        rows.append(
            "<tr>"
            f"<td>{_e(dataset.spec.name)}</td>"
            f"<td>{_e(dataset.spec.time.zone)}</td>"
            f"<td>{_e(dataset.spec.time.semantics)}</td>"
            f'<td class="num">{dataset.partition_count}</td>'
            f'<td class="num">{dataset.file_count}</td>'
            f'<td class="num">{dataset.record_count}</td>'
            f'<td class="num">{dataset.issue_count}</td>'
            f"<td>{_e(offsets)}</td>"
            "</tr>"
        )
    rows.append("</tbody></table></div>")
    return "\n".join(rows)


def _code_table(run: RunResult) -> str:
    if not run.by_code:
        return '<div class="empty">未发现问题。</div>'
    rows = ['<div class="table-wrap"><table>', "<thead><tr><th>代码</th><th class=\"num\">数量</th></tr></thead><tbody>"]
    for code, count in run.by_code.items():
        rows.append(f"<tr><td><code>{_e(code)}</code></td><td class=\"num\">{count}</td></tr>")
    rows.append("</tbody></table></div>")
    return "\n".join(rows)


def _issue_table(run: RunResult) -> str:
    if not run.issues:
        return '<div class="empty">未发现问题。</div>'
    rows = [
        '<div class="table-wrap"><table id="issues-table">',
        "<thead><tr>"
        '<th class="num">#</th><th>级别</th><th>代码</th><th>数据集</th>'
        "<th>位置</th><th>字段</th><th>原始值</th><th>说明</th>"
        "</tr></thead><tbody>",
    ]
    for index, issue in enumerate(run.issues, start=1):
        location = f"{issue.file}:{issue.line}"
        rows.append(
            f'<tr id="{_e(issue.issue_id)}">'
            f'<td class="num">{index}</td>'
            f'<td><span class="sev sev-{_e(issue.severity)}">{_e(issue.severity)}</span></td>'
            f"<td><code>{_e(issue.code)}</code></td>"
            f"<td>{_e(issue.dataset)}</td>"
            f'<td class="location">{_e(location)}</td>'
            f"<td>{_e(issue.column) if issue.column else '—'}</td>"
            f'<td class="raw">{_e(issue.raw) if issue.raw else "—"}</td>'
            f"<td>{_e(issue.message)}</td>"
            "</tr>"
        )
    rows.append("</tbody></table></div>")
    return "\n".join(rows)


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)
