"""CLI connect, import, and settings."""

from __future__ import annotations

import shutil
import socket
from pathlib import Path
from typing import Any, ClassVar

import pytest

from omarchy_proxy.backends.base import BackendSettings
from omarchy_proxy.cli import main
from omarchy_proxy.model import Server
from omarchy_proxy.settings import Settings, SettingsError
from omarchy_proxy.supervisor import state_dir


class Stub:
    name = "stub"
    binary_candidates = ("sleep",)
    command: ClassVar[list[str]] = [shutil.which("sleep") or "sleep", "30"]

    def generate_config(self, server: Server, settings: BackendSettings) -> dict[str, Any]:
        return {"server": server.host}

    def validate_config(self, path: Path) -> None:
        return None

    def version(self) -> str:
        return "0"


def test_settings_reject_unknown_and_invalid_values(tmp_path: Path) -> None:
    store = Settings(tmp_path / "settings.json")
    assert store.get("mode") == "manual"
    assert store.set("mode", "system")["value"] == "system"
    assert Settings(tmp_path / "settings.json").get("mode") == "system"
    with pytest.raises(SettingsError) as unknown:
        store.set("nope", "1")
    assert unknown.value.code == "unknown_setting"
    with pytest.raises(SettingsError) as bad:
        store.set("socks_port", "70000")
    assert bad.value.code == "invalid_field"


def test_import_file_uri_and_subscription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(tmp_path, monkeypatch)
    uri_file = tmp_path / "uris.txt"
    uri_file.write_text("# c\nsocks5://10.0.0.1:1080\nsocks5://10.0.0.2:1080\n", encoding="utf-8")
    assert main(["--import", str(uri_file)]) == 0
    assert main(["--import", "socks5://10.0.0.3:1080"]) == 0
    body = b"socks5://10.1.0.1:1080\n"
    httpd = _http(body)
    host, port = httpd.server_address[:2]
    try:
        assert main(["--import", f"http://{host}:{port}/list"]) == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert main(["--servers"]) == 0


def test_connect_progress_unknown_busy_and_disconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(tmp_path, monkeypatch)
    _patch_core(monkeypatch)
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "omarchy_proxy.output.emit",
        lambda payload, stream=None: written.append(payload),
    )
    Settings().set("socks_port", "19380")
    Settings().set("http_port", "19381")
    assert main(["--import", "socks5://10.0.0.9:1080"]) == 0
    written.clear()
    missing = main(["--connect", "missing"])
    assert missing == 1
    assert written[-1]["error"]["code"] == "not_found"

    holder = socket.socket()
    holder.bind(("127.0.0.1", 19382))
    Settings().set("socks_port", "19382")
    written.clear()
    busy = main(["--connect", _only_id()])
    assert busy == 1
    assert written[-1]["error"]["code"] == "port_in_use"
    holder.close()

    Settings().set("socks_port", "19380")
    written.clear()
    assert main(["--connect", _only_id()]) == 0
    assert "healthy" in [line.get("progress") for line in written]
    written.clear()
    again = main(["--connect", _only_id()])
    assert again == 1
    assert written[-1]["error"]["code"] == "already_running"
    assert main(["--disconnect"]) == 0
    assert not (state_dir() / "current.json").exists()


def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _patch_core(monkeypatch: pytest.MonkeyPatch) -> None:
    def choose(*_args: object, **_kwargs: object) -> tuple[Stub, str]:
        return Stub(), "stub"

    monkeypatch.setattr("omarchy_proxy.manager.Selector.choose", choose)
    monkeypatch.setattr("omarchy_proxy.supervisor._tcp_open", lambda _port: True)
    monkeypatch.setattr("omarchy_proxy.supervisor.round_trip", lambda *_args, **_kwargs: 204)
    monkeypatch.setattr(
        "omarchy_proxy.supervisor.fetch_json",
        lambda *_args, **_kwargs: {
            "query": "1.1.1.1",
            "country": "DE",
            "countryCode": "DE",
            "city": "Berlin",
            "isp": "x",
        },
    )


def _only_id() -> str:
    from omarchy_proxy.catalog import Catalog

    return str(Catalog().list()[0]["id"])


def _http(body: bytes):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
