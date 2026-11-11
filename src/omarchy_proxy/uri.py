"""Parse and re-serialize proxy share URIs.

Failures return a :class:`ParseError` and never raise. Implemented schemes:
``socks5``, ``socks5h``, ``http``, ``https``, ``ss``, ``vless``, ``vmess``,
``trojan``, ``hysteria2`` (and ``hy2``), and ``tuic``.
"""

from __future__ import annotations

import base64
import json
from urllib.parse import SplitResult, parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from omarchy_proxy.model import (
    GrpcTransport,
    H2Transport,
    HttpProxyServer,
    HttpUpgradeTransport,
    Hysteria2Server,
    QuicTransport,
    RealityParams,
    Server,
    ShadowsocksServer,
    Socks5Server,
    TcpTransport,
    TlsParams,
    TransportParams,
    TrojanServer,
    TuicServer,
    VlessServer,
    VmessServer,
    WsTransport,
)

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


ERROR_CODES = frozenset(
    {
        "invalid_scheme",
        "invalid_uri",
        "invalid_base64",
        "invalid_json",
        "invalid_field",
        "missing_field",
        "invalid_port",
        "invalid_uuid",
        "unsupported_method",
        "unsupported_scheme",
    }
)


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


def parse_uri(text: str | bytes) -> Server | ParseError:
    """Parse one share URI. Never raises."""
    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        result: Server | ParseError = _parse(text)
    except UnicodeDecodeError:
        return ParseError("invalid_uri", "input is not utf-8")
    except ParseError as exc:
        result = exc
    except Exception as exc:
        return ParseError("invalid_uri", str(exc) or "invalid uri")
    return result


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
    if isinstance(server, VlessServer):
        return _serialize_vless(server)
    if isinstance(server, VmessServer):
        return _serialize_vmess(server)
    if isinstance(server, TrojanServer):
        return _serialize_user_uri("trojan", server.password, server)
    if isinstance(server, Hysteria2Server):
        return _serialize_user_uri("hysteria2", server.password, server)
    if isinstance(server, TuicServer):
        user = f"{server.uuid}:{quote(server.password, safe='')}"
        return _serialize_user_uri("tuic", user, server, encoded=True)
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
    if scheme == "vless":
        return _parse_vless(raw)
    if scheme == "vmess":
        return _parse_vmess(raw)
    if scheme == "trojan":
        return _parse_trojan(raw)
    if scheme in {"hysteria2", "hy2"}:
        return _parse_hysteria2(raw)
    if scheme == "tuic":
        return _parse_tuic(raw)
    if scheme not in _SIMPLE:
        raise ParseError("invalid_scheme", f"scheme not implemented yet: {scheme}", scheme=scheme)

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


_VLESS_KNOWN = frozenset(
    {
        "type",
        "net",
        "path",
        "host",
        "security",
        "sni",
        "alpn",
        "fp",
        "pbk",
        "sid",
        "flow",
        "serviceName",
        "mode",
    }
)


def _parse_vless(raw: str) -> VlessServer:
    parts = urlsplit(raw)
    uuid = unquote(parts.username or "")
    if not uuid:
        raise ParseError("missing_field", "missing uuid", scheme="vless", field="uuid")
    if not _uuid_ok(uuid):
        raise ParseError("invalid_uuid", f"invalid uuid: {uuid}", scheme="vless", field="uuid")
    host = parts.hostname
    if not host:
        raise ParseError("missing_field", "missing host", scheme="vless", field="host")
    try:
        raw_port = parts.port
    except ValueError as exc:
        raise ParseError("invalid_port", str(exc), scheme="vless", field="port") from exc
    port = 443 if raw_port is None else raw_port
    params, extra = _split_query(parts.query, _VLESS_KNOWN)
    kind = params.get("type") or params.get("net") or "tcp"
    transport = _vless_transport(params)
    if kind not in {"tcp", "ws", "grpc", "h2", "quic", "httpupgrade"}:
        label = "type" if "type" in params else "net"
        extra = {label: kind, **extra}
    security = params.get("security", "none")
    tls = None
    reality = None
    if security == "reality":
        pbk = params.get("pbk", "")
        if not pbk:
            raise ParseError("missing_field", "missing pbk", scheme="vless", field="pbk")
        reality = RealityParams(
            public_key=pbk,
            short_id=params.get("sid"),
            sni=params.get("sni"),
            fingerprint=params.get("fp"),
        )
    elif security == "tls":
        alpn = tuple(part for part in params.get("alpn", "").split(",") if part)
        tls = TlsParams(sni=params.get("sni"), alpn=alpn, fingerprint=params.get("fp"))
    elif security != "none":
        extra = {"security": security, **extra}
    flow = params.get("flow") or None
    extra.update(_unstored_vless(params, transport, security, tls, reality, flow))
    return VlessServer(
        host=host,
        port=port,
        name=_name(parts.fragment, host, port),
        uuid=uuid,
        flow=flow,
        transport=transport,
        tls=tls,
        reality=reality,
        extra=extra,
    )


def _unstored_vless(
    params: dict[str, str],
    transport: TransportParams,
    security: str,
    tls: TlsParams | None,
    reality: RealityParams | None,
    flow: str | None,
) -> dict[str, str]:
    """Known query keys that were not copied onto the server."""
    used = {"type", "net"}
    if isinstance(transport, (WsTransport, H2Transport, HttpUpgradeTransport)):
        used.update({"path", "host"})
    if isinstance(transport, GrpcTransport):
        used.update({"serviceName", "mode"})
    if security in {"none", "tls", "reality"}:
        used.add("security")
    if reality is not None:
        used.update({"pbk", "sid", "sni", "fp"})
    if tls is not None:
        used.update({"sni", "alpn", "fp"})
    if flow:
        used.add("flow")
    return {key: value for key, value in params.items() if key not in used and value}


def _uuid_ok(value: str) -> bool:
    hexdigits = set("0123456789abcdefABCDEF")
    if len(value) == 32:
        return all(char in hexdigits for char in value)
    parts = value.split("-")
    lengths = (8, 4, 4, 4, 12)
    if len(parts) != 5:
        return False
    pairs = zip(parts, lengths, strict=True)
    return all(len(part) == size and all(c in hexdigits for c in part) for part, size in pairs)


def _split_query(query: str, known: frozenset[str]) -> tuple[dict[str, str], dict[str, str]]:
    params: dict[str, str] = {}
    extra: dict[str, str] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key in known and key not in params:
            params[key] = value
        else:
            extra[key] = value
    return params, extra


def _vless_transport(params: dict[str, str]) -> TransportParams:
    kind = params.get("type") or params.get("net") or "tcp"
    header_host = params.get("host")
    path = params.get("path") or "/"
    if kind == "ws":
        return WsTransport(path=path, host=header_host)
    if kind == "grpc":
        service = params.get("serviceName", "")
        return GrpcTransport(service_name=service, mode=params.get("mode") or "gun")
    if kind == "h2":
        return H2Transport(path=path, host=header_host)
    if kind == "quic":
        return QuicTransport()
    if kind == "httpupgrade":
        return HttpUpgradeTransport(path=path, host=header_host)
    return TcpTransport()


def _serialize_vless(server: VlessServer) -> str:
    query: list[tuple[str, str]] = []
    transport = server.transport
    if not isinstance(transport, TcpTransport):
        query.append(("type", transport.kind.value))
    if isinstance(transport, (WsTransport, H2Transport, HttpUpgradeTransport)):
        query.append(("path", transport.path))
        if transport.host:
            query.append(("host", transport.host))
    if isinstance(transport, GrpcTransport):
        query.append(("serviceName", transport.service_name))
        query.append(("mode", transport.mode))
    if server.reality is not None:
        query.append(("security", "reality"))
        query.append(("pbk", server.reality.public_key))
        if server.reality.short_id:
            query.append(("sid", server.reality.short_id))
        if server.reality.sni:
            query.append(("sni", server.reality.sni))
        if server.reality.fingerprint:
            query.append(("fp", server.reality.fingerprint))
    elif server.tls is not None:
        query.append(("security", "tls"))
        if server.tls.sni:
            query.append(("sni", server.tls.sni))
        if server.tls.alpn:
            query.append(("alpn", ",".join(server.tls.alpn)))
        if server.tls.fingerprint:
            query.append(("fp", server.tls.fingerprint))
    if server.flow:
        query.append(("flow", server.flow))
    query.extend(server.extra.items())
    host = f"[{server.host}]" if ":" in server.host else server.host
    netloc = f"{server.uuid}@{host}:{server.port}"
    fragment = quote(server.name, safe="")
    return urlunsplit(("vless", netloc, "", urlencode(query), fragment))


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


def _b64_text(value: str, scheme: str = "ss") -> str:
    alphabet = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_+/=")
    if not value or any(char not in alphabet for char in value):
        raise ParseError("invalid_base64", f"{scheme} payload is not base64", scheme=scheme)
    padded = value + ("=" * (-len(value) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode()).decode()
    except Exception as exc:
        raise ParseError(
            "invalid_base64",
            f"{scheme} payload is not base64",
            scheme=scheme,
        ) from exc


_VMESS_KNOWN = frozenset(
    {"v", "ps", "add", "port", "id", "aid", "net", "type", "host", "path", "tls", "sni", "scy"}
)


def _parse_trojan(raw: str) -> TrojanServer:
    parts = urlsplit(raw)
    password = _userinfo(parts)
    if not password:
        raise ParseError("missing_field", "missing password", scheme="trojan", field="password")
    host, port = _host_port(parts, "trojan", 443)
    params, extra = _split_query(parts.query, _VLESS_KNOWN)
    transport = _vless_transport(params)
    return TrojanServer(
        host=host,
        port=port,
        name=_name(parts.fragment, host, port),
        password=password,
        transport=transport,
        tls=_tls_from_params(params),
        extra=_with_unstored(params, extra, transport),
    )


def _parse_hysteria2(raw: str) -> Hysteria2Server:
    parts = urlsplit(raw)
    password = _userinfo(parts)
    if not password:
        raise ParseError("missing_field", "missing password", scheme="hysteria2", field="password")
    host, port = _host_port(parts, "hysteria2", 443)
    known = _VLESS_KNOWN | {"obfs-password", "congestion_control", "cc"}
    params, extra = _split_query(parts.query, known)
    cc = params.get("congestion_control") or params.get("cc")
    if params.get("cc") and params.get("congestion_control"):
        extra["cc"] = params["cc"]
    return Hysteria2Server(
        host=host,
        port=port,
        name=_name(parts.fragment, host, port),
        password=password,
        obfs_password=params.get("obfs-password"),
        congestion_control=cc,
        tls=_tls_from_params(params),
        extra=extra,
    )


def _parse_tuic(raw: str) -> TuicServer:
    parts = urlsplit(raw)
    uuid = unquote(parts.username or "")
    password = unquote(parts.password) if parts.password is not None else ""
    if not uuid:
        raise ParseError("missing_field", "missing uuid", scheme="tuic", field="uuid")
    if not _uuid_ok(uuid):
        raise ParseError("invalid_uuid", f"invalid uuid: {uuid}", scheme="tuic", field="uuid")
    if not password:
        raise ParseError("missing_field", "missing password", scheme="tuic", field="password")
    host, port = _host_port(parts, "tuic", 443)
    known = _VLESS_KNOWN | {"congestion_control"}
    params, extra = _split_query(parts.query, known)
    return TuicServer(
        host=host,
        port=port,
        name=_name(parts.fragment, host, port),
        uuid=uuid,
        password=password,
        congestion_control=params.get("congestion_control"),
        tls=_tls_from_params(params),
        extra=extra,
    )


def _host_port(parts: SplitResult, scheme: str, default: int) -> tuple[str, int]:
    host = parts.hostname
    if not host:
        raise ParseError("missing_field", "missing host", scheme=scheme, field="host")
    try:
        raw_port = parts.port
    except ValueError as exc:
        raise ParseError("invalid_port", str(exc), scheme=scheme, field="port") from exc
    port = default if raw_port is None else raw_port
    if not 1 <= port <= 65535:
        raise ParseError("invalid_port", f"invalid port: {port}", scheme=scheme, field="port")
    return host, port


def _userinfo(parts: SplitResult) -> str:
    """Whole userinfo, including colons. ``urlsplit`` stops the username at the first colon."""
    netloc = parts.netloc
    at = netloc.rfind("@")
    if at < 0:
        return ""
    return unquote(netloc[:at])


def _tls_from_params(params: dict[str, str]) -> TlsParams:
    alpn = tuple(part for part in params.get("alpn", "").split(",") if part)
    return TlsParams(sni=params.get("sni") or None, alpn=alpn, fingerprint=params.get("fp") or None)


def _with_unstored(
    params: dict[str, str],
    extra: dict[str, str],
    transport: TransportParams,
) -> dict[str, str]:
    used = {"type", "net", "sni", "alpn", "fp", "security"}
    if isinstance(transport, (WsTransport, H2Transport, HttpUpgradeTransport)):
        used.update({"path", "host"})
    if isinstance(transport, GrpcTransport):
        used.update({"serviceName", "mode"})
    kept = {key: value for key, value in params.items() if key not in used and value}
    return {**extra, **kept}


def _serialize_user_uri(
    scheme: str,
    user: str,
    server: TrojanServer | Hysteria2Server | TuicServer,
    *,
    encoded: bool = False,
) -> str:
    auth = user if encoded else quote(user, safe="")
    host = f"[{server.host}]" if ":" in server.host else server.host
    query: list[tuple[str, str]] = []
    transport = getattr(server, "transport", None)
    framed = (WsTransport, H2Transport, HttpUpgradeTransport, GrpcTransport, QuicTransport)
    if isinstance(transport, framed):
        query.append(("type", transport.kind.value))
    if isinstance(transport, (WsTransport, H2Transport, HttpUpgradeTransport)):
        query.append(("path", transport.path))
        if transport.host:
            query.append(("host", transport.host))
    if isinstance(transport, GrpcTransport):
        query.append(("serviceName", transport.service_name))
        query.append(("mode", transport.mode))
    tls = server.tls
    if tls.sni:
        query.append(("sni", tls.sni))
    if tls.alpn:
        query.append(("alpn", ",".join(tls.alpn)))
    if tls.fingerprint:
        query.append(("fp", tls.fingerprint))
    obfs = getattr(server, "obfs_password", None)
    if isinstance(obfs, str):
        query.append(("obfs-password", obfs))
    cc = getattr(server, "congestion_control", None)
    if isinstance(cc, str):
        query.append(("congestion_control", cc))
    query.extend(server.extra.items())
    fragment = quote(server.name, safe="")
    return urlunsplit((scheme, f"{auth}@{host}:{server.port}", "", urlencode(query), fragment))


def _parse_vmess(raw: str) -> VmessServer:
    body, _, fragment = raw.split("://", 1)[1].partition("#")
    payload = unquote(body.strip())
    try:
        data = json.loads(_b64_text(payload, "vmess"))
    except json.JSONDecodeError as exc:
        raise ParseError("invalid_json", "vmess payload is not JSON", scheme="vmess") from exc
    if not isinstance(data, dict):
        raise ParseError("invalid_json", "vmess payload is not a JSON object", scheme="vmess")
    host = str(data.get("add") or "")
    if not host:
        raise ParseError("missing_field", "missing add", scheme="vmess", field="add")
    uuid = str(data.get("id") or "")
    if not uuid:
        raise ParseError("missing_field", "missing id", scheme="vmess", field="id")
    if not _uuid_ok(uuid):
        raise ParseError("invalid_uuid", f"invalid uuid: {uuid}", scheme="vmess", field="id")
    if "port" not in data or data["port"] in ("", None):
        raise ParseError("missing_field", "missing port", scheme="vmess", field="port")
    try:
        port = int(data["port"])
    except (TypeError, ValueError) as exc:
        raise ParseError("invalid_port", "invalid port", scheme="vmess", field="port") from exc
    if not 1 <= port <= 65535:
        raise ParseError("invalid_port", f"invalid port: {port}", scheme="vmess", field="port")
    aid = data.get("aid", 0)
    try:
        alter_id = int(aid)
    except (TypeError, ValueError) as exc:
        raise ParseError("invalid_field", "invalid aid", scheme="vmess", field="aid") from exc
    net = str(data.get("net") or "tcp")
    header = str(data.get("type") or "")
    path = str(data.get("path") or "")
    header_host = str(data.get("host") or "") or None
    transport = _vmess_transport(net, header, path, header_host)
    tls = None
    if str(data.get("tls") or "") == "tls":
        sni = str(data.get("sni") or "").strip()
        tls = TlsParams(sni=sni or None)
    ps = str(data.get("ps") or "").strip()
    name = _name(fragment, host, port) if fragment else (ps or _name("", host, port))
    extra = {key: _json_text(value) for key, value in data.items() if key not in _VMESS_KNOWN}
    if net not in {"tcp", "ws", "grpc", "h2", "quic", "httpupgrade"}:
        extra["net"] = net
    if header and not isinstance(transport, GrpcTransport):
        extra["type"] = header
    framed = (WsTransport, H2Transport, HttpUpgradeTransport, GrpcTransport)
    if path and not isinstance(transport, framed):
        extra["path"] = path
    if header_host and not isinstance(transport, (WsTransport, H2Transport, HttpUpgradeTransport)):
        extra["host"] = header_host
    if "v" in data:
        extra["v"] = _json_text(data["v"])
    return VmessServer(
        host=host,
        port=port,
        name=name,
        uuid=uuid,
        alter_id=alter_id,
        security=str(data.get("scy") or "auto"),
        transport=transport,
        tls=tls,
        extra=extra,
    )


def _vmess_transport(net: str, header: str, path: str, header_host: str | None) -> TransportParams:
    if net == "ws":
        return WsTransport(path=path or "/", host=header_host)
    if net == "h2":
        return H2Transport(path=path or "/", host=header_host)
    if net == "httpupgrade":
        return HttpUpgradeTransport(path=path or "/", host=header_host)
    if net == "grpc":
        return GrpcTransport(service_name=path, mode=header or "gun")
    if net == "quic":
        return QuicTransport()
    return TcpTransport()


def _json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _serialize_vmess(server: VmessServer) -> str:
    transport = server.transport
    body: dict[str, object] = {
        "ps": server.name,
        "add": server.host,
        "port": str(server.port),
        "id": server.uuid,
        "aid": str(server.alter_id),
        "scy": server.security,
        "net": transport.kind.value,
    }
    if isinstance(transport, (WsTransport, H2Transport, HttpUpgradeTransport)):
        body["path"] = transport.path
        body["host"] = transport.host or ""
    if isinstance(transport, GrpcTransport):
        body["path"] = transport.service_name
        body["type"] = transport.mode
    body["tls"] = "tls" if server.tls is not None else ""
    if server.tls is not None and server.tls.sni:
        body["sni"] = server.tls.sni
    for key, value in server.extra.items():
        if key not in body:
            body[key] = value
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    token = base64.urlsafe_b64encode(text.encode())
    return "vmess://" + token.decode().rstrip("=")


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
