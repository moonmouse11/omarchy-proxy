"""Health states for the local proxy, plus one SOCKS round trip."""

from __future__ import annotations

import json
import ssl
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from omarchy_proxy.socks5 import SocksError, connect

DEFAULT_URL = "https://www.gstatic.com/generate_204"
DEFAULT_GEO_URL = "http://ip-api.com/json"
GEO_TTL = 300
_TIMEOUT_MSG = "round trip timed out"
_GEO_FIELDS = ("query", "country", "countryCode", "city", "isp")


class HealthError(Exception):
    """The round trip did not finish."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GeoCache:
    """Exit IP and country, reused for five minutes. A failed fetch is ignored."""

    def __init__(self) -> None:
        self.fetched_at: float | None = None
        self.payload: dict[str, str] | None = None

    def lookup(self, now: float, fetch: Callable[[], Any]) -> dict[str, str] | None:
        fresh = (
            self.payload is not None
            and self.fetched_at is not None
            and now - self.fetched_at < GEO_TTL
        )
        if fresh:
            return self.payload
        try:
            raw = fetch()
        except (OSError, ValueError, HealthError):
            return self.payload
        if not isinstance(raw, dict):
            return self.payload
        parsed = {key: str(raw[key]) for key in _GEO_FIELDS if key in raw}
        self.payload = parsed
        self.fetched_at = now
        return parsed


class Health:
    """One state at a time. Callers feed port and round-trip results."""

    def __init__(self) -> None:
        self.state = "unknown"
        self.http_failures = 0
        self.last_error: str | None = None
        self.started_at: float | None = None

    def mark_stopped(self) -> None:
        self.state = "stopped"

    def port(self, is_open: bool, now: float) -> None:
        if self.started_at is None:
            self.started_at = now
        if not is_open:
            self.state = "failed"
            self.last_error = "port closed"
            return
        if self.state not in {"ok", "degraded"} and now - self.started_at < 5:
            self.state = "starting"

    def round_trip_result(self, ok: bool, error: str | None = None) -> None:
        if ok:
            self.http_failures = 0
            self.last_error = None
            self.state = "ok"
            return
        self.http_failures += 1
        self.last_error = error
        if self.http_failures >= 3:
            self.state = "degraded"


def round_trip(
    port: int,
    url: str = DEFAULT_URL,
    *,
    timeout: float = 10,
    username: str | None = None,
    password: str | None = None,
) -> int:
    """HTTP status through the local SOCKS proxy. Raises ``HealthError`` on timeout."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    remote_port = parts.port or (443 if parts.scheme == "https" else 80)
    started = time.monotonic()
    try:
        sock = connect(
            "127.0.0.1",
            port,
            host,
            remote_port,
            username=username,
            password=password,
            timeout=timeout,
        )
    except (TimeoutError, SocksError) as exc:
        if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
            raise HealthError("timeout", _TIMEOUT_MSG) from exc
        raise HealthError("socks_error", exc.message) from exc
    try:
        if parts.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        remaining = timeout - (time.monotonic() - started)
        sock.settimeout(max(0.01, remaining))
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        sock.sendall(f"GET {path} HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
        reply = sock.recv(65536)
    except TimeoutError as exc:
        raise HealthError("timeout", _TIMEOUT_MSG) from exc
    except ssl.SSLError as exc:
        raise HealthError("socks_error", "tls handshake failed") from exc
    finally:
        sock.close()
    return _status_code(reply)


def _status_code(reply: bytes) -> int:
    line = reply.split(b"\r\n", 1)[0].decode(errors="replace")
    parts = line.split()
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise HealthError("empty_reply", "proxy returned no HTTP status")
    return int(parts[1])


def fetch_json(
    port: int,
    url: str = DEFAULT_GEO_URL,
    *,
    timeout: float = 10,
) -> Any:
    """GET url through the local proxy and parse the body as JSON."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    remote_port = parts.port or (443 if parts.scheme == "https" else 80)
    started = time.monotonic()
    try:
        sock = connect("127.0.0.1", port, host, remote_port, timeout=timeout)
    except (TimeoutError, SocksError) as exc:
        if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
            raise HealthError("timeout", _TIMEOUT_MSG) from exc
        raise HealthError("socks_error", exc.message) from exc
    try:
        if parts.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        remaining = timeout - (time.monotonic() - started)
        sock.settimeout(max(0.01, remaining))
        path = parts.path or "/"
        sock.sendall(f"GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        chunks: list[bytes] = []
        while True:
            block = sock.recv(65536)
            if not block:
                break
            chunks.append(block)
    except TimeoutError as exc:
        raise HealthError("timeout", _TIMEOUT_MSG) from exc
    finally:
        sock.close()
    raw = b"".join(chunks)
    head, _, body = raw.partition(b"\r\n\r\n")
    if _status_code(head) >= 400:
        raise HealthError("http_status", "geolocation request failed")
    return json.loads(body.decode(encoding="utf-8"))
