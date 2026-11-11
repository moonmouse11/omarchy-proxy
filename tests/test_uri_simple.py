"""URI parsing for socks5, socks5h, http, and https."""

from __future__ import annotations

import pytest

from omarchy_proxy.model import HttpProxyServer, Socks5Server
from omarchy_proxy.uri import ParseError, parse_uri, serialize_uri


def test_socks5_userinfo_fragment_and_port() -> None:
    server = parse_uri("socks5://alice:s%40cret@proxy.example.com:1080#My%20SOCKS")
    assert isinstance(server, Socks5Server)
    assert server.user == "alice"
    assert server.password == "s@cret"
    assert server.host == "proxy.example.com"
    assert server.port == 1080
    assert server.name == "My SOCKS"
    assert server.dns_via_proxy is False


def test_socks5h_flag_and_default_name() -> None:
    server = parse_uri("socks5h://proxy.example.com:1080")
    assert isinstance(server, Socks5Server)
    assert server.dns_via_proxy is True
    assert server.name == "proxy.example.com:1080"


def test_http_and_https() -> None:
    http = parse_uri("http://bob:p%40ss@proxy.example.com:8080#HTTP")
    https = parse_uri("https://proxy.example.com#HTTPS")
    assert isinstance(http, HttpProxyServer)
    assert http.user == "bob"
    assert http.password == "p@ss"
    assert http.use_tls is False
    assert isinstance(https, HttpProxyServer)
    assert https.use_tls is True
    assert https.port == 443
    assert https.tls is not None
    assert https.tls.sni == "proxy.example.com"


def test_encoded_display_name() -> None:
    server = parse_uri("socks5://example.com:443#My%20Server%20%F0%9F%87%B3%F0%9F%87%B1")
    assert isinstance(server, Socks5Server)
    assert server.name == "My Server 🇳🇱"


def test_missing_host() -> None:
    err = parse_uri("socks5://:1080")
    assert isinstance(err, ParseError)
    assert err.code == "missing_field"
    assert err.field == "host"


def test_invalid_port() -> None:
    err = parse_uri("http://example.com:99999")
    assert isinstance(err, ParseError)
    assert err.code == "invalid_port"


def test_empty_name_rejected() -> None:
    err = parse_uri("socks5://example.com:1080#%20%20")
    assert isinstance(err, ParseError)
    assert err.code == "missing_field"
    assert err.field == "name"


def test_password_without_username_is_rejected() -> None:
    err = parse_uri("socks5://:secret@proxy.example.com:1080")
    assert isinstance(err, ParseError)
    assert err.code == "missing_field"
    assert err.field == "user"


def test_unknown_query_round_trips() -> None:
    server = parse_uri("http://proxy.example.com:8080?foo=1#Name")
    assert isinstance(server, HttpProxyServer)
    assert server.extra == {"foo": "1"}
    assert serialize_uri(server) == "http://proxy.example.com:8080?foo=1#Name"


def test_known_scheme_without_parser_is_not_unsupported() -> None:
    err = parse_uri("vless://example")
    assert isinstance(err, ParseError)
    assert err.code == "not_implemented"
    assert err.scheme == "vless"


def test_ipv6_default_name_keeps_brackets() -> None:
    server = parse_uri("socks5://[::1]:1080")
    assert isinstance(server, Socks5Server)
    assert server.host == "::1"
    assert server.name == "[::1]:1080"


def test_unsupported_and_garbage() -> None:
    bad = parse_uri("wireguard://example.com")
    assert isinstance(bad, ParseError)
    assert bad.code == "unsupported_scheme"
    assert bad.scheme == "wireguard"
    junk = parse_uri("not a uri")
    assert isinstance(junk, ParseError)
    assert junk.code == "invalid_uri"


@pytest.mark.parametrize(
    "uri",
    [
        "socks5://alice:secret@proxy.example.com:1080#My%20SOCKS",
        "socks5h://proxy.example.com:1080#H",
        "http://bob:p%40ss@proxy.example.com:8080#HTTP",
        "https://proxy.example.com:8443#HTTPS",
    ],
)
def test_round_trip(uri: str) -> None:
    server = parse_uri(uri)
    assert not isinstance(server, ParseError)
    again = parse_uri(serialize_uri(server))
    assert again == server
