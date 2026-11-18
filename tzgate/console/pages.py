"""HTML pages for the read-only console."""

from __future__ import annotations

import html
import json
from typing import Any, Iterable
from urllib.parse import quote

from ..report.style import BASE_CSS, CONSOLE_CSS
from ..version import __version__
from .data import Ledger

_STATUS = {"fail": "FAIL", "warn": "WARN", "pass": "PASS"}

def render_index(ledger: Ledger, filters: dict[str, str]) -> str:
    run = ledger.run()
    datasets = ledger.datasets()
    issues = ledger.issues(
        dataset=filters.get("dataset") or None,
        code=filters.get("code") or None,
        severity=filters.get("severity") or None,
    )
    total = int(run["issues_total"])
    facets = ledger.facets()
    body: list[str] = [
        *_head(f"时界核验台 · {run['run_id']}"),
        '<p class="eyebrow">tzgate / read-only console</p>',
        "<h1>时界核验台</h1>",
        '<p class="lede">只读复核台，数据来自本地 SQLite 台账；服务仅监听 127.0.0.1。</p>',
        '<div class="meta">',
        f'<span class="pill status-{_e(run["status"])}">'
        f'{_STATUS.get(run["status"], run["status"])}</span>',
        f'<span class="pill">run_id {_e(run["run_id"])}</span>',
        f'<span class="pill">契约 {_e(run["contract_path"])}</span>',
        f'<span class="pill">tzgate {_e(__version__)}</span>',
        "</div>",
        '<nav class="tabs"><a class="active" href="/">问题台账</a>'
        '<a href="/api/issues">JSON API</a></nav>',
        '<section class="cards">',
        _card("问题", total, "danger" if int(run["errors"]) else ""),
        _card("Error", run["errors"], "danger" if int(run["errors"]) else ""),
        _card("Warning", run["warnings"], "warn" if int(run["warnings"]) else ""),
        _card("数据集", run["datasets"], ""),
        _card("文件", run["files_scanned"], ""),
        _card("记录", run["records_scanned"], ""),
        "</section>",
        "<h2>数据集</h2>",
        _dataset_table(datasets),
        "<h2>问题筛选</h2>",
        _filters(facets, filters),
        f'<p class="muted">显示 {len(issues)} / {total} 条问题。</p>',
        _issue_table(issues),
        "<footer>",
        f"台账只读打开：<code>{_e(ledger.path.name)}</code> · 契约 sha256 "
        f"<code>{_e(run['contract_sha256'])}</code> · 规则簿 sha256 "
        f"<code>{_e(run['rulebook_sha256'])}</code>",
        "</footer>",
        "</body></html>",
        "",
    ]
    return "\n".join(body)

def render_issue(ledger: Ledger, issue_id: str) -> str | None:
    issue = ledger.issue(issue_id)
    if issue is None:
        return None
    details = json.dumps( issue["details"], ensure_ascii=False, indent=2, sort_keys=True )
    rows = [
        ("issue_id", issue["issue_id"]),
        ("级别", issue["severity"]),
        ("代码", issue["code"]),
        ("数据集", issue["dataset"]),
        ("检查", issue["check_name"]),
        ("文件", issue["file"]),
        ("行号", str(issue["line"])),
        ("字段", issue["column_name"] or "—"),
        ("原始值", issue["raw"] or "—"),
        ("时区", issue["zone"]),
        ("说明", issue["message"]),
    ]
    parts: list[str] = [
        *_head(f"{issue['code']} · tzgate"),
        '<a class="back" href="/">← 返回问题台账</a>',
        '<p class="eyebrow">issue detail</p>',
        f"<h1>{_e(issue['code'])}</h1>",
        f'<p class="lede">{_e(issue["message"])}</p>',
        '<dl class="detail-grid">',
    ]
    for label, value in rows:
        parts.append(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>")
    parts.extend(
        [
            "</dl>",
            "<h2>结构化证据</h2>",
            f"<pre>{_e(details)}</pre>",
            "<footer>只读视图 · 服务仅监听 127.0.0.1。</footer>",
            "</body></html>",
            "",
        ]
    )
    return "\n".join(parts)

def render_not_found(message: str) -> str:
    return _simple_page("404", message)

def render_error(message: str) -> str:
    return _simple_page("复核台错误", message)

def _dataset_table(datasets: list[dict[str, Any]]) -> str:
    rows = [
        '<div class="table-wrap"><table>',
        "<thead><tr><th>数据集</th><th>时区</th><th>语义</th>"
        '<th class="num">分区</th><th class="num">文件</th>'
        '<th class="num">记录</th><th class="num">问题</th>'
        "<th>观测偏移</th></tr></thead><tbody>",
    ]
    for dataset in datasets:
        rows.append(
            "<tr>"
            f"<td>{_e(dataset['name'])}</td>"
            f"<td>{_e(dataset['zone'])}</td>"
            f"<td>{_e(dataset['semantics'])}</td>"
            f'<td class="num">{dataset["partitions"]}</td>'
            f'<td class="num">{dataset["files"]}</td>'
            f'<td class="num">{dataset["records"]}</td>'
            f'<td class="num">{dataset["issues"]}</td>'
            f"<td>{_e(dataset['observed_offsets'] or '—')}</td>"
            "</tr>"
        )
    rows.append("</tbody></table></div>")
    return "\n".join(rows)

def _filters(facets: dict[str, list[str]], filters: dict[str, str]) -> str:
    return "\n".join(
        [
            '<form class="filters" method="get" action="/">',
            _select("dataset", "数据集", facets["datasets"], filters.get("dataset", "")),
            _select("code", "代码", facets["codes"], filters.get("code", "")),
            _select(
                "severity",
                "级别",
                facets["severities"],
                filters.get("severity", ""),
            ),
            '<button type="submit">筛选</button>',
            '<a href="/">清除</a>',
            "</form>",
        ]
    )

def _select(name: str, label: str, values: Iterable[str], current: str) -> str:
    options = sorted(set(values))
    if current and current not in options:
        options = sorted(options + [current])
    parts = [f'<label>{_e(label)}<select name="{_e(name)}">']
    parts.append(f'<option value="">全部</option>')
    for value in options:
        selected = " selected" if value == current else ""
        parts.append(f'<option value="{_e(value)}"{selected}>{_e(value)}</option>')
    parts.append("</select></label>")
    return "".join(parts)

def _issue_table(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return '<div class="empty">当前筛选条件下没有问题。</div>'
    rows = [
        '<div class="table-wrap"><table>',
        "<thead><tr><th>级别</th><th>代码</th><th>数据集</th>"
        "<th>位置</th><th>字段</th><th>原始值</th><th>说明</th></tr></thead><tbody>",
    ]
    for issue in issues:
        location = f"{issue['file']}:{issue['line']}"
        link = f"/issue/{quote(issue['issue_id'], safe='')}"
        rows.append(
            "<tr>"
            f'<td><span class="sev sev-{_e(issue["severity"])}">'
            f'{_e(issue["severity"])}</span></td>'
            f'<td><a href="{_e(link)}"><code>{_e(issue["code"])}</code></a></td>'
            f"<td>{_e(issue['dataset'])}</td>"
            f'<td class="location">{_e(location)}</td>'
            f"<td>{_e(issue['column_name'] or '—')}</td>"
            f'<td class="raw">{_e(issue["raw"] or "—")}</td>'
            f"<td>{_e(issue['message'])}</td>"
            "</tr>"
        )
    rows.append("</tbody></table></div>")
    return "\n".join(rows)

def _card(label: str, value: Any, tone: str) -> str:
    tone_class = f" {tone}" if tone else ""
    return ( '<div class="card">' f'<div class="label">{_e(label)}</div>' f'<div class="value{tone_class}">{_e(value)}</div>' "</div>" )

def _head(title: str) -> list[str]:
    return [
        "<!doctype html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(title)}</title>",
        f"<style>{BASE_CSS}{CONSOLE_CSS}</style>",
        "</head><body>",
    ]

def _simple_page(title: str, message: str) -> str:
    return "\n".join(
        [
            *_head(f"{title} · tzgate"),
            f"<h1>{_e(title)}</h1>",
            f'<div class="error-box">{_e(message)}</div>',
            '<p><a href="/">返回问题台账</a></p>',
            "</body></html>",
            "",
        ]
    )

def _e(value: object) -> str:
    return html.escape(str(value), quote=True)

