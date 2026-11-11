"""Health, restart, stop, logs, ports, and the systemd fallback."""

from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from omarchy_proxy.supervisor import LOG_LIMIT, Supervisor, SupervisorError, unit_text

UUID = "01234567-89ab-cdef-0123-456789abcdef"


def _stop(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        return


def _exit_cmd() -> list[str]:
    return [sys.executable, "-c", "import sys; sys.exit(1)"]


def _sleep_cmd() -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(30)"]


def test_three_tcp_failures_schedule_one_restart(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path)
    state = supervisor.start(_sleep_cmd())
    original = state["pid"]
    try:
        for step in range(3):
            state = supervisor.tick(float(step * 5), socks_port=1)
        assert state["health"] == "failed"
        assert state["state"] == "starting"
        assert state["restarts"] == 1
        restarted = supervisor.tick(float(state["next_restart_at"]), socks_port=1)
        assert restarted["state"] == "running"
        assert restarted["pid"] != original
        _stop(restarted["pid"])
    finally:
        current = supervisor._read()
        if current and current.get("state") == "running":
            _stop(int(current["pid"]))


def test_http_round_trip_marks_health_ok(tmp_path: Path) -> None:
    port = _serve_socks()
    supervisor = Supervisor(tmp_path)
    state = supervisor.start(_sleep_cmd())
    try:
        probed = supervisor.tick(0, socks_port=port, http_url="http://example.test/generate_204")
        assert probed["health"] == "ok"
        later = supervisor.tick(4, socks_port=port, http_url="http://example.test/generate_204")
        assert later["health"] == "ok"
    finally:
        _stop(state["pid"])


def test_backoff_then_failed(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path)
    supervisor.start(_exit_cmd())
    now = 0.0
    delays: list[float] = []
    seen: float | None = None
    state: dict[str, object] = {}
    for _ in range(40):
        time.sleep(0.02)
        state = supervisor.tick(now, socks_port=1)
        if state["state"] == "starting":
            at = float(state["next_restart_at"])
            if at != seen:
                delays.append(at - now)
                seen = at
                now = at
            continue
        if state["state"] == "failed":
            break
    assert delays == [1, 2, 4, 8]
    assert state["state"] == "failed"


def test_stop_kills_a_stubborn_group(tmp_path: Path) -> None:
    code = (
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    time.sleep(30)\n"
        "    os._exit(0)\n"
        f"open({str(tmp_path / 'child')!r}, 'w').write(str(child))\n"
        "time.sleep(30)\n"
    )
    supervisor = Supervisor(tmp_path)
    state = supervisor.start([sys.executable, "-c", code])
    child_path = tmp_path / "child"
    for _ in range(50):
        if child_path.exists():
            break
        time.sleep(0.02)
    child = int(child_path.read_text(encoding="utf-8"))
    result = supervisor.stop(grace=0.2)
    assert result["forced"] is True
    assert result["note"] == "force was used"
    assert not (tmp_path / "current.json").exists()
    with pytest.raises(ProcessLookupError):
        os.kill(state["pid"], 0)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_log_redacts_secrets_and_rotates(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path, secrets=["s3cret-password", UUID, "reality-key"])
    supervisor.write_log(b"password=s3cret-password uuid=" + UUID.encode() + b" key=reality-key\n")
    text = (tmp_path / "core.log").read_text(encoding="utf-8")
    assert "s3cret-password" not in text
    assert UUID not in text
    assert "reality-key" not in text
    assert text.count("***") == 3
    supervisor.write_log(b"x" * LOG_LIMIT)
    assert not (tmp_path / "core.log.1").exists()
    supervisor.write_log(b"y")
    assert (tmp_path / "core.log.1").stat().st_size >= LOG_LIMIT
    assert (tmp_path / "core.log").read_bytes() == b"y"


def test_spawned_secret_is_redacted(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path, secrets=["s3cret-password"])
    code = "print('see s3cret-password', flush=True); import time; time.sleep(30)"
    state = supervisor.start([sys.executable, "-u", "-c", code])
    try:
        for _ in range(50):
            path = tmp_path / "core.log"
            if path.exists() and b"***" in path.read_bytes():
                break
            time.sleep(0.02)
        assert b"s3cret-password" not in (tmp_path / "core.log").read_bytes()
    finally:
        _stop(state["pid"])


def test_occupied_port_names_the_holder(tmp_path: Path) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        with pytest.raises(SupervisorError) as exc:
            Supervisor(tmp_path).start(_sleep_cmd(), ports=[port])
        assert exc.value.code == "port_in_use"
        assert str(os.getpid()) in exc.value.message
        assert "command" in exc.value.message
    finally:
        sock.close()


def test_fresh_start_clears_a_failure_streak(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path)
    supervisor._failures = 5
    supervisor._tcp_failures = 2
    state = supervisor.start(_sleep_cmd())
    try:
        assert state["restarts"] == 0
        assert supervisor._failures == 0
        assert supervisor._tcp_failures == 0
        os.kill(state["pid"], signal.SIGKILL)
        time.sleep(0.05)
        crashed = supervisor.tick(0, socks_port=1)
        assert crashed["state"] == "starting"
        assert crashed["restarts"] == 1
    finally:
        current = supervisor._read()
        if current and isinstance(current.get("pid"), int):
            _stop(int(current["pid"]))


def test_one_tcp_miss_stays_unknown(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path)
    state = supervisor.start(_sleep_cmd())
    try:
        probed = supervisor.tick(0, socks_port=1)
        assert probed["health"] == "failed"
        assert probed["state"] == "running"
    finally:
        _stop(state["pid"])


def test_unit_text_and_detached_fallback(tmp_path: Path) -> None:
    text = unit_text("/usr/bin/python -m omarchy_proxy.supervisor")
    assert "ExecStart=/usr/bin/python -m omarchy_proxy.supervisor" in text
    assert "WantedBy=default.target" in text
    supervisor = Supervisor(tmp_path)
    assert supervisor.detach(True) == {"mode": "systemd"}
    detached = supervisor.detach(False)
    try:
        assert detached["mode"] == "detached"
        assert "logout" in detached["warning"]
        command = Path(f"/proc/{detached['pid']}/cmdline").read_bytes()
        assert b"omarchy_proxy.supervisor" in command
        pid = int((tmp_path / "supervisor.pid").read_text(encoding="utf-8"))
        os.kill(pid, 0)
    finally:
        _stop(int(detached["pid"]))


def _serve_socks() -> int:
    ready = threading.Event()
    holder: dict[str, int] = {}

    def run() -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(2)
        holder["port"] = server.getsockname()[1]
        ready.set()
        for _ in range(2):
            conn, _address = server.accept()
            with conn:
                data = conn.recv(2)
                if not data or data[0] != 5:
                    continue
                conn.recv(16)
                conn.sendall(b"\x05\x00")
                conn.recv(64)
                conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
                conn.recv(128)
                conn.sendall(b"HTTP/1.0 204 No Content\r\n\r\n")
        server.close()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(1)
    return holder["port"]
