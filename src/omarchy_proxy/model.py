"""Canonical internal model of a proxy server.

Polymorphic design: one frozen dataclass per protocol, all sharing a common
``Server`` base. Required fields are enforced by the type system — a
``VlessServer`` cannot exist without a ``uuid``, and mypy rejects a backend
that tries to read ``server.method`` from a VLESS entry.

Layout:

- ``Server`` — abstract base with the fields every protocol has.
- ``TransportParams`` — discriminated variants for stream transports
  (TCP, WebSocket, gRPC, HTTP/2, QUIC, HTTPUpgrade).
- ``TlsParams`` / ``RealityParams`` — TLS configuration blocks.
- One ``*Server`` dataclass per supported protocol.
- ``Server`` (a union alias at the bottom) is what the rest of the codebase
  actually passes around.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Literal, TypeAlias, cast


class Protocol(StrEnum):
    """Supported proxy protocols."""

    SOCKS5 = "socks5"
    SOCKS5H = "socks5h"
    HTTP = "http"
    HTTPS = "https"
    SHADOWSOCKS = "ss"
    VLESS = "vless"
    VMESS = "vmess"
    TROJAN = "trojan"
    HYSTERIA2 = "hysteria2"
    TUIC = "tuic"

    @classmethod
    def from_scheme(cls, scheme: str) -> Protocol:
        """Map a URI scheme to a Protocol, normalising aliases (``hy2``)."""
        s = scheme.lower()
        if s == "hy2":
            s = "hysteria2"
        try:
            return cls(s)
        except ValueError as exc:
            raise KeyError(scheme) from exc


# ─── Transport variants ────────────────────────────────────────────────────────


class TransportKind(StrEnum):
    TCP = "tcp"
    WS = "ws"
    GRPC = "grpc"
    H2 = "h2"
    QUIC = "quic"
    HTTPUPGRADE = "httpupgrade"


@dataclass(frozen=True)
class TcpTransport:
    """Plain TCP, no extra framing. Default for most protocols."""

    kind: Literal[TransportKind.TCP] = TransportKind.TCP


@dataclass(frozen=True)
class WsTransport:
    """WebSocket transport."""

    path: str = "/"
    host: str | None = None  # Host header; defaults to server host when None
    kind: Literal[TransportKind.WS] = TransportKind.WS


@dataclass(frozen=True)
class GrpcTransport:
    """gRPC transport."""

    service_name: str = ""
    mode: str = "gun"
    kind: Literal[TransportKind.GRPC] = TransportKind.GRPC


@dataclass(frozen=True)
class H2Transport:
    """HTTP/2 transport."""

    path: str = "/"
    host: str | None = None
    kind: Literal[TransportKind.H2] = TransportKind.H2


@dataclass(frozen=True)
class QuicTransport:
    """QUIC transport."""

    kind: Literal[TransportKind.QUIC] = TransportKind.QUIC


@dataclass(frozen=True)
class HttpUpgradeTransport:
    """HTTPUpgrade transport."""

    path: str = "/"
    host: str | None = None
    kind: Literal[TransportKind.HTTPUPGRADE] = TransportKind.HTTPUPGRADE


TransportParams: TypeAlias = (
    TcpTransport | WsTransport | GrpcTransport | H2Transport | QuicTransport | HttpUpgradeTransport
)


# ─── TLS / Reality ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TlsParams:
    """Standard TLS configuration."""

    sni: str | None = None
    alpn: tuple[str, ...] = ()
    fingerprint: str | None = None  # utls fingerprint, e.g. "chrome"

    def __post_init__(self) -> None:
        # JSON round-trips deliver a list; normalise to tuple for equality.
        alpn = cast("tuple[str, ...] | list[str]", self.alpn)
        if isinstance(alpn, list):
            object.__setattr__(self, "alpn", tuple(alpn))


@dataclass(frozen=True)
class RealityParams:
    """Reality configuration (VLESS only)."""

    public_key: str  # pbk
    short_id: str | None = None  # sid
    sni: str | None = None
    fingerprint: str | None = None


# ─── Server base ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServerBase:
    """Fields every proxy server shares."""

    host: str
    port: int
    name: str
    # Anything the parser did not model, preserved verbatim for round-trip.
    # Keyword-only so subclasses can declare their own required positional
    # fields without violating dataclass field ordering.
    extra: dict[str, str] = field(default_factory=dict, compare=True, kw_only=True)

    PROTOCOL: ClassVar[Protocol]


# ─── Per-protocol servers ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Socks5Server(ServerBase):
    """SOCKS5 / SOCKS5H proxy. ``dns_via_proxy`` distinguishes socks5h."""

    PROTOCOL: ClassVar[Protocol] = Protocol.SOCKS5

    user: str | None = None
    password: str | None = None
    dns_via_proxy: bool = False  # True for socks5h


@dataclass(frozen=True)
class HttpProxyServer(ServerBase):
    """HTTP CONNECT proxy. ``use_tls`` distinguishes http:// from https://."""

    PROTOCOL: ClassVar[Protocol] = Protocol.HTTP

    user: str | None = None
    password: str | None = None
    use_tls: bool = False  # True for https://
    tls: TlsParams | None = None


@dataclass(frozen=True)
class ShadowsocksServer(ServerBase):
    """Shadowsocks server."""

    PROTOCOL: ClassVar[Protocol] = Protocol.SHADOWSOCKS

    method: str  # e.g. "aes-256-gcm"; validated by the parser
    password: str


@dataclass(frozen=True)
class VlessServer(ServerBase):
    """VLESS server. TLS or Reality configured via the matching field."""

    PROTOCOL: ClassVar[Protocol] = Protocol.VLESS

    uuid: str
    flow: str | None = None  # e.g. "xtls-rprx-vision"
    transport: TransportParams = field(default_factory=TcpTransport)
    tls: TlsParams | None = None
    reality: RealityParams | None = None


@dataclass(frozen=True)
class VmessServer(ServerBase):
    """VMess server."""

    PROTOCOL: ClassVar[Protocol] = Protocol.VMESS

    uuid: str
    alter_id: int = 0
    security: str = "auto"  # aes-128-gcm / chacha20-poly1305 / auto / none
    transport: TransportParams = field(default_factory=TcpTransport)
    tls: TlsParams | None = None


@dataclass(frozen=True)
class TrojanServer(ServerBase):
    """Trojan server."""

    PROTOCOL: ClassVar[Protocol] = Protocol.TROJAN

    password: str
    transport: TransportParams = field(default_factory=TcpTransport)
    tls: TlsParams = field(default_factory=TlsParams)


@dataclass(frozen=True)
class Hysteria2Server(ServerBase):
    """Hysteria2 server."""

    PROTOCOL: ClassVar[Protocol] = Protocol.HYSTERIA2

    password: str
    obfs_password: str | None = None
    congestion_control: str | None = None
    tls: TlsParams = field(default_factory=TlsParams)


@dataclass(frozen=True)
class TuicServer(ServerBase):
    """TUIC server."""

    PROTOCOL: ClassVar[Protocol] = Protocol.TUIC

    uuid: str
    password: str
    congestion_control: str | None = None
    tls: TlsParams = field(default_factory=TlsParams)


# ─── Union alias ───────────────────────────────────────────────────────────────


Server: TypeAlias = (
    Socks5Server
    | HttpProxyServer
    | ShadowsocksServer
    | VlessServer
    | VmessServer
    | TrojanServer
    | Hysteria2Server
    | TuicServer
)


# ─── JSON serialization ────────────────────────────────────────────────────────


def _transport_to_dict(t: TransportParams) -> dict[str, Any]:
    data = asdict(t)
    data["kind"] = t.kind.value
    return data


def _transport_from_dict(data: dict[str, Any]) -> TransportParams:
    kind = TransportKind(data["kind"])
    payload = {k: v for k, v in data.items() if k != "kind"}
    cls = {
        TransportKind.TCP: TcpTransport,
        TransportKind.WS: WsTransport,
        TransportKind.GRPC: GrpcTransport,
        TransportKind.H2: H2Transport,
        TransportKind.QUIC: QuicTransport,
        TransportKind.HTTPUPGRADE: HttpUpgradeTransport,
    }[kind]
    return cast(TransportParams, cls(**payload))


def _protocol_value(server: Server) -> str:
    if isinstance(server, Socks5Server) and server.dns_via_proxy:
        return Protocol.SOCKS5H.value
    if isinstance(server, HttpProxyServer) and server.use_tls:
        return Protocol.HTTPS.value
    return server.PROTOCOL.value


def server_to_dict(server: Server) -> dict[str, Any]:
    """Serialise any Server subtype to a JSON-safe dict."""
    raw = asdict(server)
    raw["protocol"] = _protocol_value(server)
    # Re-emit transport with its string discriminator (asdict flattens nested
    # dataclasses but leaves the kind as an enum — we want a plain string).
    if isinstance(server, (VlessServer, VmessServer, TrojanServer)):
        raw["transport"] = _transport_to_dict(server.transport)
    return raw


_PROTOCOL_CLASSES: dict[Protocol, type[Server]] = {
    Protocol.SOCKS5: Socks5Server,
    Protocol.SOCKS5H: Socks5Server,  # same dataclass; dns_via_proxy distinguishes
    Protocol.HTTP: HttpProxyServer,
    Protocol.HTTPS: HttpProxyServer,  # same; use_tls distinguishes
    Protocol.SHADOWSOCKS: ShadowsocksServer,
    Protocol.VLESS: VlessServer,
    Protocol.VMESS: VmessServer,
    Protocol.TROJAN: TrojanServer,
    Protocol.HYSTERIA2: Hysteria2Server,
    Protocol.TUIC: TuicServer,
}


def server_from_dict(data: dict[str, Any]) -> Server:
    """Inverse of :func:`server_to_dict`. Raises on unknown protocol or fields."""
    if "protocol" not in data:
        raise ValueError("missing required field: protocol")
    protocol = Protocol(data["protocol"])
    cls = _PROTOCOL_CLASSES[protocol]
    payload: dict[str, Any] = dict(data)
    payload.pop("protocol")
    if protocol is Protocol.SOCKS5H:
        payload["dns_via_proxy"] = True
    elif protocol is Protocol.HTTPS:
        payload["use_tls"] = True
        if not isinstance(payload.get("tls"), dict):
            payload["tls"] = TlsParams(sni=str(payload.get("host", "")) or None)

    # Rebuild nested transport if present
    if "transport" in payload and isinstance(payload["transport"], dict):
        payload["transport"] = _transport_from_dict(payload["transport"])

    # Rebuild TLS / Reality blocks based on the server class
    if cls is VlessServer:
        if isinstance(payload.get("tls"), dict):
            payload["tls"] = TlsParams(**payload["tls"])
        if isinstance(payload.get("reality"), dict):
            payload["reality"] = RealityParams(**payload["reality"])
    elif cls in (VmessServer, TrojanServer, Hysteria2Server, TuicServer, HttpProxyServer):
        tls = payload.get("tls")
        if isinstance(tls, dict):
            payload["tls"] = TlsParams(**tls)

    return cls(**payload)


def server_to_json(server: Server) -> str:
    return json.dumps(server_to_dict(server), ensure_ascii=False, sort_keys=True)


def server_from_json(text: str) -> Server:
    return server_from_dict(json.loads(text))
