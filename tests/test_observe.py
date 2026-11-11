"""Exit IP cache, ping cap, status budget, and doctor."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from omarchy_proxy.catalog import Catalog
from omarchy_proxy.doctor import doctor_report, status_report, write_geo
from omarchy_proxy.health import GeoCache, HealthError
from omarchy_proxy.ping import is_fresh, ping_many
from omarchy_proxy.supervisor import Supervisor


def test_geo_cache_skips_a_fresh_fetch_and_ignores_failure() -> None:
    cache = GeoCache()
    calls = {"n": 0}

    def fetch() -> dict[str, str]:
        calls["n"] += 1
        return {
            "query": "1.2.3.4",
            "country": "DE",
            "countryCode": "DE",
            "city": "Berlin",
            "isp": "x",
        }

    first = cache.lookup(0, fetch)
    assert calls["n"] == 1
    assert first is not None
    assert first["query"] == "1.2.3.4"
    assert cache.lookup(299, fetch) == first
    assert calls["n"] == 1

    def boom() -> dict[str, str]:
        raise TimeoutError("down")

    assert cache.lookup(300, boom) == first
    assert GeoCache().lookup(0, boom) is None


def test_ping_cache_uses_a_number(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    row = catalog.add("socks5://10.0.0.1:1080")
    catalog.set_ping(str(row["id"]), 12, 1000.0)
    assert is_fresh(catalog.get(str(row["id"])), 1030)
    catalog.set_ping(str(row["id"]), 12, "1000")
    assert is_fresh(catalog.get(str(row["id"])), 1030) is False


def test_successful_check_stores_exit_ip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = Supervisor(tmp_path)
    monkeypatch.setattr(
        "omarchy_proxy.supervisor.fetch_json",
        lambda *_args, **_kwargs: {
            "query": "9.9.9.9",
            "country": "DE",
            "countryCode": "DE",
            "city": "Berlin",
            "isp": "x",
        },
    )
    supervisor._record_geo(1, 0)
    stored = json.loads((tmp_path / "geo.json").read_text(encoding="utf-8"))
    assert stored["query"] == "9.9.9.9"

    def fail(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise HealthError("timeout", "down")

    monkeypatch.setattr("omarchy_proxy.supervisor.fetch_json", fail)
    supervisor._record_geo(1, 300)
    assert json.loads((tmp_path / "geo.json").read_text(encoding="utf-8"))["query"] == "9.9.9.9"


def test_ping_stays_at_eight_in_flight() -> None:
    rows = [{"id": str(i), "host": "127.0.0.1", "port": 1} for i in range(20)]

    def slow(_host: str, _port: int) -> int:
        time.sleep(0.05)
        return 1

    results, peak = ping_many(rows, slow)
    assert len(results) == 20
    assert peak == 8


def test_status_reads_cache_without_sleeping(tmp_path: Path) -> None:
    (tmp_path / "current.json").write_text(
        json.dumps({"state": "running", "health": "ok"}),
        encoding="utf-8",
    )
    write_geo(tmp_path, {"query": "1.2.3.4", "country": "DE"})
    started = time.perf_counter()
    cold = status_report(tmp_path)
    cold_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    warm = status_report(tmp_path)
    warm_ms = (time.perf_counter() - started) * 1000
    assert cold["exit_ip"] == "1.2.3.4"
    (tmp_path / "current.json").write_text(
        json.dumps({"state": "running", "health": "degraded", "health_error": "timeout"}),
        encoding="utf-8",
    )
    assert status_report(tmp_path)["health_error"] == "timeout"
    assert cold["health"] == "ok"
    assert warm == cold
    assert cold_ms < 500
    assert warm_ms < 100


def test_doctor_names_a_missing_backend_and_redacts_the_log(tmp_path: Path) -> None:
    log = tmp_path / "core.log"
    log.write_text("line\n" * 60 + "password=s3cret\n", encoding="utf-8")
    report = doctor_report(
        tmp_path,
        tmp_path / "servers.json",
        lookup=lambda _name: None,
        secrets=["s3cret"],
    )
    assert report["issues"][0]["code"] == "no_backend"
    assert "pacman -S sing-box" in report["issues"][0]["hint"]
    assert len(report["log"]) == 50
    assert "s3cret" not in "".join(report["log"])
    assert "paths" in report
    assert "ports" in report

    found = doctor_report(tmp_path, lookup=lambda name: f"/usr/bin/{name}")
    assert [item["name"] for item in found["backends"]] == ["xray", "sing-box"]
    assert found["issues"] == []


def test_cli_status_and_doctor(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["XDG_STATE_HOME"] = str(tmp_path)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    status = subprocess.run(
        [sys.executable, "-m", "omarchy_proxy", "--status"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["health"] == "unknown"
    doctor = subprocess.run(
        [sys.executable, "-m", "omarchy_proxy", "--doctor"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    body = json.loads(doctor.stdout)
    if doctor.returncode == 1:
        assert body["error"]["code"] == "no_backend"
    else:
        assert body["ok"] is True
        assert body["backends"]


def test_measure_against_a_local_listener() -> None:
    from omarchy_proxy.ping import measure

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        ms = measure("127.0.0.1", port, timeout=1)
        assert ms is not None
        assert ms < 1000
    finally:
        sock.close()
