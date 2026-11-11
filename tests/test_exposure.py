"""Exposure modes: gsettings, environment.d, and rendered snippets."""

from __future__ import annotations

import json
from pathlib import Path

from omarchy_proxy.doctor import status_report
from omarchy_proxy.manager import Manager
from omarchy_proxy.sysproxy import (
    DEFAULT_BYPASS,
    HTTP_PORT,
    SOCKS_PORT,
    Exposure,
    Runner,
    env_snippet,
    ignore_hosts_value,
    no_proxy_value,
    proxychains_conf,
)

_SCHEMA = "org.gnome.system.proxy"


def test_bypass_conversions() -> None:
    raw = "localhost, 127.0.0.0/8,::1"
    assert no_proxy_value(raw) == "localhost,127.0.0.0/8,::1"
    assert ignore_hosts_value(raw) == "['localhost', '127.0.0.0/8', '::1']"
    assert ignore_hosts_value("localhost,.internal.lan") == "['localhost', '.internal.lan']"
    assert no_proxy_value(DEFAULT_BYPASS) == DEFAULT_BYPASS


def test_rendered_artifacts_match_goldens() -> None:
    root = Path(__file__).parent / "golden" / "exposure"
    assert env_snippet(SOCKS_PORT, HTTP_PORT) == (root / "env.sh").read_text(encoding="utf-8")
    assert proxychains_conf(SOCKS_PORT) == (root / "proxychains.conf").read_text(encoding="utf-8")


def test_gsettings_saves_and_restores(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    saved = {f"{schema} {key}": "'old'" for schema, key in _tracked()}

    def run(args: list[str]) -> str:
        calls.append(args)
        if args[1] == "set":
            saved_state = json.loads((tmp_path / "exposure.json").read_text(encoding="utf-8"))
            assert saved_state["active"] is True
            assert saved_state["prior"][f"{_SCHEMA} mode"] == "'old'"
        if args[1] == "list-schemas":
            return f"{_SCHEMA}\n{_SCHEMA}.http\n{_SCHEMA}.https\n{_SCHEMA}.socks"
        if args[1] == "get":
            return saved[f"{args[2]} {args[3]}"]
        return ""

    manager = Manager(tmp_path, tmp_path / "config", _exposure(tmp_path, run))
    applied = manager.exposure.connect("system", bypass="localhost,.internal.lan")
    assert applied["system_mode"] == "gsettings"
    sets = [call for call in calls if call[1] == "set"]
    assert ["gsettings", "set", _SCHEMA, "mode", "manual"] in sets
    assert ["gsettings", "set", _SCHEMA, "ignore-hosts", "['localhost', '.internal.lan']"] in sets
    assert ["gsettings", "set", f"{_SCHEMA}.socks", "port", str(SOCKS_PORT)] in sets
    calls.clear()
    manager.exposure.connect("system", socks_port=2000)
    assert ["gsettings", "set", f"{_SCHEMA}.socks", "port", "2000"] in calls
    calls.clear()
    assert manager.exposure.disconnect()["active"] is False
    restored = [call for call in calls if call[1] == "set"]
    assert ["gsettings", "set", _SCHEMA, "mode", "'old'"] in restored
    again = manager.exposure.disconnect()
    assert again["active"] is False
    assert calls == restored


def test_missing_gsettings_writes_environment_d(tmp_path: Path) -> None:
    def run(_args: list[str]) -> str:
        raise OSError("missing")

    config = tmp_path / "config"
    manager = Manager(tmp_path, config, _exposure(tmp_path, run))
    applied = manager.exposure.connect("system")
    path = config / "environment.d" / "omarchy-proxy.conf"
    text = path.read_text(encoding="utf-8")
    assert "http_proxy=http://127.0.0.1:10809" in text
    assert "HTTP_PROXY=http://127.0.0.1:10809" in text
    assert "all_proxy=socks5://127.0.0.1:10808" in text
    assert "no_proxy=localhost,127.0.0.0/8,::1" in text
    assert applied["requires_relogin"] is True
    report = status_report(tmp_path)
    assert report["system_mode"] == "environment.d"
    assert report["requires_relogin"] is True
    other = config / "environment.d" / "other.conf"
    other.write_text("keep\n", encoding="utf-8")
    path.write_text("hand edited\n", encoding="utf-8")
    manager.exposure.disconnect()
    assert not path.exists()
    assert other.read_text(encoding="utf-8") == "keep\n"
    manager.exposure.disconnect()


def test_mode_change_while_connected_reverts_the_old_path(tmp_path: Path) -> None:
    def run(args: list[str]) -> str:
        if args[1] == "list-schemas":
            return "unrelated"
        return ""

    config = tmp_path / "config"
    manager = Manager(tmp_path, config, _exposure(tmp_path, run))
    manager.exposure.connect("system")
    path = config / "environment.d" / "omarchy-proxy.conf"
    assert path.exists()
    switched = manager.exposure.connect("manual")
    assert switched["system_mode"] == "none"
    assert not path.exists()
    manager.exposure.connect("env-only")
    assert not path.exists()
    stored = json.loads((tmp_path / "exposure.json").read_text(encoding="utf-8"))
    assert stored["mode"] == "env-only"
    assert stored["system_mode"] == "none"


def test_manual_connect_touches_no_files(tmp_path: Path) -> None:
    config = tmp_path / "config"
    manager = Manager(tmp_path, config, _exposure(tmp_path, lambda _args: ""))
    manager.exposure.connect("manual")
    assert list(config.glob("**/*")) == []


def _exposure(tmp_path: Path, run: Runner) -> Exposure:
    return Exposure(tmp_path, tmp_path / "config", run)


def _tracked() -> list[tuple[str, str]]:
    return [
        (_SCHEMA, "mode"),
        (_SCHEMA, "ignore-hosts"),
        (f"{_SCHEMA}.http", "host"),
        (f"{_SCHEMA}.http", "port"),
        (f"{_SCHEMA}.http", "enabled"),
        (f"{_SCHEMA}.https", "host"),
        (f"{_SCHEMA}.https", "port"),
        (f"{_SCHEMA}.socks", "host"),
        (f"{_SCHEMA}.socks", "port"),
    ]
