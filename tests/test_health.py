"""Health state changes and the round-trip timeout."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from omarchy_proxy.health import Health, HealthError, round_trip


def test_states_follow_port_and_round_trip() -> None:
    health = Health()
    assert health.state == "unknown"
    health.port(True, now=0)
    assert health.state == "starting"
    health.port(False, now=1)
    assert health.state == "failed"
    health.port(True, now=2)
    assert health.state == "starting"
    health.round_trip_result(False, "http_status")
    health.round_trip_result(False, "http_status")
    assert health.state == "starting"
    health.round_trip_result(False, "timeout")
    assert health.state == "degraded"
    assert health.last_error == "timeout"
    health.round_trip_result(True)
    assert health.state == "ok"
    assert health.last_error is None
    health.mark_stopped()
    assert health.state == "stopped"


def test_round_trip_rejects_a_reply_without_a_status_code() -> None:
    port = _reply(b"not http\r\n\r\n")
    with pytest.raises(HealthError) as exc:
        round_trip(port, "http://example.test/generate_204", timeout=1)
    assert exc.value.code == "empty_reply"


def test_round_trip_timeout() -> None:
    port = _hang()
    with pytest.raises(HealthError) as exc:
        round_trip(port, "http://example.test/generate_204", timeout=0.2)
    assert exc.value.code == "timeout"


def _reply(payload: bytes) -> int:
    ready = threading.Event()
    holder: dict[str, int] = {}

    def run() -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        holder["port"] = server.getsockname()[1]
        ready.set()
        conn, _address = server.accept()
        conn.recv(16)
        conn.sendall(b"\x05\x00")
        conn.recv(64)
        conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        conn.recv(128)
        conn.sendall(payload)
        conn.close()
        server.close()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(1)
    return holder["port"]


def _hang() -> int:
    ready = threading.Event()
    holder: dict[str, int] = {}

    def run() -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        holder["port"] = server.getsockname()[1]
        ready.set()
        conn, _address = server.accept()
        conn.recv(16)
        conn.sendall(b"\x05\x00")
        conn.recv(64)
        conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        time.sleep(2)
        conn.close()
        server.close()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(1)
    return holder["port"]
