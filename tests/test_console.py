from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tzgate.console.server import make_server
from tzgate.errors import ConsoleError


def _request(base: str, path: str, method: str = "GET"):
    request = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


@pytest.fixture()
def console(outputs):
    server = make_server(outputs.out_dir, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield server, base
    finally:
        server.shutdown()
        server.server_close()
        server.ledger.close()
        thread.join(timeout=5)


def test_console_is_loopback_only(console):
    server, _base = console
    assert server.server_address[0] == "127.0.0.1"
    assert server.server_port > 0


def test_console_pages_and_api(console):
    _server, base = console
    status, headers, body = _request(base, "/healthz")
    assert (status, body) == (200, b"ok\n")
    assert "style-src" in headers["Content-Security-Policy"]

    status, _, body = _request(base, "/")
    text = body.decode("utf-8")
    assert status == 200
    assert "test-run" in text
    assert "TZ-DST-GAP" in text
    assert "127.0.0.1" in text

    status, _, body = _request(base, "/api/issues")
    payload = json.loads(body)
    assert status == 200
    assert payload["count"] == 13
    assert payload["issues"][0]["check"] in {"dst", "partition", "parse", "offset"}

    status, _, body = _request(base, "/api/issues?code=TZ-DST-GAP")
    assert status == 200
    assert json.loads(body)["count"] == 2

    first = payload["issues"][0]
    status, _, body = _request(base, f"/issue/{first['issue_id']}")
    detail = body.decode("utf-8")
    assert status == 200
    assert first["code"] in detail
    assert "结构化证据" in detail
    assert first["raw"] in detail


def test_console_404_405_and_traversal(console):
    _server, base = console
    assert _request(base, "/missing")[0] == 404
    assert _request(base, "/issue/not-an-issue")[0] == 404
    assert _request(base, "/../report.json")[0] == 404
    status, headers, _body = _request(base, "/", method="POST")
    assert status == 405
    assert headers["Allow"] == "GET"


def test_ledger_rejects_writes(outputs):
    uri = outputs.ledger.as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE nope (value TEXT)")
    finally:
        connection.close()


@pytest.mark.parametrize("port", [-1, 65536, True])
def test_invalid_ports(outputs, port):
    with pytest.raises(ConsoleError):
        make_server(outputs.out_dir, port)
