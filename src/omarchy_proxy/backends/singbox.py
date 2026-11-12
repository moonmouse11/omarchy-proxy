"""sing-box core: config generation, version floor, and ``sing-box check``."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from omarchy_proxy.backends.base import BackendError, BackendSettings
from omarchy_proxy.model import (
    GrpcTransport,
    H2Transport,
    HttpProxyServer,
    HttpUpgradeTransport,
    Hysteria2Server,
    Server,
    ShadowsocksServer,
    Socks5Server,
    TlsParams,
    TrojanServer,
    TuicServer,
    VlessServer,
    VmessServer,
    WsTransport,
)

MIN_VERSION = (1, 10, 0)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class SingBoxBackend:
    """Generate and check a sing-box JSON config."""

    name = "sing-box"
    binary_candidates = ("sing-box",)

    def generate_config(self, server: Server, settings: BackendSettings) -> dict[str, Any]:
        outbounds: list[dict[str, Any]] = [
            {**_outbound(server), "tag": "proxy"},
            {"type": "direct", "tag": "direct"},
        ]
        rules = [] if settings.get("tun") else [_health_rule(settings["health_url"])]
        config: dict[str, Any] = {
            "inbounds": _inbounds(settings),
            "outbounds": outbounds,
            "route": {"rules": rules, "final": "proxy"},
        }
        if settings.get("tun"):
            config["inbounds"] = [_tun_inbound(), *config["inbounds"]]
            rules.insert(0, {"protocol": "dns", "action": "hijack-dns"})
            config["dns"] = {
                "servers": [
                    {
                        "type": "tls",
                        "tag": "remote",
                        "server": "1.1.1.1",
                        "server_port": 853,
                        "detour": "proxy",
                    }
                ],
                "rules": [{"port": 53, "server": "remote"}],
                "final": "remote",
            }
            config["route"]["auto_detect_interface"] = True
        return config

    def validate_config(self, path: Path) -> None:
        binary = self._binary()
        result = subprocess.run(  # noqa: S603
            [binary, "check", "-c", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout)[:4096]
            raise BackendError("invalid_config", detail or "sing-box rejected the config")

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
            raise BackendError("backend_missing", "sing-box version failed")
        found = _parse_version(result.stdout)
        if found is None or found < MIN_VERSION:
            shown = ".".join(str(part) for part in found) if found else result.stdout.strip()
            raise BackendError(
                "backend_too_old",
                f"sing-box {shown} is older than {'.'.join(map(str, MIN_VERSION))}",
            )
        return ".".join(str(part) for part in found)

    def _binary(self) -> str:
        for name in self.binary_candidates:
            found = shutil.which(name)
            if found:
                return found
        raise BackendError("backend_missing", "sing-box is not installed")


def _parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _tun_inbound() -> dict[str, Any]:
    return {
        "type": "tun",
        "tag": "tun-in",
        "interface_name": "omarchy-tun",
        "address": ["172.19.0.1/30", "fdfe:dcba:9876::1/126"],
        "auto_route": True,
        "auto_redirect": True,
        "strict_route": True,
        "stack": "system",
    }


def _inbounds(settings: BackendSettings) -> list[dict[str, Any]]:
    socks = settings["socks_port"]
    http = settings["http_port"]
    if socks == http:
        return [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": socks}]
    return [
        {"type": "socks", "tag": "socks-in", "listen": "127.0.0.1", "listen_port": socks},
        {"type": "http", "tag": "http-in", "listen": "127.0.0.1", "listen_port": http},
    ]


def _health_rule(url: str) -> dict[str, Any]:
    host = urlsplit(url).hostname or ""
    if host.count(".") == 3 and all(part.isdigit() for part in host.split(".")):
        return {"ip_cidr": [f"{host}/32"], "outbound": "direct"}
    return {"domain": [host], "outbound": "direct"}


def _outbound(server: Server) -> dict[str, Any]:
    base: dict[str, Any] = {"server": server.host, "server_port": server.port}
    if isinstance(server, Socks5Server):
        base["type"] = "socks"
        base["version"] = "5"
        if server.user is not None:
            base["username"] = server.user
            base["password"] = server.password or ""
        return base
    if isinstance(server, HttpProxyServer):
        base["type"] = "http"
        if server.user is not None:
            base["username"] = server.user
            base["password"] = server.password or ""
        if server.use_tls:
            base["tls"] = _tls(server.tls, server.host)
        return base
    if isinstance(server, ShadowsocksServer):
        base["type"] = "shadowsocks"
        base["method"] = server.method
        base["password"] = server.password
        return base
    if isinstance(server, VlessServer):
        base["type"] = "vless"
        base["uuid"] = server.uuid
        if server.flow:
            base["flow"] = server.flow
        _attach(base, server)
        return base
    if isinstance(server, VmessServer):
        base["type"] = "vmess"
        base["uuid"] = server.uuid
        base["alter_id"] = server.alter_id
        base["security"] = server.security
        _attach(base, server)
        return base
    if isinstance(server, TrojanServer):
        base["type"] = "trojan"
        base["password"] = server.password
        _attach(base, server)
        return base
    if isinstance(server, Hysteria2Server):
        base["type"] = "hysteria2"
        base["password"] = server.password
        if server.obfs_password:
            base["obfs"] = {"type": "salamander", "password": server.obfs_password}
        base["tls"] = _tls(server.tls, server.host)
        return base
    if isinstance(server, TuicServer):
        base["type"] = "tuic"
        base["uuid"] = server.uuid
        base["password"] = server.password
        if server.congestion_control:
            base["congestion_control"] = server.congestion_control
        base["tls"] = _tls(server.tls, server.host)
        return base
    raise BackendError("unsupported_protocol", "this server has no sing-box outbound")


def _attach(base: dict[str, Any], server: VlessServer | VmessServer | TrojanServer) -> None:
    transport = _transport(server)
    if transport is not None:
        base["transport"] = transport
    if isinstance(server, VlessServer) and server.reality is not None:
        base["tls"] = _tls(None, server.host, server.reality.sni, server.reality.fingerprint)
        base["tls"]["reality"] = {
            "enabled": True,
            "public_key": server.reality.public_key,
            "short_id": server.reality.short_id or "",
        }
        return
    if server.tls is not None:
        base["tls"] = _tls(server.tls, server.host)


def _transport(server: VlessServer | VmessServer | TrojanServer) -> dict[str, Any] | None:
    transport = server.transport
    if isinstance(transport, WsTransport):
        block: dict[str, Any] = {"type": "ws", "path": transport.path}
        if transport.host:
            block["headers"] = {"Host": transport.host}
        return block
    if isinstance(transport, GrpcTransport):
        return {"type": "grpc", "service_name": transport.service_name}
    if isinstance(transport, (H2Transport, HttpUpgradeTransport)):
        kind = "http" if isinstance(transport, H2Transport) else "httpupgrade"
        block = {"type": kind, "path": transport.path}
        if transport.host:
            block["host"] = [transport.host]
        return block
    return None


def _tls(
    tls: TlsParams | None,
    host: str,
    sni: str | None = None,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "enabled": True,
        "server_name": (tls.sni if tls else None) or sni or host,
    }
    if tls and tls.alpn:
        block["alpn"] = list(tls.alpn)
    finger = (tls.fingerprint if tls else None) or fingerprint
    if finger:
        block["utls"] = {"enabled": True, "fingerprint": finger}
    return block
