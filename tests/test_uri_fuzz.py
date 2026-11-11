"""Random input must never escape the parser."""

from __future__ import annotations

import random

from omarchy_proxy.model import (
    HttpProxyServer,
    Hysteria2Server,
    ShadowsocksServer,
    Socks5Server,
    TrojanServer,
    TuicServer,
    VlessServer,
    VmessServer,
)
from omarchy_proxy.uri import ERROR_CODES, ParseError, parse_uri

_SERVERS = (
    Socks5Server,
    HttpProxyServer,
    ShadowsocksServer,
    VlessServer,
    VmessServer,
    TrojanServer,
    Hysteria2Server,
    TuicServer,
)


def test_random_bytes_never_raise() -> None:
    rng = random.Random(27)
    for _ in range(1000):
        blob = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 80)))
        result = parse_uri(blob)
        if isinstance(result, ParseError):
            assert result.code in ERROR_CODES
            assert result.message
        else:
            assert isinstance(result, _SERVERS)
