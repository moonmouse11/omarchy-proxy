"""Round-trip tests for the polymorphic Server model across all protocols."""

from __future__ import annotations

import pytest

from omarchy_proxy.model import (
    GrpcTransport,
    H2Transport,
    HttpProxyServer,
    HttpUpgradeTransport,
    Hysteria2Server,
    Protocol,
    QuicTransport,
    RealityParams,
    Server,
    ShadowsocksServer,
    Socks5Server,
    TcpTransport,
    TlsParams,
    TrojanServer,
    TuicServer,
    VlessServer,
    VmessServer,
    WsTransport,
    server_from_dict,
    server_from_json,
    server_to_dict,
    server_to_json,
)


def representative_servers() -> list[Server]:
    """One Server per protocol, exercising the interesting field combinations."""
    return [
        Socks5Server(
            host="proxy.example.com",
            port=1080,
            name="My SOCKS5",
            user="alice",
            password="secret",
        ),
        Socks5Server(
            host="proxy.example.com",
            port=1080,
            name="SOCKS5H",
            dns_via_proxy=True,
        ),
        HttpProxyServer(
            host="proxy.example.com",
            port=8080,
            name="HTTP",
            user="bob",
            password="p@ss",
        ),
        HttpProxyServer(
            host="proxy.example.com",
            port=8443,
            name="HTTPS",
            use_tls=True,
            tls=TlsParams(sni="proxy.example.com"),
        ),
        ShadowsocksServer(
            host="ss.example.com",
            port=8388,
            name="SS",
            method="aes-256-gcm",
            password="ss-password",
        ),
        VlessServer(
            host="vless.example.com",
            port=443,
            name="VLESS Reality",
            uuid="01234567-89ab-cdef-0123-456789abcdef",
            flow="xtls-rprx-vision",
            reality=RealityParams(
                public_key="pbk-abc",
                short_id="sid-01",
                sni="cdn.example.com",
                fingerprint="chrome",
            ),
        ),
        VlessServer(
            host="vless-ws.example.com",
            port=443,
            name="VLESS WS",
            uuid="01234567-89ab-cdef-0123-456789abcdef",
            transport=WsTransport(path="/ws", host="cdn.example.com"),
            tls=TlsParams(sni="cdn.example.com"),
        ),
        VlessServer(
            host="vless-grpc.example.com",
            port=443,
            name="VLESS gRPC",
            uuid="01234567-89ab-cdef-0123-456789abcdef",
            transport=GrpcTransport(service_name="svc", mode="multi"),
            tls=TlsParams(sni="grpc.example.com"),
        ),
        VmessServer(
            host="vmess.example.com",
            port=443,
            name="VMess",
            uuid="01234567-89ab-cdef-0123-456789abcdef",
            alter_id=0,
            transport=WsTransport(path="/vmess"),
            tls=TlsParams(sni="vmess.example.com"),
        ),
        VmessServer(
            host="vmess-h2.example.com",
            port=443,
            name="VMess H2",
            uuid="01234567-89ab-cdef-0123-456789abcdef",
            transport=H2Transport(path="/h2", host="cdn.example.com"),
            tls=TlsParams(sni="cdn.example.com"),
        ),
        TrojanServer(
            host="trojan.example.com",
            port=443,
            name="Trojan",
            password="trojan-password",
            tls=TlsParams(sni="trojan.example.com", alpn=("h2", "http/1.1")),
        ),
        Hysteria2Server(
            host="hy2.example.com",
            port=443,
            name="Hysteria2",
            password="hy2-password",
            obfs_password="obfs",
            congestion_control="bbr",
        ),
        TuicServer(
            host="tuic.example.com",
            port=443,
            name="TUIC",
            uuid="01234567-89ab-cdef-0123-456789abcdef",
            password="tuic-password",
            congestion_control="bbr",
            tls=TlsParams(alpn=("h3",)),
        ),
        VlessServer(
            host="extra.example.com",
            port=443,
            name="VLESS with extras",
            uuid="01234567-89ab-cdef-0123-456789abcdef",
            extra={"futureparam": "xyz", "another": "1"},
        ),
        VmessServer(
            host="vmess-httpupgrade.example.com",
            port=443,
            name="VMess HTTPUpgrade",
            uuid="01234567-89ab-cdef-0123-456789abcdef",
            transport=HttpUpgradeTransport(path="/up"),
            tls=TlsParams(),
        ),
        VlessServer(
            host="vless-quic.example.com",
            port=443,
            name="VLESS QUIC",
            uuid="01234567-89ab-cdef-0123-456789abcdef",
            transport=QuicTransport(),
        ),
    ]


@pytest.mark.parametrize("server", representative_servers())
def test_json_round_trip_yields_equal_server(server: Server) -> None:
    text = server_to_json(server)
    restored = server_from_json(text)
    assert restored == server
    assert type(restored) is type(server)


@pytest.mark.parametrize("server", representative_servers())
def test_dict_round_trip_preserves_transport_variant(server: Server) -> None:
    if not isinstance(server, (VlessServer, VmessServer, TrojanServer)):
        pytest.skip("no transport field")
    data = server_to_dict(server)
    restored = server_from_dict(data)
    assert restored.transport == server.transport
    assert type(restored.transport) is type(server.transport)


def test_from_dict_rejects_unknown_fields() -> None:
    server = representative_servers()[0]
    data = server_to_dict(server)
    data["not_a_field"] = 1
    with pytest.raises(TypeError, match="not_a_field"):
        server_from_dict(data)


def test_from_dict_rejects_missing_protocol() -> None:
    with pytest.raises(ValueError, match="protocol"):
        server_from_dict({"host": "x", "port": 1, "name": "y"})


def test_protocol_field_is_emitted_as_string() -> None:
    server = representative_servers()[5]  # vless reality
    data = server_to_dict(server)
    assert data["protocol"] == "vless"
    assert data["reality"]["public_key"] == "pbk-abc"
    assert data["tls"] is None


def test_socks5h_uses_same_class_with_flag() -> None:
    plain = representative_servers()[0]
    h = representative_servers()[1]
    assert isinstance(plain, Socks5Server)
    assert isinstance(h, Socks5Server)
    assert plain.dns_via_proxy is False
    assert h.dns_via_proxy is True


def test_https_uses_same_class_with_flag() -> None:
    plain = representative_servers()[2]
    tls = representative_servers()[3]
    assert isinstance(plain, HttpProxyServer)
    assert isinstance(tls, HttpProxyServer)
    assert plain.use_tls is False
    assert tls.use_tls is True


def test_protocol_from_scheme_normalises_alias() -> None:
    assert Protocol.from_scheme("hy2") is Protocol.HYSTERIA2
    assert Protocol.from_scheme("vless") is Protocol.VLESS


def test_protocol_from_scheme_rejects_unknown() -> None:
    with pytest.raises(KeyError):
        Protocol.from_scheme("wireguard")


def test_vless_requires_uuid() -> None:
    """Type system enforces required fields at construction."""
    with pytest.raises(TypeError):
        VlessServer(host="x", port=443, name="V")  # type: ignore[call-arg]


def test_shadowsocks_method_is_required() -> None:
    with pytest.raises(TypeError):
        ShadowsocksServer(host="x", port=8388, name="S", password="pw")  # type: ignore[call-arg]


def test_default_transport_is_tcp() -> None:
    v = VlessServer(host="x", port=443, name="V", uuid="u")
    assert isinstance(v.transport, TcpTransport)


def test_default_tls_for_trojan_is_present_but_empty() -> None:
    t = TrojanServer(host="x", port=443, name="T", password="pw")
    assert t.tls.sni is None
    assert t.tls.alpn == ()


def test_socks5h_and_https_survive_dict_round_trip() -> None:
    socks = Socks5Server(host="h", port=1080, name="n", dns_via_proxy=True)
    http = HttpProxyServer(host="h", port=443, name="n", use_tls=True, tls=TlsParams(sni="h"))
    assert server_to_dict(socks)["protocol"] == "socks5h"
    assert server_from_dict(server_to_dict(socks)) == socks
    assert server_to_dict(http)["protocol"] == "https"
    assert server_from_dict(server_to_dict(http)) == http


def test_protocol_key_sets_flags_when_body_omits_them() -> None:
    socks = server_from_dict({"protocol": "socks5h", "host": "h", "port": 1, "name": "n"})
    https = server_from_dict({"protocol": "https", "host": "h", "port": 443, "name": "n"})
    assert isinstance(socks, Socks5Server)
    assert socks.dns_via_proxy is True
    assert isinstance(https, HttpProxyServer)
    assert https.use_tls is True
    assert https.tls == TlsParams(sni="h")
