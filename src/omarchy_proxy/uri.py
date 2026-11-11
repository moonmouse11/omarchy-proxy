"""Parse and re-serialize proxy share URIs.

Failures return a :class:`ParseError` and never raise. Implemented schemes:
``socks5``, ``socks5h``, ``http``, ``https``, and ``ss`` (SIP002 and legacy
base64). Other supported schemes return ``not_implemented``.
"""

from __future__ import annotations

import base64
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from omarchy_proxy.model import HttpProxyServer, Server, ShadowsocksServer, Socks5Server, TlsParams

_SIMPLE = frozenset({"socks5", "socks5h", "http", "https"})
_SS_METHODS = frozenset(
    {
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
    }
)
_KNOWN = frozenset(
    {
        "socks5",
        "socks5h",
        "http",
        "https",
        "ss",
        "vless",
        "vmess",
        "trojan",
        "hysteria2",
        "hy2",
        "tuic",
    }
)
_DEFAULT_PORT = {"socks5": 1080, "socks5h": 1080, "http": 80, "https": 443}


class ParseError(Exception):
    """Machine-readable parse failure. Also raised internally, then returned."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        scheme: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.scheme = scheme
        self.field = field


def parse_uri(text: str) -> Server | ParseError:
    """Parse one share URI. Never raises."""
    try:
        return _parse(text)
    except ParseError as exc:
        return exc
    except Exception as exc:
        return ParseError("invalid_uri", str(exc) or "invalid uri")


def serialize_uri(server: Server) -> str:
    """Canonical URI for schemes implemented so far."""
    if isinstance(server, Socks5Server):
        scheme = "socks5h" if server.dns_via_proxy else "socks5"
        return _simple_uri(scheme, server)
    if isinstance(server, HttpProxyServer):
        scheme = "https" if server.use_tls else "http"
        return _simple_uri(scheme, server)
    if isinstance(server, ShadowsocksServer):
        return _serialize_ss(server)
    raise TypeError(f"serialization not implemented for {type(server).__name__}")


def _parse(text: str) -> Server:
    raw = text.strip()
    if not raw or "://" not in raw:
        raise ParseError("invalid_uri", "input is not a URI")
    scheme = raw.split("://", 1)[0].lower()
    if scheme not in _KNOWN:
        raise ParseError("unsupported_scheme", f"unsupported scheme: {scheme}", scheme=scheme)
    if scheme == "ss":
        return _parse_ss(raw)
    if scheme not in _SIMPLE:
        raise ParseError(
            "not_implemented",
            f"scheme not implemented yet: {scheme}",
            scheme=scheme,
        )

    parts = urlsplit(raw)
    host = parts.hostname
    if not host:
        raise ParseError("missing_field", "missing host", scheme=scheme, field="host")
    try:
        raw_port = parts.port
    except ValueError as exc:
        raise ParseError("invalid_port", str(exc), scheme=scheme, field="port") from exc
    port = _DEFAULT_PORT[scheme] if raw_port is None else raw_port
    if not 1 <= port <= 65535:
        raise ParseError("invalid_port", f"invalid port: {port}", scheme=scheme, field="port")

    name = _name(parts.fragment, host, port)
    user = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password is not None else None
    if password is not None and user is None:
        raise ParseError("missing_field", "password without username", scheme=scheme, field="user")
    extra = dict(parse_qsl(parts.query, keep_blank_values=True))

    if scheme in {"socks5", "socks5h"}:
        return Socks5Server(
            host=host,
            port=port,
            name=name,
            user=user,
            password=password,
            dns_via_proxy=scheme == "socks5h",
            extra=extra,
        )
    return HttpProxyServer(
        host=host,
        port=port,
        name=name,
        user=user,
        password=password,
        use_tls=scheme == "https",
        tls=TlsParams(sni=host) if scheme == "https" else None,
        extra=extra,
    )


def _parse_ss(raw: str) -> ShadowsocksServer:
    rest = raw.split("://", 1)[1]
    rest, _, fragment = rest.partition("#")
    rest, _, query = rest.partition("?")
    if "@" in rest:
        userinfo, hostport = rest.rsplit("@", 1)
        method, password = _ss_userinfo(_b64_text(unquote(userinfo)))
        host, port = _hostport(hostport)
    else:
        decoded = _b64_text(unquote(rest))
        if "@" not in decoded:
            raise ParseError("missing_field", "legacy ss has no host", scheme="ss", field="host")
        userinfo, hostport = decoded.rsplit("@", 1)
        method, password = _ss_userinfo(userinfo)
        host, port = _hostport(hostport)
    if method not in _SS_METHODS:
        raise ParseError(
            "unsupported_method",
            f"unsupported method: {method}",
            scheme="ss",
            field=method,
        )
    return ShadowsocksServer(
        host=host,
        port=port,
        name=_name(fragment, host, port),
        method=method,
        password=password,
        extra=dict(parse_qsl(query, keep_blank_values=True)),
    )


def _ss_userinfo(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise ParseError("missing_field", "ss userinfo has no method", scheme="ss", field="method")
    method, password = text.split(":", 1)
    if not method or not password:
        raise ParseError("missing_field", "empty method or password", scheme="ss", field="method")
    return method, password


def _hostport(text: str) -> tuple[str, int]:
    if text.startswith("["):
        host, _, port_text = text[1:].partition("]")
        port_text = port_text.removeprefix(":")
    elif ":" in text:
        host, port_text = text.rsplit(":", 1)
    else:
        raise ParseError("missing_field", "missing port", scheme="ss", field="port")
    host = unquote(host)
    if not host:
        raise ParseError("missing_field", "missing host", scheme="ss", field="host")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ParseError(
            "invalid_port",
            f"invalid port: {port_text}",
            scheme="ss",
            field="port",
        ) from exc
    if not 1 <= port <= 65535:
        raise ParseError("invalid_port", f"invalid port: {port}", scheme="ss", field="port")
    return host, port


def _b64_text(value: str) -> str:
    alphabet = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_+/=")
    if not value or any(char not in alphabet for char in value):
        raise ParseError("invalid_base64", "ss payload is not base64", scheme="ss")
    padded = value + ("=" * (-len(value) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode()).decode()
    except Exception as exc:
        raise ParseError("invalid_base64", "ss payload is not base64", scheme="ss") from exc


def _serialize_ss(server: ShadowsocksServer) -> str:
    raw = f"{server.method}:{server.password}".encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    host = f"[{server.host}]" if ":" in server.host else server.host
    query = urlencode(server.extra)
    fragment = quote(server.name, safe="")
    return urlunsplit(("ss", f"{token}@{host}:{server.port}", "", query, fragment))


def _name(fragment: str, host: str, port: int) -> str:
    if not fragment:
        shown = f"[{host}]" if ":" in host else host
        return f"{shown}:{port}"
    name = unquote(fragment).strip()
    if not name:
        raise ParseError("missing_field", "display name is empty", field="name")
    return name


def _simple_uri(scheme: str, server: Socks5Server | HttpProxyServer) -> str:
    auth = ""
    if server.user is not None:
        auth = quote(server.user, safe="")
        if server.password is not None:
            auth += ":" + quote(server.password, safe="")
        auth += "@"
    host = server.host if ":" not in server.host else f"[{server.host}]"
    fragment = quote(server.name, safe="")
    query = urlencode(server.extra)
    return urlunsplit((scheme, f"{auth}{host}:{server.port}", "", query, fragment))
