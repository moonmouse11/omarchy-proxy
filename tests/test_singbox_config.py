"""sing-box outbound shape and the version floor."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from omarchy_proxy.backends.base import BackendSettings, parse_version
from omarchy_proxy.backends.singbox import MIN_VERSION, SingBoxBackend
from omarchy_proxy.model import (
    Hysteria2Server,
    RealityParams,
    Server,
    ShadowsocksServer,
    Socks5Server,
    TlsParams,
    TrojanServer,
    TuicServer,
    VlessServer,
    VmessServer,
    WsTransport,
)

GOLDEN = Path(__file__).parent / "golden" / "singbox"
SETTINGS: BackendSettings = {
    "socks_port": 10808,
    "http_port": 10809,
    "health_url": "http://example.test/generate_204",
}
UUID = "01234567-89ab-cdef-0123-456789abcdef"


def test_outbound_matches_golden_files() -> None:
    servers: dict[str, Server] = {
        "socks": Socks5Server(host="10.0.0.1", port=1080, name="s"),
        "hysteria2": Hysteria2Server(
            host="hy.example",
            port=443,
            name="hy",
            password="hy-pass",
            obfs_password="obfs",
            tls=TlsParams(sni="hy.example"),
        ),
        "tuic": TuicServer(
            host="tuic.example",
            port=443,
            name="tuic",
            uuid=UUID,
            password="tuic-pass",
            congestion_control="bbr",
            tls=TlsParams(sni="tuic.example"),
        ),
        "vless": VlessServer(
            host="v.example",
            port=443,
            name="v",
            uuid=UUID,
            flow="xtls-rprx-vision",
            reality=RealityParams(
                public_key="pbk", short_id="sid", sni="www.example.com", fingerprint="chrome"
            ),
        ),
        "vmess": VmessServer(
            host="m.example",
            port=443,
            name="m",
            uuid=UUID,
            transport=WsTransport(path="/vmess", host="cdn.example"),
            tls=TlsParams(sni="m.example"),
        ),
        "trojan": TrojanServer(
            host="t.example",
            port=443,
            name="t",
            password="trojan-pass",
            tls=TlsParams(sni="t.example", alpn=("h2",)),
        ),
        "ss": ShadowsocksServer(
            host="10.0.0.3", port=8388, name="ss", method="aes-256-gcm", password="secret"
        ),
    }
    backend = SingBoxBackend()
    for name, server in servers.items():
        config = backend.generate_config(server, SETTINGS)
        outbound = config["outbounds"][0]
        outbound.pop("tag")
        expected = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
        assert outbound == expected


def test_split_ports_and_mixed_inbound() -> None:
    server = Socks5Server(host="10.0.0.1", port=1080, name="s")
    split = SingBoxBackend().generate_config(server, SETTINGS)
    assert [item["type"] for item in split["inbounds"]] == ["socks", "http"]
    assert split["route"]["rules"][0]["outbound"] == "direct"
    same: BackendSettings = {**SETTINGS, "http_port": SETTINGS["socks_port"]}
    mixed = SingBoxBackend().generate_config(server, same)
    assert mixed["inbounds"][0]["type"] == "mixed"
    assert mixed["inbounds"][0]["listen_port"] == 10808


def test_version_floor() -> None:
    assert parse_version("sing-box version 1.12.0") == MIN_VERSION
    assert parse_version("sing-box version 1.11.0") < MIN_VERSION


@pytest.mark.skipif(shutil.which("sing-box") is None, reason="sing-box binary is not installed")
def test_generated_config_passes_singbox(tmp_path: Path) -> None:
    server = Socks5Server(host="10.0.0.1", port=1080, name="s")
    config = SingBoxBackend().generate_config(server, SETTINGS)
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    SingBoxBackend().validate_config(path)
