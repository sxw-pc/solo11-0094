"""Human-readable Markdown report."""

from __future__ import annotations

from ..models import RunResult
from ..version import __version__

_STATUS_LABEL = {
    "fail": "FAIL（存在阻断级时间语义问题）",
    "warn": "WARN（仅有警告）",
    "pass": "PASS（未发现问题）",
}


def render_summary_md(run: RunResult) -> str:
    lines: list[str] = []
    lines.append("# 时界核验报告")
    lines.append("")
    lines.append(f"> 结论：**{_STATUS_LABEL[run.status]}**")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 运行编号 | {_code(run.run_id)} |")
    if run.generated_at is not None:
        lines.append(f"| 生成时间（注入） | {_code(run.generated_at)} |")
    lines.append(f"| 工具版本 | {_code('tzgate ' + __version__)} |")
    lines.append(
        f"| 契约 | {_code(run.contract.label)}<br>sha256 `{run.contract.sha256}` |"
    )
    lines.append(
        f"| 规则簿 | {_code(run.contract.rulebook_label)}"
        f"<br>sha256 `{run.contract.rulebook_sha256}` |"
    )
    lines.append(
        f"| 数据集 / 分区 / 文件 / 记录 | {len(run.datasets)} / "
        f"{sum(item.partition_count for item in run.datasets)} / "
        f"{run.files_scanned} / {run.records_scanned} |"
    )
    severity = run.by_severity
    lines.append(
        f"| 问题 | {len(run.issues)} 条（error={severity['error']}, "
        f"warning={severity['warning']}, info={severity['info']}） |"
    )
    lines.append("")
    lines.append("## 数据集概览")
    lines.append("")
    lines.append(
        "| 数据集 | 时区 | 语义 | 分区 | 文件 | 记录 | 问题 | 观测偏移 |"
    )
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | --- |")
    for dataset in run.datasets:
        offsets = ", ".join(sorted(dataset.observed_offsets)) or "—"
        lines.append(
            f"| {_cell(dataset.spec.name)} | {_cell(dataset.spec.time.zone)} | "
            f"{_cell(dataset.spec.time.semantics)} | {dataset.partition_count} | "
            f"{dataset.file_count} | {dataset.record_count} | "
            f"{dataset.issue_count} | {_cell(offsets)} |"
        )
    lines.append("")
    lines.append("## 问题分布")
    lines.append("")
    lines.append("| 代码 | 数量 |")
    lines.append("| --- | ---: |")
    if run.by_code:
        for code, count in run.by_code.items():
            lines.append(f"| {_code(code)} | {count} |")
    else:
        lines.append("| — | 0 |")
    lines.append("")
    lines.append("## 问题清单")
    lines.append("")
    lines.append("| # | 级别 | 代码 | 数据集 | 位置 | 字段 | 原始值 | 说明 |")
    lines.append("| ---: | --- | --- | --- | --- | --- | --- | --- |")
    if run.issues:
        for index, issue in enumerate(run.issues, start=1):
            position = f"{issue.file}:{issue.line}"
            lines.append(
                f"| {index} | {_cell(issue.severity)} | {_code(issue.code)} | "
                f"{_cell(issue.dataset)} | {_code(position)} | "
                f"{_code(issue.column) if issue.column else '—'} | "
                f"{_code(issue.raw) if issue.raw else '—'} | "
                f"{_cell(issue.message)} |"
            )
    else:
        lines.append("| — | — | — | — | — | — | — | 未发现问题 |")
    lines.append("")
    lines.append("## 核验口径")
    lines.append("")
    lines.append("- `local_wall`：字段必须是无偏移的本地墙钟时间；落入夏令时空洞或回拨窗口时报错。")
    lines.append("- `utc_instant`：字段必须带 `Z` 或显式偏移；按规则簿换算为数据集时区的本地日期。")
    lines.append("- `offset_aware`：显式偏移必须与该时刻规则簿偏移一致；同源多偏移会汇总为漂移警告。")
    lines.append("- 分区日界：分区日期必须等于记录在声明时区中的本地日期。")
    lines.append("- 本工具只读输入，不写回湖目录，不发起任何网络请求。")
    lines.append("")
    lines.append("## 复现")
    lines.append("")
    lines.append("```sh")
    lines.append(
        f"python -m tzgate verify --contract {run.contract.label} --root . "
        f"--out <OUT> --run-id {run.run_id}"
    )
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _cell(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("|", "\\|")


def _code(value: object) -> str:
    text = _cell(value)
    if "`" in text:
        return text.replace("`", "'")
    return f"`{text}`"
