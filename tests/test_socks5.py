"""SOCKS5 client against a local stub proxy."""

from __future__ import annotations

import socket
import threading

import pytest

from omarchy_proxy.socks5 import SocksError, connect


def test_connect_no_auth_hostname_ipv4_and_ipv6() -> None:
    seen: list[bytes] = []
    port = _serve(seen, auth=None, reply=0)
    for host in ("example.test", "10.0.0.8", "2001:db8::1"):
        sock = connect("127.0.0.1", port, host, 80)
        sock.close()
    assert seen[0][0] == 3
    assert seen[1][0] == 1
    assert seen[2][0] == 4
    port = _serve(seen, auth=None, reply=0)
    sock = connect("127.0.0.1", port, "bücher.de", 80)
    sock.close()
    assert b"xn--bcher-kva.de" in seen[-1]


def test_username_password_is_offered() -> None:
    seen: list[bytes] = []
    port = _serve(seen, auth=("user", "secret"), reply=0)
    sock = connect("127.0.0.1", port, "example.test", 443, username="user", password="secret")
    sock.close()
    assert seen[0] == b"\x01\x04user\x06secret"


def test_bad_password_is_auth_failed() -> None:
    port = _serve([], auth=("user", "secret"), reply=0, reject_auth=True)
    with pytest.raises(SocksError) as exc:
        connect("127.0.0.1", port, "example.test", 80, username="user", password="nope")
    assert exc.value.code == "auth_failed"


def test_connect_error_code_is_kept() -> None:
    port = _serve([], auth=None, reply=5)
    with pytest.raises(SocksError) as exc:
        connect("127.0.0.1", port, "example.test", 80)
    assert exc.value.code == "connection_refused"


def _serve(
    seen: list[bytes],
    *,
    auth: tuple[str, str] | None,
    reply: int,
    reject_auth: bool = False,
) -> int:
    ready = threading.Event()
    holder: dict[str, int] = {}

    def run() -> None:
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        holder["port"] = server.getsockname()[1]
        ready.set()
        for _ in range(4 if auth is None and not reject_auth else 1):
            conn, _address = server.accept()
            with conn:
                conn.recv(16)
                if auth is None:
                    conn.sendall(b"\x05\x00")
                else:
                    conn.sendall(b"\x05\x02")
                    blob = conn.recv(64)
                    seen.append(blob)
                    conn.sendall(b"\x01\x01" if reject_auth else b"\x01\x00")
                    if reject_auth:
                        continue
                request = conn.recv(64)
                seen.append(request[3:])
                conn.sendall(b"\x05" + bytes([reply]) + b"\x00\x01" + b"\x00" * 6)
        server.close()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(1)
    return holder["port"]
