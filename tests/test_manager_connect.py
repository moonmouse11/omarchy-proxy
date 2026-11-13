"""Happy-path connect with a stub core."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, ClassVar

import pytest

from omarchy_proxy.backends.base import BackendSettings
from omarchy_proxy.catalog import Catalog
from omarchy_proxy.manager import Manager, ManagerError
from omarchy_proxy.model import Server
from omarchy_proxy.supervisor import Supervisor
from omarchy_proxy.sysproxy import Exposure


class Stub:
    name = "stub"
    binary_candidates = ("sleep",)
    command: ClassVar[list[str]] = [shutil.which("sleep") or "sleep", "30"]

    def __init__(self) -> None:
        self.validated = False

    def generate_config(self, server: Server, settings: BackendSettings) -> dict[str, Any]:
        return {"server": server.host, "socks": settings["socks_port"]}

    def validate_config(self, path: Path) -> None:
        assert json.loads(path.read_text(encoding="utf-8"))["server"] == "10.0.0.1"
        self.validated = True

    def version(self) -> str:
        return "0"


def test_connect_reaches_healthy_and_marks_last_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    row = catalog.add("socks5://10.0.0.1:1080#Mine")
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
    state = tmp_path / "state"
    backend = Stub()
    manager = Manager(state, tmp_path / "config", supervisor=Supervisor(state))
    try:
        result = manager.connect(
            catalog,
            str(row["id"]),
            backend=backend,
            socks_port=19080,
            http_port=19081,
            health_url="http://example.test/generate_204",
            timeout=2,
        )
        assert result["health"] == "ok"
        assert result["backend"] == "stub"
        assert result["system_mode"] == "none"
        assert backend.validated is True
        assert catalog.get(str(row["id"]))["last_used_at"]
        current = json.loads((state / "current.json").read_text(encoding="utf-8"))
        assert current["server_id"] == row["id"]
        assert current["backend"] == "stub"
        assert (state / "config.json").stat().st_mode & 0o777 == 0o600
        stopped = manager.disconnect()
        assert stopped["state"] == "stopped"
        assert not (state / "current.json").exists()
        assert not (state / "config.json").exists()
        again = manager.disconnect()
        assert again["state"] == "stopped"
    finally:
        manager.supervisor.stop()


def test_failed_health_reverts_exposure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    row = catalog.add("socks5://10.0.0.1:1080")
    monkeypatch.setattr("omarchy_proxy.supervisor._tcp_open", lambda _port: True)
    monkeypatch.setattr("omarchy_proxy.supervisor.round_trip", lambda *_args, **_kwargs: 500)
    state = tmp_path / "state"
    config = tmp_path / "config"

    def run(_args: list[str]) -> str:
        raise OSError("missing")

    manager = Manager(
        state,
        config,
        exposure=Exposure(state, config, run),
        supervisor=Supervisor(state),
    )
    with pytest.raises(ManagerError) as exc:
        manager.connect(
            catalog,
            str(row["id"]),
            backend=Stub(),
            mode="system",
            socks_port=19082,
            http_port=19083,
            health_url="http://example.test/generate_204",
            timeout=0.1,
        )
    assert exc.value.code == "health_timeout"
    assert not (config / "environment.d" / "omarchy-proxy.conf").exists()
    assert not (state / "current.json").exists()


class XrayStub(Stub):
    name = "xray"


def test_xray_rejects_equal_ports(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    row = catalog.add("socks5://10.0.0.1:1080")
    state = tmp_path / "state"
    manager = Manager(state, tmp_path / "config", supervisor=Supervisor(state))
    with pytest.raises(ManagerError) as exc:
        manager.connect(
            catalog,
            str(row["id"]),
            backend=XrayStub(),
            socks_port=19100,
            http_port=19100,
        )
    assert exc.value.code == "invalid_field"
    assert not (state / "config.json").exists()


def test_connect_records_secrets_and_health_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    row = catalog.add("socks5://user123:s3cret-pw@10.0.0.1:1080#Mine")
    monkeypatch.setattr("omarchy_proxy.supervisor._tcp_open", lambda _port: True)
    monkeypatch.setattr("omarchy_proxy.supervisor.round_trip", lambda *_args, **_kwargs: 204)
    monkeypatch.setattr("omarchy_proxy.supervisor.fetch_json", lambda *_args, **_kwargs: {})
    state = tmp_path / "state"
    manager = Manager(state, tmp_path / "config", supervisor=Supervisor(state))
    try:
        manager.connect(
            catalog,
            str(row["id"]),
            backend=Stub(),
            socks_port=19110,
            http_port=19111,
            health_url="http://example.test/x",
            timeout=2,
        )
        assert "s3cret-pw" in manager.supervisor.secrets
        current = json.loads((state / "current.json").read_text(encoding="utf-8"))
        assert current["health_url"] == "http://example.test/x"
    finally:
        manager.supervisor.stop()
