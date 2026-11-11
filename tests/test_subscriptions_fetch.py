"""Subscription download: redirects, timeout, size, and User-Agent."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from omarchy_proxy import __version__
from omarchy_proxy.subscriptions import SubscriptionError, fetch


def test_redirects_three_times_and_stops_on_the_fourth(server: _Box) -> None:
    body = fetch(server.url("/hop/3"), connect_timeout=2, total_timeout=2)
    assert body == b"ok"
    with pytest.raises(SubscriptionError) as exc:
        fetch(server.url("/hop/4"), connect_timeout=2, total_timeout=2)
    assert exc.value.code == "too_many_redirects"


def test_slow_body_times_out(server: _Box) -> None:
    with pytest.raises(SubscriptionError) as exc:
        fetch(server.url("/slow"), connect_timeout=0.2, total_timeout=0.3)
    assert exc.value.code == "fetch_timeout"


def test_body_over_the_cap_is_rejected(server: _Box) -> None:
    with pytest.raises(SubscriptionError) as exc:
        fetch(server.url("/big"), connect_timeout=2, total_timeout=2, max_bytes=8)
    assert exc.value.code == "too_large"


def test_user_agent_and_scheme(server: _Box) -> None:
    assert fetch(server.url("/echo"), connect_timeout=2, total_timeout=2) == b"ok"
    assert server.agent == f"omarchy-proxy/{__version__}"
    with pytest.raises(SubscriptionError) as exc:
        fetch("ftp://example.test/list")
    assert exc.value.code == "invalid_scheme"


class _Box:
    def __init__(self, httpd: ThreadingHTTPServer, agent: list[str]) -> None:
        self._httpd = httpd
        self._agent = agent

    def url(self, path: str) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}{path}"

    @property
    def agent(self) -> str:
        return self._agent[0]


@pytest.fixture
def server() -> _Box:
    seen: list[str] = [""]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen[0] = self.headers.get("User-Agent", "")
            if self.path.startswith("/hop/"):
                left = int(self.path.removeprefix("/hop/"))
                if left <= 0:
                    self._send(b"ok")
                    return
                self.send_response(302)
                self.send_header("Location", f"/hop/{left - 1}")
                self.end_headers()
                return
            if self.path == "/slow":
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                time.sleep(0.8)
                try:
                    self.wfile.write(b"ok")
                except OSError:
                    return
                return
            if self.path == "/big":
                self._send(b"0123456789abcdef")
                return
            self._send(b"ok")

        def _send(self, payload: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Box(httpd, seen)
    finally:
        httpd.shutdown()
        httpd.server_close()
