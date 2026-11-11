"""Shadowsocks SIP002 and legacy URI parsing."""

from __future__ import annotations

import base64

import pytest

from omarchy_proxy.model import ShadowsocksServer
from omarchy_proxy.uri import ParseError, parse_uri, serialize_uri

METHODS = [
    "aes-128-gcm",
    "aes-256-gcm",
    "chacha20-ietf-poly1305",
    "2022-blake3-aes-128-gcm",
    "2022-blake3-aes-256-gcm",
    "2022-blake3-chacha20-poly1305",
    "chacha20-ietf",
    "aes-128-cfb",
    "aes-192-cfb",
    "aes-256-cfb",
    "aes-128-ctr",
    "aes-256-ctr",
]


def _b64(text: str, *, urlsafe: bool, pad: bool) -> str:
    raw = text.encode()
    encoded = base64.urlsafe_b64encode(raw) if urlsafe else base64.b64encode(raw)
    token = encoded.decode()
    return token if pad else token.rstrip("=")


@pytest.mark.parametrize("method", METHODS)
def test_each_allowed_method(method: str) -> None:
    token = _b64(f"{method}:secret", urlsafe=True, pad=False)
    server = parse_uri(f"ss://{token}@ss.example.com:8388#SS")
    assert isinstance(server, ShadowsocksServer)
    assert server.method == method
    assert server.password == "secret"


def test_sip002_and_legacy_round_trip_to_sip002() -> None:
    token = _b64("aes-256-gcm:ss-password", urlsafe=False, pad=True)
    sip = parse_uri(f"ss://{token}@ss.example.com:8388#Name")
    legacy_token = _b64("aes-256-gcm:ss-password@ss.example.com:8388", urlsafe=True, pad=False)
    legacy = parse_uri(f"ss://{legacy_token}#Name")
    assert isinstance(sip, ShadowsocksServer)
    assert isinstance(legacy, ShadowsocksServer)
    assert legacy == sip
    again = serialize_uri(legacy)
    assert again.startswith("ss://")
    assert "@ss.example.com:8388" in again
    assert parse_uri(again) == legacy


def test_deprecated_method_is_named() -> None:
    token = _b64("rc4-md5:secret", urlsafe=True, pad=False)
    err = parse_uri(f"ss://{token}@ss.example.com:8388")
    assert isinstance(err, ParseError)
    assert err.code == "unsupported_method"
    assert err.field == "rc4-md5"


def test_percent_encoded_userinfo_and_host() -> None:
    token = _b64("aes-256-gcm:p0~", urlsafe=False, pad=False)
    encoded = token.replace("+", "%2B")
    server = parse_uri(f"ss://{encoded}@ex%2Eample.com:8388")
    assert isinstance(server, ShadowsocksServer)
    assert server.password == "p0~"
    assert server.host == "ex.ample.com"


def test_invalid_base64() -> None:
    err = parse_uri("ss://!!!!@ss.example.com:8388")
    assert isinstance(err, ParseError)
    assert err.code == "invalid_base64"
