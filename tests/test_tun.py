"""Tun mode: sing-box only, helper rules, and the route refusal."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, ClassVar

import pytest

from omarchy_proxy.backends.base import BackendError
from omarchy_proxy.backends.detect import Selector
from omarchy_proxy.backends.singbox import SingBoxBackend
from omarchy_proxy.catalog import Catalog
from omarchy_proxy.doctor import doctor_report
from omarchy_proxy.manager import Manager, ManagerError, _argv
from omarchy_proxy.model import Socks5Server, VlessServer
from omarchy_proxy.settings import Settings, SettingsError
from omarchy_proxy.tun_helper import (
    config_allowed,
    down,
    foreign_device,
    ruleset,
    up,
)

UUID = "01234567-89ab-cdef-0123-456789abcdef"


def test_settings_accept_tun_and_reject_unknown(tmp_path: Path) -> None:
    store = Settings(tmp_path / "settings.json")
    assert store.get("mode") == "manual"
    assert store.set("mode", "tun")["value"] == "tun"
    assert Settings(tmp_path / "settings.json").get("mode") == "tun"
    with pytest.raises(SettingsError) as exc:
        store.set("mode", "vpn")
    assert exc.value.code == "invalid_field"


def test_tun_forces_singbox_even_when_xray_is_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omarchy_proxy.backends.detect.shutil.which",
        lambda name: "/bin/xray" if name == "xray" else "/bin/sing-box",
    )
    server = VlessServer(host="v.example", port=443, name="v", uuid=UUID)
    backend, reason = Selector().choose(server, "xray", "tun")
    assert backend.name == "sing-box"
    assert reason == "tun_requires_singbox"


def test_tun_without_singbox_is_no_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omarchy_proxy.backends.detect.shutil.which",
        lambda name: "/bin/xray" if name == "xray" else None,
    )
    server = Socks5Server(host="10.0.0.1", port=1080, name="s")
    with pytest.raises(BackendError) as exc:
        Selector().choose(server, "xray", "tun")
    assert exc.value.code == "no_backend"
    assert "pacman -S sing-box" in exc.value.message


def test_tun_config_keeps_local_proxies_and_hijacks_dns() -> None:
    server = Socks5Server(host="10.1.2.3", port=1080, name="s")
    config = SingBoxBackend().generate_config(
        server,
        {
            "socks_port": 10808,
            "http_port": 10809,
            "health_url": "http://example.test/x",
            "tun": True,
        },
    )
    tun = config["inbounds"][0]
    assert tun["type"] == "tun"
    assert tun["strict_route"] is True
    assert tun["auto_route"] is True
    assert tun["auto_redirect"] is True
    assert "172.19.0.1/30" in tun["address"]
    assert "fdfe:dcba:9876::1/126" in tun["address"]
    kinds = {item["type"] for item in config["inbounds"]}
    assert {"tun", "socks", "http"} <= kinds
    assert {"protocol": "dns", "action": "hijack-dns"} in config["route"]["rules"]
    assert all(item.get("type") != "dns" for item in config["outbounds"])
    assert all(item.get("outbound") != "direct" for item in config["route"]["rules"])
    assert config["dns"]["rules"] == [{"port": 53, "server": "remote"}]
    binary = __import__("shutil").which("sing-box")
    if binary:
        path = Path("/tmp/omarchy-proxy-tun-check.json")
        path.write_text(json.dumps(config), encoding="utf-8")
        result = subprocess.run(
            [binary, "check", "-c", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_helper_rejects_a_foreign_config_and_keeps_the_table_after_exec(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}", encoding="utf-8")
    assert config_allowed(outside, state) is False
    config = state / "config.json"
    config.write_text(
        json.dumps({"outbounds": [{"tag": "proxy", "server": "10.9.8.7"}]}),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def run(argv: list[str], input: str | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert input is not None
        assert "policy drop" in input
        assert "10.9.8.7" in input
        return subprocess.CompletedProcess(argv, 0, "", "")

    replaced: list[list[str]] = []
    up(config, state, run=run, replace=lambda _binary, argv: replaced.append(argv))
    assert calls == [["nft", "-f", "-"]]
    assert replaced
    assert replaced[0][1:3] == ["run", "-c"]
    down_calls: list[list[str]] = []

    def record(argv: list[str], input: str | None = None) -> subprocess.CompletedProcess[str]:
        down_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    down(run=record)
    assert ["nft", "delete", "table", "inet", "omarchy-proxy"] in down_calls
    assert ["ip", "link", "delete", "omarchy-tun"] in down_calls


def test_ruleset_allows_loopback_dhcp_and_the_server() -> None:
    text = ruleset(["203.0.113.5", "2001:db8::1"])
    assert "policy drop" in text
    assert 'oifname "lo" accept' in text
    assert "udp dport { 67, 68, 546, 547 } accept" in text
    assert "ip daddr 203.0.113.5 accept" in text
    assert "ip6 daddr 2001:db8::1 accept" in text


def test_foreign_route_refuses_connect_before_the_core_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert foreign_device(json.dumps([{"dst": "default", "dev": "wlan0"}])) is None
    assert foreign_device(json.dumps([{"dst": "default", "dev": "tun0"}])) == "tun0"
    assert foreign_device(json.dumps([{"dst": "default", "dev": "wg0"}])) == "wg0"
    monkeypatch.setattr(
        "omarchy_proxy.backends.detect.shutil.which",
        lambda name: "/bin/sing-box" if name == "sing-box" else None,
    )
    catalog = Catalog(tmp_path / "servers.json")
    row = catalog.add("socks5://10.0.0.1:1080#Mine")
    state = tmp_path / "state"
    state.mkdir()
    (state / "tun-acknowledged").write_text("acknowledged\n", encoding="utf-8")
    started: list[list[str]] = []

    class Quiet:
        def start(self, argv: list[str], _ports: list[int]) -> None:
            started.append(argv)

        def stop(self) -> dict[str, Any]:
            return {"state": "stopped"}

        def _read(self) -> None:
            return None

        def _write(self, _current: dict[str, Any]) -> None:
            return None

    manager = Manager(state, tmp_path / "config", supervisor=Quiet())  # type: ignore[arg-type]
    manager.route_lookup = lambda: json.dumps([{"dst": "default", "dev": "wg0"}])
    with pytest.raises(ManagerError) as exc:
        manager.connect(catalog, str(row["id"]), mode="tun", preference="xray")
    assert exc.value.code == "route_owned"
    assert started == []


def test_core_argv_uses_the_helper_only_for_tun(tmp_path: Path) -> None:
    class Core:
        name = "sing-box"
        binary_candidates = ("sing-box",)
        command: ClassVar[list[str]] = ["/bin/sing-box", "run", "-c", "x"]

    config = tmp_path / "config.json"
    tun_argv = _argv(Core(), config, "tun")  # type: ignore[arg-type]
    assert tun_argv[:4] == ["sudo", "-n", "--preserve-env=XDG_STATE_HOME", "/usr/local/bin/tun-helper"]
    assert tun_argv[4:] == ["up", str(config)]
    manual = _argv(Core(), config, "manual")  # type: ignore[arg-type]
    assert manual == ["/bin/sing-box", "run", "-c", "x"]


def test_tun_connect_does_not_call_gsettings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    row = catalog.add("socks5://10.0.0.1:1080#Mine")
    state = tmp_path / "state"
    state.mkdir()
    (state / "tun-acknowledged").write_text("acknowledged\n", encoding="utf-8")
    monkeypatch.setattr("omarchy_proxy.backends.detect.shutil.which", lambda name: "/bin/sing-box")

    class Backend:
        name = "sing-box"
        binary_candidates = ("sing-box",)

        def generate_config(self, server: Socks5Server, settings: dict[str, Any]) -> dict[str, Any]:
            return {"server": server.host, "tun": settings.get("tun")}

        def validate_config(self, path: Path) -> None:
            assert json.loads(path.read_text(encoding="utf-8"))["tun"] is True

        def version(self) -> str:
            return "1.10.0"

    class Quiet:
        def start(self, argv: list[str], _ports: list[int]) -> None:
            assert argv[0] == "sudo"
            assert argv[4:] == ["up", str(state / "config.json")]

        def tick(self, *_args: object, **_kwargs: object) -> dict[str, str]:
            return {"health": "ok"}

        def _read(self) -> dict[str, Any]:
            return {}

        def _write(self, current: dict[str, Any]) -> None:
            (state / "current.json").write_text(json.dumps(current), encoding="utf-8")

        def stop(self) -> dict[str, Any]:
            return {"state": "stopped"}

    called: list[str] = []

    class Exposure:
        def connect(self, mode: str, **_kwargs: object) -> dict[str, str]:
            called.append(mode)
            return {"system_mode": "gsettings"}

        def disconnect(self) -> dict[str, str]:
            return {"system_mode": "none"}

    manager = Manager(
        state,
        tmp_path / "config",
        exposure=Exposure(),  # type: ignore[arg-type]
        supervisor=Quiet(),  # type: ignore[arg-type]
    )
    manager.route_lookup = lambda: "[]"
    monkeypatch.setattr("omarchy_proxy.manager.shutil.which", lambda name: f"/bin/{name}")
    result = manager.connect(
        catalog,
        str(row["id"]),
        mode="tun",
        preference="xray",
        backend=Backend(),  # type: ignore[arg-type]
        timeout=1,
    )
    assert called == []
    assert result["system_mode"] == "none"
    assert result["reason"] == "tun_requires_singbox"


def test_connect_without_acknowledgement_does_not_start(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    row = catalog.add("socks5://10.0.0.1:1080#Mine")
    state = tmp_path / "state"
    manager = Manager(state, tmp_path / "config")
    with pytest.raises(ManagerError) as exc:
        manager.connect(catalog, str(row["id"]), mode="tun")
    assert exc.value.code == "tun_unacknowledged"
    assert not (state / "config.json").exists()


def test_bad_route_json_is_not_reported_as_another_tunnel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("omarchy_proxy.backends.detect.shutil.which", lambda name: "/bin/sing-box")
    catalog = Catalog(tmp_path / "servers.json")
    row = catalog.add("socks5://10.0.0.1:1080#Mine")
    state = tmp_path / "state"
    state.mkdir()
    (state / "tun-acknowledged").write_text("acknowledged\n", encoding="utf-8")
    manager = Manager(state, tmp_path / "config")
    manager.route_lookup = lambda: "not-json"
    with pytest.raises(ManagerError) as exc:
        manager.connect(catalog, str(row["id"]), mode="tun")
    assert exc.value.code == "route_unavailable"


def test_doctor_reports_a_missing_helper_and_clears_when_installed(tmp_path: Path) -> None:
    missing = doctor_report(
        tmp_path, tmp_path / "servers.json", mode="tun", sudoers=tmp_path / "nope"
    )
    codes = [item["code"] for item in missing["issues"]]
    assert "tun_helper_missing" in codes
    hint = next(item["hint"] for item in missing["issues"] if item["code"] == "tun_helper_missing")
    assert "sudo install -m 755" in hint
    assert "/etc/sudoers.d/omarchy-proxy" in hint
    present = tmp_path / "sudoers"
    present.write_text("user\n", encoding="utf-8")
    found = doctor_report(tmp_path, tmp_path / "servers.json", mode="tun", sudoers=present)
    assert "tun_helper_missing" not in [item["code"] for item in found["issues"]]
