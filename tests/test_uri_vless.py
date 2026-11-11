"""VLESS share-link parsing."""

from __future__ import annotations

from omarchy_proxy.model import (
    GrpcTransport,
    HttpUpgradeTransport,
    RealityParams,
    TcpTransport,
    VlessServer,
    WsTransport,
)
from omarchy_proxy.uri import ParseError, parse_uri, serialize_uri

UUID = "01234567-89ab-cdef-0123-456789abcdef"


def test_reality_and_flow() -> None:
    uri = (
        f"vless://{UUID}@vless.example.com:443?security=reality&flow=xtls-rprx-vision"
        "&sni=cdn.example.com&pbk=pbk-abc&sid=sid-01&fp=chrome#VLESS%20Reality"
    )
    server = parse_uri(uri)
    assert isinstance(server, VlessServer)
    assert server.flow == "xtls-rprx-vision"
    assert server.reality == RealityParams(
        public_key="pbk-abc",
        short_id="sid-01",
        sni="cdn.example.com",
        fingerprint="chrome",
    )
    assert isinstance(server.transport, TcpTransport)
    assert parse_uri(serialize_uri(server)) == server


def test_websocket_tls() -> None:
    uri = f"vless://{UUID}@vless.example.com:443?type=ws&path=/ws&host=cdn.example.com&security=tls&sni=cdn.example.com&alpn=h2,http/1.1&fp=chrome"
    server = parse_uri(uri)
    assert isinstance(server, VlessServer)
    assert server.transport == WsTransport(path="/ws", host="cdn.example.com")
    assert server.tls is not None
    assert server.tls.sni == "cdn.example.com"
    assert server.tls.alpn == ("h2", "http/1.1")
    assert server.tls.fingerprint == "chrome"
    assert parse_uri(serialize_uri(server)) == server


def test_grpc_and_httpupgrade() -> None:
    grpc = parse_uri(
        f"vless://{UUID}@h.example:443?type=grpc&serviceName=svc&mode=multi&security=tls&sni=h.example"
    )
    upgrade = parse_uri(f"vless://{UUID}@h.example:443?type=httpupgrade&path=/up&host=cdn.example")
    assert isinstance(grpc, VlessServer)
    assert grpc.transport == GrpcTransport(service_name="svc", mode="multi")
    assert isinstance(upgrade, VlessServer)
    assert upgrade.transport == HttpUpgradeTransport(path="/up", host="cdn.example")
    assert parse_uri(serialize_uri(grpc)) == grpc
    assert parse_uri(serialize_uri(upgrade)) == upgrade


def test_unknown_param_is_kept() -> None:
    server = parse_uri(f"vless://{UUID}@h.example:443?futureparam=xyz#Name")
    assert isinstance(server, VlessServer)
    assert server.extra == {"futureparam": "xyz"}
    assert "futureparam=xyz" in serialize_uri(server)


def test_unused_known_params_round_trip() -> None:
    reality = parse_uri(f"vless://{UUID}@h.example:443?security=reality&pbk=k&alpn=h2#N")
    tls = parse_uri(f"vless://{UUID}@h.example:443?security=tls&pbk=abc#N")
    assert isinstance(reality, VlessServer)
    assert isinstance(tls, VlessServer)
    assert reality.extra["alpn"] == "h2"
    assert tls.extra["pbk"] == "abc"
    assert parse_uri(serialize_uri(reality)) == reality
    assert parse_uri(serialize_uri(tls)) == tls


def test_blank_flow_is_absent() -> None:
    server = parse_uri(f"vless://{UUID}@h.example:443?flow=#N")
    assert isinstance(server, VlessServer)
    assert server.flow is None
    assert parse_uri(serialize_uri(server)) == server


def test_missing_and_invalid_uuid() -> None:
    missing = parse_uri("vless://@h.example:443")
    invalid = parse_uri("vless://not-a-uuid@h.example:443")
    assert isinstance(missing, ParseError)
    assert missing.code == "missing_field"
    assert missing.field == "uuid"
    assert isinstance(invalid, ParseError)
    assert invalid.code == "invalid_uuid"
