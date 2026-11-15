"""Command-line interface for tzgate."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .console.server import serve_console
from .errors import InputError, TzGateError
from .models import SEVERITY_RANK
from .pipeline import execute_verification
from .version import __version__

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DEFAULT_CONTRACT = "contracts/default.yaml"
_DEFAULT_OUT = "out/demo"
_DEFAULT_RUN_ID = "demo-001"
_FAIL_THRESHOLD = {"none": 99, "warning": 1, "error": 2}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tzgate",
        description="时界核验台：离线核验时间语义、偏移、夏令时与分区日界。",
    )
    parser.add_argument("--version", action="version", version=f"tzgate {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="运行核验并写出全部报告产物")
    verify.add_argument("--contract", default=_DEFAULT_CONTRACT, help="契约 YAML 路径")
    verify.add_argument("--root", default=".", help="路径解析基准目录")
    verify.add_argument("--out", default=_DEFAULT_OUT, help="输出目录")
    verify.add_argument("--run-id", default=_DEFAULT_RUN_ID, help="确定性运行编号")
    verify.add_argument("--generated-at", default=None, help="可选的注入式生成时间")
    verify.add_argument(
        "--fail-on",
        choices=("none", "warning", "error"),
        default="none",
        help="达到该级别时以状态码 1 退出（默认不因问题退出）",
    )
    console = subparsers.add_parser("console", help="启动只读本地复核台")
    console.add_argument("--out", default=_DEFAULT_OUT, help="包含台账的输出目录")
    console.add_argument(
        "--port",
        type=_port,
        default=0,
        help="127.0.0.1 监听端口；0 表示由系统分配",
    )
    summary = subparsers.add_parser("summary", help="打印已生成的 Markdown 摘要")
    summary.add_argument("--out", default=_DEFAULT_OUT, help="输出目录")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            return _verify(args)
        if args.command == "console":
            return serve_console(Path(args.out), args.port)
        if args.command == "summary":
            return _summary(Path(args.out))
    except TzGateError as exc:
        print(f"tzgate: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"tzgate: error: {exc}", file=sys.stderr)
        return 2
    return 2


def _verify(args: argparse.Namespace) -> int:
    run_id = _run_id(args.run_id)
    generated_at = _generated_at(args.generated_at)
    run, outputs = execute_verification(
        Path(args.contract),
        Path(args.root),
        Path(args.out),
        run_id,
        generated_at,
    )
    severity = run.by_severity
    print(
        "tzgate verify: "
        f"status={run.status} run_id={run.run_id} "
        f"datasets={len(run.datasets)} files={run.files_scanned} "
        f"records={run.records_scanned} issues={len(run.issues)} "
        f"(error={severity['error']}, warning={severity['warning']})"
    )
    print(
        "  outputs: "
        f"{_join(args.out, 'report.json')}, "
        f"{_join(args.out, 'summary.md')}, "
        f"{_join(args.out, 'issues.csv')}, "
        f"{_join(args.out, 'report.html')}, "
        f"{_join(args.out, 'ledger.sqlite3')}"
    )
    if run.by_code:
        top = ", ".join(f"{code}={count}" for code, count in list(run.by_code.items())[:6])
        print(f"  issue codes: {top}")
        print(
            f"  review: python -m tzgate console --out {args.out} --port 0"
        )
    threshold = _FAIL_THRESHOLD[args.fail_on]
    highest = max((SEVERITY_RANK[level] for level in run.by_severity if run.by_severity[level]), default=-1)
    if threshold != 99 and highest >= threshold:
        return 1
    return 0


def _summary(out_dir: Path) -> int:
    path = out_dir / "summary.md"
    if not path.is_file():
        raise InputError(
            f"summary.md not found in {out_dir}; run the verify command first"
        )
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


def _run_id(value: str) -> str:
    text = value.strip()
    if _RUN_ID_RE.fullmatch(text) is None:
        raise InputError(
            "run id must be 1-128 characters of letters, digits, dot, underscore, colon or dash"
        )
    return text


def _generated_at(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text or "\n" in text or "\r" in text:
        raise InputError("generated-at must be a single-line ISO-8601 timestamp")
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        moment = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InputError(f"generated-at is not ISO-8601: {exc}") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        return moment.strftime("%Y-%m-%dT%H:%M:%S")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def _join(out: str, name: str) -> str:
    return f"{out.rstrip('/')}/{name}" if out else name
