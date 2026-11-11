"""Trojan, Hysteria2, and TUIC share links."""

from __future__ import annotations

from omarchy_proxy.model import Hysteria2Server, TrojanServer, TuicServer, WsTransport
from omarchy_proxy.uri import ParseError, parse_uri, serialize_uri

UUID = "01234567-89ab-cdef-0123-456789abcdef"


def test_trojan_ws_round_trip() -> None:
    uri = "trojan://secret@trojan.example:443?type=ws&path=/ws&host=cdn.example&sni=trojan.example#Trojan"
    server = parse_uri(uri)
    assert isinstance(server, TrojanServer)
    assert server.password == "secret"
    assert server.transport == WsTransport(path="/ws", host="cdn.example")
    assert server.tls.sni == "trojan.example"
    assert parse_uri(serialize_uri(server)) == server


def test_password_keeps_colon_and_port_zero_is_rejected() -> None:
    server = parse_uri("trojan://p:w@h.example:443#N")
    hy2 = parse_uri("hy2://a:b@h.example:443#N")
    assert isinstance(server, TrojanServer)
    assert isinstance(hy2, Hysteria2Server)
    assert server.password == "p:w"
    assert hy2.password == "a:b"
    assert parse_uri(serialize_uri(server)) == server
    err = parse_uri("trojan://pw@h.example:0")
    assert isinstance(err, ParseError)
    assert err.code == "invalid_port"


def test_trojan_requires_password() -> None:
    err = parse_uri("trojan://trojan.example:443")
    assert isinstance(err, ParseError)
    assert err.code == "missing_field"
    assert err.field == "password"


def test_hysteria2_alias_and_obfs() -> None:
    uri = "hy2://pw@hy2.example:443?obfs-password=obfs&cc=bbr&sni=hy2.example#H"
    server = parse_uri(uri)
    assert isinstance(server, Hysteria2Server)
    assert server.password == "pw"
    assert server.obfs_password == "obfs"
    assert server.congestion_control == "bbr"
    assert server.tls.sni == "hy2.example"
    again = serialize_uri(server)
    assert again.startswith("hysteria2://")
    assert parse_uri(again) == server


def test_tuic_round_trip_and_missing_password() -> None:
    uri = f"tuic://{UUID}:pw@tuic.example:443?congestion_control=bbr&alpn=h3#T"
    server = parse_uri(uri)
    assert isinstance(server, TuicServer)
    assert server.uuid == UUID
    assert server.password == "pw"
    assert server.congestion_control == "bbr"
    assert server.tls.alpn == ("h3",)
    assert parse_uri(serialize_uri(server)) == server
    missing = parse_uri(f"tuic://{UUID}@tuic.example:443")
    assert isinstance(missing, ParseError)
    assert missing.field == "password"
