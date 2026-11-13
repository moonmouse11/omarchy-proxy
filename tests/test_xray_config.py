"""Xray outbound shape and the version floor."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from omarchy_proxy.backends.base import BackendSettings, parse_version
from omarchy_proxy.backends.xray import MIN_VERSION, BackendError, XrayBackend
from omarchy_proxy.model import (
    HttpProxyServer,
    Hysteria2Server,
    RealityParams,
    Server,
    ShadowsocksServer,
    Socks5Server,
    TlsParams,
    TrojanServer,
    VlessServer,
    VmessServer,
    WsTransport,
)

GOLDEN = Path(__file__).parent / "golden" / "xray"
SETTINGS: BackendSettings = {
    "socks_port": 10808,
    "http_port": 10809,
    "health_url": "http://example.test/generate_204",
}
UUID = "01234567-89ab-cdef-0123-456789abcdef"


def _proxy(config: dict[str, object]) -> dict[str, object]:
    outbound = config["outbounds"][0]
    assert isinstance(outbound, dict)
    outbound.pop("tag")
    return outbound


def test_outbound_matches_golden_files() -> None:
    servers: dict[str, Server] = {
        "socks": Socks5Server(host="10.0.0.1", port=1080, name="s"),
        "http": HttpProxyServer(host="10.0.0.2", port=8080, name="h", user="u", password="p"),
        "ss": ShadowsocksServer(
            host="10.0.0.3", port=8388, name="ss", method="aes-256-gcm", password="secret"
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
            tls=TlsParams(),
        ),
        "trojan": TrojanServer(
            host="t.example",
            port=443,
            name="t",
            password="trojan-pass",
            tls=TlsParams(fingerprint="chrome", alpn=("h2",)),
        ),
    }
    backend = XrayBackend()
    for name, server in servers.items():
        config = backend.generate_config(server, SETTINGS)
        expected = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
        assert _proxy(config) == expected


def test_health_rule_is_direct_and_first() -> None:
    server = Socks5Server(host="10.0.0.1", port=1080, name="s")
    config = XrayBackend().generate_config(server, SETTINGS)
    rules = config["routing"]["rules"]
    assert rules[0]["outboundTag"] == "direct"
    assert rules[0]["domain"] == ["full:example.test"]
    assert config["inbounds"][0]["settings"]["udp"] is True
    assert config["outbounds"][1]["protocol"] == "freedom"


def test_hysteria2_is_refused() -> None:
    server = Hysteria2Server(host="h.example", port=443, name="h", password="p")
    with pytest.raises(BackendError) as exc:
        XrayBackend().generate_config(server, SETTINGS)
    assert exc.value.code == "unsupported_protocol"


def test_version_floor() -> None:
    assert parse_version("Xray 1.8.0 (Xray)") == MIN_VERSION
    assert parse_version("Xray 1.7.5") < MIN_VERSION
    assert parse_version("nope") is None


@pytest.mark.skipif(shutil.which("xray") is None, reason="xray binary is not installed")
def test_generated_config_passes_xray(tmp_path: Path) -> None:
    server = Socks5Server(host="10.0.0.1", port=1080, name="s")
    config = XrayBackend().generate_config(server, SETTINGS)
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    XrayBackend().validate_config(path)
