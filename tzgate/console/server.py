"""Loopback-only HTTP server for the read-only review console."""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from ..errors import ConsoleError, LedgerError
from .data import Ledger
from .pages import render_error, render_index, render_issue, render_not_found

HOST = "127.0.0.1"
_ALLOWED_FILTERS = ("dataset", "code", "severity")
_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; "
    "form-action 'self'; base-uri 'none'"
)


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, ledger: Ledger):
        self.ledger = ledger
        super().__init__(address, handler)


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "tzgate-console/0.1.0"
    sys_version = ""

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        filters = _filters(parsed.query)
        try:
            if path == "/healthz":
                self._send(200, b"ok\n", "text/plain; charset=utf-8")
            elif path == "/":
                body = render_index(self.server.ledger, filters)
                self._send_html(200, body)
            elif path.startswith("/issue/"):
                issue_id = unquote(path[len("/issue/") :])
                body = render_issue(self.server.ledger, issue_id)
                if body is None:
                    self._send_html(404, render_not_found("没有找到该问题编号。"))
                else:
                    self._send_html(200, body)
            elif path == "/api/issues":
                self._send_json(filters)
            elif path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            else:
                self._send_html(404, render_not_found("请求的页面不存在。"))
        except LedgerError as exc:
            self._send_html(500, render_error(str(exc)))
        except sqlite3.Error as exc:
            self._send_html(500, render_error(f"台账读取失败：{exc}"))

    def do_POST(self) -> None:
        self._send(405, b"method not allowed\n", "text/plain; charset=utf-8")

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, filters: dict[str, str]) -> None:
        issues = self.server.ledger.issues(
            dataset=filters.get("dataset") or None,
            code=filters.get("code") or None,
            severity=filters.get("severity") or None,
        )
        for issue in issues:
            issue["check"] = issue.pop("check_name")
        payload = {
            "run": self.server.ledger.run(),
            "filters": filters,
            "count": len(issues),
            "issues": issues,
        }
        body = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        self._send(200, body + b"\n", "application/json; charset=utf-8")

    def _send_html(self, status: int, body: str) -> None:
        self._send(status, body.encode("utf-8"), "text/html; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CSP)
        if status == 405:
            self.send_header("Allow", "GET")
        self.end_headers()
        if body:
            self.wfile.write(body)


def make_server(out_dir: Path, port: int = 0) -> ConsoleServer:
    _validate_port(port)
    ledger = Ledger(Path(out_dir) / "ledger.sqlite3")
    try:
        server = ConsoleServer((HOST, port), ConsoleHandler, ledger)
    except OSError as exc:
        ledger.close()
        raise ConsoleError(f"cannot bind {HOST}:{port}: {exc}") from exc
    return server


def serve_console(out_dir: Path, port: int = 0) -> int:
    server = make_server(out_dir, port)
    address = f"http://{HOST}:{server.server_port}/"
    print(f"tzgate console: {address} (read-only, Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("tzgate console: stopped", flush=True)
    finally:
        server.server_close()
        server.ledger.close()
    return 0


def _validate_port(port: int) -> None:
    if isinstance(port, bool) or not isinstance(port, int):
        raise ConsoleError("port must be an integer")
    if not 0 <= port <= 65535:
        raise ConsoleError("port must be between 0 and 65535")


def _filters(query: str) -> dict[str, str]:
    parsed = parse_qs(query, keep_blank_values=False)
    filters: dict[str, str] = {}
    for name in _ALLOWED_FILTERS:
        values = parsed.get(name)
        if values:
            filters[name] = values[0][:200]
    return filters
