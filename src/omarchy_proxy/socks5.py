"""Minimal SOCKS5 client: no-auth or username/password, CONNECT only."""

from __future__ import annotations

import ipaddress
import socket

_ERRORS = {
    1: "general_failure",
    2: "not_allowed",
    3: "network_unreachable",
    4: "host_unreachable",
    5: "connection_refused",
    6: "ttl_expired",
    7: "command_not_supported",
    8: "address_not_supported",
}


class SocksError(Exception):
    """The proxy refused the handshake or the connect."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def connect(
    proxy_host: str,
    proxy_port: int,
    dest_host: str,
    dest_port: int,
    *,
    username: str | None = None,
    password: str | None = None,
    timeout: float = 10,
) -> socket.socket:
    """Open a TCP connection to ``dest_host`` through a SOCKS5 proxy."""
    sock = socket.create_connection((proxy_host, proxy_port), timeout)
    try:
        _greet(sock, username, password)
        _command(sock, dest_host, dest_port)
    except Exception:
        sock.close()
        raise
    return sock


def _greet(sock: socket.socket, username: str | None, password: str | None) -> None:
    if username is None:
        sock.sendall(b"\x05\x01\x00")
        reply = _recv(sock, 2)
        if reply != b"\x05\x00":
            raise SocksError("auth_rejected", "proxy refused no-auth")
        return
    sock.sendall(b"\x05\x01\x02")
    reply = _recv(sock, 2)
    if reply != b"\x05\x02":
        raise SocksError("auth_rejected", "proxy refused username/password")
    user = username.encode()
    secret = (password or "").encode()
    if not 1 <= len(user) <= 255 or len(secret) > 255:
        raise SocksError("invalid_field", "username or password has a bad length")
    sock.sendall(bytes([1, len(user)]) + user + bytes([len(secret)]) + secret)
    status = _recv(sock, 2)
    if status[1] != 0:
        raise SocksError("auth_failed", "username or password was rejected")


def _command(sock: socket.socket, host: str, port: int) -> None:
    sock.sendall(b"\x05\x01\x00" + _address(host) + port.to_bytes(2, "big"))
    head = _recv(sock, 4)
    if head[1] != 0:
        code = _ERRORS.get(head[1], "general_failure")
        raise SocksError(code, f"socks connect failed ({head[1]})")
    if head[3] == 1:
        _recv(sock, 6)
    elif head[3] == 4:
        _recv(sock, 18)
    elif head[3] == 3:
        _recv(sock, _recv(sock, 1)[0] + 2)
    else:
        raise SocksError("address_not_supported", "proxy returned an unknown address type")


def _address(host: str) -> bytes:
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        encoded = host.encode("idna")
        if not 1 <= len(encoded) <= 255:
            raise SocksError("invalid_field", "destination host is too long") from None
        return b"\x03" + bytes([len(encoded)]) + encoded
    if isinstance(parsed, ipaddress.IPv4Address):
        return b"\x01" + parsed.packed
    return b"\x04" + parsed.packed


def _recv(sock: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        piece = sock.recv(count - len(chunks))
        if not piece:
            raise SocksError("connection_closed", "proxy closed the connection")
        chunks += piece
    return bytes(chunks)
