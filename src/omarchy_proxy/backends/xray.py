"""Xray core: config generation, version floor, and ``xray run -test``."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from omarchy_proxy.backends.base import BackendSettings
from omarchy_proxy.model import (
    GrpcTransport,
    H2Transport,
    HttpProxyServer,
    HttpUpgradeTransport,
    Hysteria2Server,
    Server,
    ShadowsocksServer,
    Socks5Server,
    TcpTransport,
    TrojanServer,
    TuicServer,
    VlessServer,
    VmessServer,
    WsTransport,
)

MIN_VERSION = (1, 8, 0)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class BackendError(Exception):
    """The selected core cannot be used."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class XrayBackend:
    """Generate and check an xray JSON config."""

    name = "xray"
    binary_candidates = ("xray",)

    def generate_config(self, server: Server, settings: BackendSettings) -> dict[str, Any]:
        if isinstance(server, (Hysteria2Server, TuicServer)):
            raise BackendError(
                "unsupported_protocol",
                f"{server.PROTOCOL.value} is not generated for xray",
            )
        return {
            "inbounds": [
                {
                    "tag": "socks-in",
                    "listen": "127.0.0.1",
                    "port": settings["socks_port"],
                    "protocol": "socks",
                    "settings": {"udp": True},
                },
                {
                    "tag": "http-in",
                    "listen": "127.0.0.1",
                    "port": settings["http_port"],
                    "protocol": "http",
                },
            ],
            "outbounds": [
                {**_outbound(server), "tag": "proxy"},
                {"protocol": "freedom", "tag": "direct"},
            ],
            "routing": {
                "rules": [
                    _health_rule(settings["health_url"]),
                    {"type": "field", "network": "tcp,udp", "outboundTag": "proxy"},
                ]
            },
        }

    def validate_config(self, path: Path) -> None:
        binary = self._binary()
        result = subprocess.run(  # noqa: S603
            [binary, "run", "-test", "-config", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout)[:4096]
            raise BackendError("invalid_config", detail or "xray rejected the config")

    def version(self) -> str:
        binary = self._binary()
        result = subprocess.run(  # noqa: S603
            [binary, "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise BackendError("backend_missing", "xray version failed")
        found = _parse_version(result.stdout)
        if found is None or found < MIN_VERSION:
            shown = ".".join(str(part) for part in found) if found else result.stdout.strip()
            raise BackendError(
                "backend_too_old",
                f"xray {shown} is older than {'.'.join(map(str, MIN_VERSION))}",
            )
        return ".".join(str(part) for part in found)

    def _binary(self) -> str:
        for name in self.binary_candidates:
            found = shutil.which(name)
            if found:
                return found
        raise BackendError("backend_missing", "xray is not installed")


def _parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _health_rule(url: str) -> dict[str, Any]:
    host = urlsplit(url).hostname or ""
    rule: dict[str, Any] = {"type": "field", "outboundTag": "direct"}
    if host.count(".") == 3 and all(part.isdigit() for part in host.split(".")):
        rule["ip"] = [host]
    else:
        rule["domain"] = [f"full:{host}"]
    return rule


def _outbound(server: Server) -> dict[str, Any]:
    if isinstance(server, Socks5Server):
        entry: dict[str, Any] = {"address": server.host, "port": server.port}
        if server.user is not None:
            entry["users"] = [{"user": server.user, "pass": server.password or ""}]
        return {"protocol": "socks", "settings": {"servers": [entry]}}
    if isinstance(server, HttpProxyServer):
        entry = {"address": server.host, "port": server.port}
        if server.user is not None:
            entry["users"] = [{"user": server.user, "pass": server.password or ""}]
        outbound: dict[str, Any] = {"protocol": "http", "settings": {"servers": [entry]}}
        if server.use_tls:
            outbound["streamSettings"] = _stream(server)
        return outbound
    if isinstance(server, ShadowsocksServer):
        return {
            "protocol": "shadowsocks",
            "settings": {
                "servers": [
                    {
                        "address": server.host,
                        "port": server.port,
                        "method": server.method,
                        "password": server.password,
                    }
                ]
            },
        }
    if isinstance(server, VlessServer):
        user: dict[str, Any] = {"id": server.uuid, "encryption": "none"}
        if server.flow:
            user["flow"] = server.flow
        return {
            "protocol": "vless",
            "settings": {"vnext": [{"address": server.host, "port": server.port, "users": [user]}]},
            "streamSettings": _stream(server),
        }
    if isinstance(server, VmessServer):
        return {
            "protocol": "vmess",
            "settings": {
                "vnext": [
                    {
                        "address": server.host,
                        "port": server.port,
                        "users": [
                            {
                                "id": server.uuid,
                                "alterId": server.alter_id,
                                "security": server.security,
                            }
                        ],
                    }
                ]
            },
            "streamSettings": _stream(server),
        }
    if isinstance(server, TrojanServer):
        return {
            "protocol": "trojan",
            "settings": {
                "servers": [
                    {"address": server.host, "port": server.port, "password": server.password}
                ]
            },
            "streamSettings": _stream(server),
        }
    raise BackendError("unsupported_protocol", "this server has no xray outbound")


def _stream(server: VlessServer | VmessServer | TrojanServer | HttpProxyServer) -> dict[str, Any]:
    transport = getattr(server, "transport", TcpTransport())
    kind = transport.kind.value
    stream: dict[str, Any] = {"network": kind}
    if isinstance(transport, WsTransport):
        headers = {"Host": transport.host} if transport.host else {}
        stream["wsSettings"] = {"path": transport.path, "headers": headers}
    elif isinstance(transport, GrpcTransport):
        stream["grpcSettings"] = {
            "serviceName": transport.service_name,
            "multiMode": transport.mode != "gun",
        }
    elif isinstance(transport, (H2Transport, HttpUpgradeTransport)):
        block = "httpSettings" if isinstance(transport, H2Transport) else "httpupgradeSettings"
        host = transport.host
        stream[block] = {"path": transport.path, "host": [host] if host else []}
    if isinstance(server, VlessServer) and server.reality is not None:
        reality = server.reality
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": reality.sni or "",
            "fingerprint": reality.fingerprint or "",
            "publicKey": reality.public_key,
            "shortId": reality.short_id or "",
        }
        return stream
    tls = getattr(server, "tls", None)
    if tls is not None or getattr(server, "use_tls", False):
        stream["security"] = "tls"
        stream["tlsSettings"] = {
            "serverName": (tls.sni if tls else None) or server.host,
            "fingerprint": (tls.fingerprint if tls else None) or "",
            "alpn": list(tls.alpn) if tls else [],
        }
    else:
        stream["security"] = "none"
    return stream
