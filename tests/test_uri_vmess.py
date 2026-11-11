"""VMess base64 JSON share links."""

from __future__ import annotations

import base64
import json

from omarchy_proxy.model import TlsParams, VmessServer, WsTransport
from omarchy_proxy.uri import ParseError, parse_uri, serialize_uri

UUID = "01234567-89ab-cdef-0123-456789abcdef"


def _link(payload: dict[str, object], *, urlsafe: bool = True, pad: bool = False) -> str:
    raw = json.dumps(payload).encode()
    token = base64.urlsafe_b64encode(raw) if urlsafe else base64.b64encode(raw)
    text = token.decode()
    return "vmess://" + (text if pad else text.rstrip("="))


def test_full_field_set_round_trips() -> None:
    server = parse_uri(
        _link(
            {
                "v": "2",
                "ps": "VMess",
                "add": "vmess.example.com",
                "port": "443",
                "id": UUID,
                "aid": "0",
                "scy": "aes-128-gcm",
                "net": "ws",
                "type": "none",
                "host": "cdn.example.com",
                "path": "/vmess",
                "tls": "tls",
                "sni": "vmess.example.com",
                "future": "1",
            }
        )
    )
    assert isinstance(server, VmessServer)
    assert server.host == "vmess.example.com"
    assert server.port == 443
    assert server.uuid == UUID
    assert server.alter_id == 0
    assert server.security == "aes-128-gcm"
    assert server.transport == WsTransport(path="/vmess", host="cdn.example.com")
    assert server.tls == TlsParams(sni="vmess.example.com")
    assert server.name == "VMess"
    assert server.extra["v"] == "2"
    assert server.extra["type"] == "none"
    assert server.extra["future"] == "1"
    assert parse_uri(serialize_uri(server)) == server


def test_standard_base64_with_padding() -> None:
    server = parse_uri(
        _link({"add": "h.example", "port": 443, "id": UUID, "ps": "P"}, urlsafe=False, pad=True)
    )
    assert isinstance(server, VmessServer)
    assert server.name == "P"


def test_fragment_wins_over_ps_and_sni_is_not_invented() -> None:
    payload = {"add": "h.example", "port": "443", "id": UUID, "ps": "Inside", "tls": "tls"}
    server = parse_uri(_link(payload) + "#Outside")
    assert isinstance(server, VmessServer)
    assert server.name == "Outside"
    assert server.tls is not None
    assert server.tls.sni is None
    again = parse_uri(serialize_uri(server))
    assert again == server


def test_invalid_aid_is_not_a_missing_field() -> None:
    err = parse_uri(_link({"add": "h.example", "port": "443", "id": UUID, "aid": "x"}))
    assert isinstance(err, ParseError)
    assert err.code == "invalid_field"
    assert err.field == "aid"


def test_missing_field() -> None:
    err = parse_uri(_link({"port": 443, "id": UUID}))
    assert isinstance(err, ParseError)
    assert err.code == "missing_field"
    assert err.field == "add"


def test_non_json_payload() -> None:
    token = base64.urlsafe_b64encode(b"not-json").decode().rstrip("=")
    err = parse_uri(f"vmess://{token}")
    assert isinstance(err, ParseError)
    assert err.code == "invalid_json"
