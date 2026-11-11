"""Load, broken state, and a core that dies during a health check."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import time
from pathlib import Path

from omarchy_proxy.catalog import Catalog
from omarchy_proxy.cli import main
from omarchy_proxy.doctor import status_report
from omarchy_proxy.supervisor import Supervisor


def test_thousand_servers_stay_inside_the_status_budget(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config"
    state = tmp_path / "state"
    config.mkdir()
    state.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    rows = [
        {
            "id": f"s{i:04d}",
            "name": f"n{i}",
            "uri": f"socks5://h{i}:1",
            "favorite": False,
            "source": "manual",
            "added_at": "2020-01-01T00:00:00+00:00",
            "last_used_at": None,
        }
        for i in range(1000)
    ]
    catalog = config / "omarchy-proxy" / "servers.json"
    catalog.parent.mkdir()
    catalog.write_text(
        json.dumps({"version": 1, "servers": rows, "tombstones": {}, "subscriptions": []}),
        encoding="utf-8",
    )
    os.chmod(catalog, 0o600)
    started = time.perf_counter()
    assert len(Catalog().list()) == 1000
    assert (time.perf_counter() - started) * 1000 < 100
    started = time.perf_counter()
    assert main(["--servers"]) == 0
    assert (time.perf_counter() - started) * 1000 < 100
    started = time.perf_counter()
    cold = status_report(state, catalog)
    cold_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    warm = status_report(state, catalog)
    warm_ms = (time.perf_counter() - started) * 1000
    assert cold["subscriptions"] == []
    assert warm == cold
    assert cold_ms < 500
    assert warm_ms < 100


def test_corrupt_state_does_not_crash_the_supervisor(tmp_path: Path) -> None:
    (tmp_path / "current.json").write_text("{", encoding="utf-8")
    report = Supervisor(tmp_path).tick(0, 0)
    assert report["state"] == "stopped"
    assert status_report(tmp_path)["state"] == "stopped"


def test_killing_the_core_during_a_health_check_is_a_restart(tmp_path: Path, monkeypatch) -> None:
    sleep = os.posix_spawn("/bin/sleep", ["/bin/sleep", "30"], os.environ, setsid=True)
    supervisor = Supervisor(tmp_path)
    (tmp_path / "current.json").write_text(
        json.dumps(
            {"state": "running", "pid": sleep, "argv": ["/bin/sleep", "30"], "health": "ok"}
        ),
        encoding="utf-8",
    )

    def kill_and_fail(_port: int, _url: str, timeout: float = 10) -> int:
        os.kill(sleep, 9)
        raise OSError("closed")

    monkeypatch.setattr("omarchy_proxy.supervisor.round_trip", kill_and_fail)
    try:
        first = supervisor.tick(100, 1, "http://example.com/")
        assert first["health_error"] == "socks_error"
        second = supervisor.tick(101, 1, "http://example.com/")
        assert second["state"] == "starting"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(sleep, 9)


def test_a_port_taken_after_the_check_ends_as_a_restart(tmp_path: Path, monkeypatch) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setattr("omarchy_proxy.supervisor._ensure_free", lambda _port: None)
    holder = socket.socket()
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    supervisor = Supervisor(tmp_path)
    code = "import socket,sys; socket.socket().bind(('127.0.0.1', int(sys.argv[1])))"
    try:
        supervisor.start([sys.executable, "-c", code, str(port)], ports=[port])
        result: dict[str, object] = {"state": "running"}
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and result.get("state") != "starting":
            result = supervisor.tick(time.monotonic(), 0)
            time.sleep(0.02)
        assert result["state"] == "starting"
    finally:
        holder.close()
