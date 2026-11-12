"""One proxied HTTP request per protocol, using the real xray and sing-box binaries."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from omarchy_proxy.backends.singbox import SingBoxBackend
from omarchy_proxy.backends.xray import XrayBackend
from omarchy_proxy.model import HttpProxyServer as HttpServer
from omarchy_proxy.model import (
    Hysteria2Server,
    ShadowsocksServer,
    Socks5Server,
    TlsParams,
    TrojanServer,
    TuicServer,
    VlessServer,
    VmessServer,
)
from omarchy_proxy.socks5 import SocksError, connect

UUID = "01234567-89ab-cdef-0123-456789abcdef"
WORK = Path("/tmp/omarchy-proxy-docker")
CERT = WORK / "cert.pem"
KEY = WORK / "key.pem"
ORIGIN = 19080


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"pong"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def wait_port(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"port {port} did not open")


def write_config(name: str, payload: dict) -> Path:
    path = WORK / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def spawn(argv: list[str]) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["SSL_CERT_FILE"] = str(WORK / "ca.pem")
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def fetch(socks_port: int) -> str:
    try:
        sock = connect("127.0.0.1", socks_port, "127.0.0.1", ORIGIN, timeout=5)
    except SocksError as exc:
        raise RuntimeError(exc.message) from exc
    try:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        data = b""
        while chunk := sock.recv(4096):
            data += chunk
    finally:
        sock.close()
    if not data.endswith(b"pong"):
        raise RuntimeError(data.decode(errors="replace"))
    return "pong"


def xray_server(name: str, inbound: dict) -> subprocess.Popen[bytes]:
    config = write_config(
        f"{name}-server.json",
        {"inbounds": [inbound], "outbounds": [{"protocol": "freedom"}]},
    )
    return spawn(["xray", "run", "-config", str(config)])


def singbox_server(name: str, inbound: dict) -> subprocess.Popen[bytes]:
    config = write_config(
        f"{name}-server.json",
        {"inbounds": [inbound], "outbounds": [{"type": "direct"}]},
    )
    return spawn(["sing-box", "run", "-c", str(config)])


def client(name: str, backend: XrayBackend | SingBoxBackend, server) -> subprocess.Popen[bytes]:
    settings = {
        "socks_port": 19100 + hash(name) % 500,
        "http_port": 19500,
        "health_url": "https://example.com/",
    }
    payload = backend.generate_config(server, settings)
    config = write_config(f"{name}-client.json", payload)
    if isinstance(backend, XrayBackend):
        process = spawn(["xray", "run", "-config", str(config)])
    else:
        process = spawn(["sing-box", "run", "-c", str(config)])
    process.socks_port = settings["socks_port"]  # type: ignore[attr-defined]
    return process


def check(
    name: str,
    process: subprocess.Popen[bytes],
    server_process: subprocess.Popen[bytes],
) -> None:
    try:
        wait_port(process.socks_port)  # type: ignore[attr-defined]
        body = fetch(process.socks_port)  # type: ignore[attr-defined]
    except Exception:
        process.kill()
        server_process.kill()
        _out = process.stderr.read().decode() if process.stderr else ""
        _server = server_process.stderr.read().decode() if server_process.stderr else ""
        raise RuntimeError(f"{name} failed\nclient: {_out}\nserver: {_server}") from None
    if body != "pong":
        raise RuntimeError(f"{name} returned {body!r}")
    process.kill()
    server_process.kill()
    process.wait(timeout=5)
    server_process.wait(timeout=5)


def main() -> None:
    WORK.mkdir(exist_ok=True)
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(KEY),
            "-out",
            str(CERT),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    bundle = Path("/etc/ssl/certs/ca-certificates.crt").read_bytes()
    (WORK / "ca.pem").write_bytes(bundle + b"\n" + CERT.read_bytes())
    httpd = ThreadingHTTPServer(("127.0.0.1", ORIGIN), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    tls = TlsParams(sni="localhost")
    xray = XrayBackend()
    sing = SingBoxBackend()
    cases = [
        (
            "socks",
            xray,
            Socks5Server("127.0.0.1", 20001, "socks"),
            xray_server(
                "socks",
                {
                    "listen": "127.0.0.1",
                    "port": 20001,
                    "protocol": "socks",
                    "settings": {"udp": True},
                },
            ),
        ),
        (
            "http",
            xray,
            HttpServer("127.0.0.1", 20002, "http"),
            xray_server("http", {"listen": "127.0.0.1", "port": 20002, "protocol": "http"}),
        ),
        (
            "ss",
            xray,
            ShadowsocksServer("127.0.0.1", 20003, "ss", method="aes-128-gcm", password="secret"),
            xray_server(
                "ss",
                {
                    "listen": "127.0.0.1",
                    "port": 20003,
                    "protocol": "shadowsocks",
                    "settings": {"method": "aes-128-gcm", "password": "secret"},
                },
            ),
        ),
        (
            "vless",
            xray,
            VlessServer("127.0.0.1", 20004, "vless", uuid=UUID),
            xray_server(
                "vless",
                {
                    "listen": "127.0.0.1",
                    "port": 20004,
                    "protocol": "vless",
                    "settings": {"clients": [{"id": UUID}], "decryption": "none"},
                },
            ),
        ),
        (
            "vmess",
            xray,
            VmessServer("127.0.0.1", 20005, "vmess", uuid=UUID, security="aes-128-gcm"),
            xray_server(
                "vmess",
                {
                    "listen": "127.0.0.1",
                    "port": 20005,
                    "protocol": "vmess",
                    "settings": {
                        "clients": [{"id": UUID, "alterId": 0, "security": "aes-128-gcm"}]
                    },
                },
            ),
        ),
        (
            "trojan",
            xray,
            TrojanServer("127.0.0.1", 20006, "trojan", password="secret", tls=tls),
            xray_server(
                "trojan",
                {
                    "listen": "127.0.0.1",
                    "port": 20006,
                    "protocol": "trojan",
                    "settings": {"clients": [{"password": "secret"}]},
                    "streamSettings": {
                        "network": "tcp",
                        "security": "tls",
                        "tlsSettings": {
                            "certificates": [{"certificateFile": str(CERT), "keyFile": str(KEY)}]
                        },
                    },
                },
            ),
        ),
        (
            "hysteria2",
            sing,
            Hysteria2Server("127.0.0.1", 20007, "hy2", password="secret", tls=tls),
            singbox_server(
                "hysteria2",
                {
                    "type": "hysteria2",
                    "listen": "127.0.0.1",
                    "listen_port": 20007,
                    "users": [{"password": "secret"}],
                    "tls": {"enabled": True, "certificate_path": str(CERT), "key_path": str(KEY)},
                },
            ),
        ),
        (
            "tuic",
            sing,
            TuicServer("127.0.0.1", 20008, "tuic", uuid=UUID, password="secret", tls=tls),
            singbox_server(
                "tuic",
                {
                    "type": "tuic",
                    "listen": "127.0.0.1",
                    "listen_port": 20008,
                    "users": [{"uuid": UUID, "password": "secret"}],
                    "tls": {"enabled": True, "certificate_path": str(CERT), "key_path": str(KEY)},
                },
            ),
        ),
    ]
    try:
        for name, backend, server, upstream in cases:
            proxy = client(name, backend, server)
            check(name, proxy, upstream)
            print(name, "ok")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
