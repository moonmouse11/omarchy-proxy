"""Subscription documents, reconciliation, tombstones, and refresh."""

from __future__ import annotations

import base64
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from omarchy_proxy.catalog import Catalog
from omarchy_proxy.cli import main
from omarchy_proxy.doctor import status_report
from omarchy_proxy.settings import Settings
from omarchy_proxy.subscriptions import (
    SubscriptionError,
    parse_document,
    queue_stale,
    refresh,
)

URI = "socks5://10.0.0.1:1080"
OTHER = "socks5://10.0.0.2:1080"
NOW = datetime(2026, 1, 2, tzinfo=UTC)


def test_document_formats() -> None:
    plain = b"# comment\n// also\n\nsocks5://10.0.0.1:1080\n"
    assert parse_document(plain) == [URI]
    encoded = base64.b64encode(f"{URI}\n{OTHER}\n".encode())
    assert parse_document(encoded) == [URI, OTHER]
    assert parse_document(json.dumps([URI, OTHER]).encode()) == [URI, OTHER]
    with pytest.raises(SubscriptionError) as exc:
        parse_document(b"this is not a list")
    assert exc.value.code == "invalid_document"


def test_manual_row_is_shared_then_downgraded(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    added = catalog.add(URI + "#Mine")
    catalog.toggle_favorite(str(added["id"]))
    url = "https://example.test/sub"
    refresh(catalog, url, fetcher=lambda _url: f"{URI}\n".encode(), now=NOW)
    shared = catalog.get(str(added["id"]))
    assert shared["source"] == f"manual+subscription:{url}"
    assert shared["favorite"] is True
    assert shared["name"] == "Mine"
    assert shared["added_at"] == added["added_at"]
    refresh(catalog, url, fetcher=lambda _url: b"", now=NOW)
    assert catalog.get(str(added["id"]))["source"] == "manual"


def test_pure_subscription_row_is_removed_when_it_vanishes(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    url = "https://example.test/sub"
    refresh(catalog, url, fetcher=lambda _url: f"{URI}\n{OTHER}\n".encode(), now=NOW)
    assert len(catalog.list()) == 2
    refresh(catalog, url, fetcher=lambda _url: f"{URI}\n".encode(), now=NOW)
    assert len(catalog.list()) == 1


def test_overlapping_subscriptions_keep_the_server(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    body = f"{URI}\n".encode()
    refresh(catalog, "https://example.test/a", fetcher=lambda _url: body, now=NOW)
    refresh(catalog, "https://example.test/b", fetcher=lambda _url: body, now=NOW)
    refresh(catalog, "https://example.test/a", fetcher=lambda _url: b"", now=NOW)
    row = catalog.list()[0]
    assert row["source"] == "subscription:https://example.test/b"
    assert row["subscriptions"] == ["https://example.test/b"]


def test_tombstone_blocks_until_cleared(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    url = "https://example.test/sub"
    body = f"{URI}\n".encode()
    refresh(catalog, url, fetcher=lambda _url: body, now=NOW)
    identity = catalog.list()[0]["id"]
    catalog.remove(str(identity), confirm_subscription=True)
    refresh(catalog, url, fetcher=lambda _url: body, now=NOW)
    assert catalog.list() == []
    refresh(catalog, url, fetcher=lambda _url: body, now=NOW, clear_tombstones=True)
    assert len(catalog.list()) == 1


def test_failed_fetch_keeps_servers_and_records_the_error(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    url = "https://example.test/sub"
    refresh(catalog, url, fetcher=lambda _url: f"{URI}\n".encode(), now=NOW)

    def fail(_url: str) -> bytes:
        raise SubscriptionError("fetch_failed", "HTTP 500")

    with pytest.raises(SubscriptionError):
        refresh(catalog, url, fetcher=fail, now=NOW)
    assert len(catalog.list()) == 1
    meta = catalog.subscriptions()[0]
    assert meta["last_error"] == "HTTP 500"
    assert meta["last_fetch_at"] == "2026-01-02T00:00:00Z"
    assert meta["auto_refresh"] is False
    report = status_report(tmp_path, tmp_path / "servers.json")
    assert report["subscriptions"][0]["last_error"] == "HTTP 500"


def test_stale_refresh_is_queued_without_waiting(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    url = "https://example.test/sub"
    catalog.ensure_subscription(url)
    catalog.set_auto_refresh(url, True)
    started = threading.Event()
    release = threading.Event()

    def refresh_one(_url: str) -> None:
        started.set()
        release.wait(1)

    queued = queue_stale(catalog, refresh_one, now=NOW, interval=60)
    assert queued == [url]
    assert started.wait(1)
    release.set()


def test_supervisor_queues_stale_subscriptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    Catalog().set_auto_refresh("https://example.test/a", True)
    Settings().set("refresh_hours", "1")

    def fake_queue(catalog: Catalog, _refresh: object, interval: float = 0) -> list[str]:
        assert catalog.subscriptions()[0]["auto_refresh"] is True
        assert interval == 3600
        return ["https://example.test/a"]

    monkeypatch.setattr("omarchy_proxy.subscriptions.queue_stale", fake_queue)
    from omarchy_proxy.supervisor import queue_saved_subscriptions

    assert queue_saved_subscriptions() == ["https://example.test/a"]


def test_cli_subscribe_and_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = f"{URI}\n".encode()
    httpd = _server(body)
    host, port = httpd.server_address[:2]
    url = f"http://{host}:{port}/list"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    try:
        assert main(["--subscribe", url]) == 0
        assert Catalog().subscriptions()[0]["auto_refresh"] is False
        assert main(["--auto-refresh", url]) == 0
        assert Catalog().subscriptions()[0]["auto_refresh"] is True
        assert main(["--refresh", url]) == 0
        assert Catalog().list()[0]["source"] == f"subscription:{url}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _server(body: bytes) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
