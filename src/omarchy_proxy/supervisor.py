"""One proxy core: start, health, restart, stop, and logs.

The state file is ``current.json`` in the state directory. The core's output
goes to ``core.log``. A caller drives time by passing ``now`` into ``tick``.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

from omarchy_proxy.health import (
    DEFAULT_GEO_URL,
    GeoCache,
    Health,
    HealthError,
    fetch_json,
    round_trip,
)

LOG_LIMIT = 1_000_000
_BACKOFF = (1, 2, 4, 8, 16)
_MAX_FAILURES = 5


class SupervisorError(Exception):
    """The supervisor cannot start or read its state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def state_dir() -> Path:
    root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(root) / "omarchy-proxy"


def unit_text(exec_start: str) -> str:
    """systemd --user unit. The installer writes this file."""
    return (
        "[Unit]\n"
        "Description=omarchy-proxy supervisor\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


class Supervisor:
    """Track a single child process."""

    def __init__(self, directory: Path | None = None, secrets: list[str] | None = None) -> None:
        self.directory = directory or state_dir()
        self.secrets = secrets or []
        self.health = Health()
        self.geo = GeoCache()
        self._failures = 0
        self._stopped = False
        self._tcp_failures = 0
        self._last_tcp: float | None = None
        self._last_http: float | None = None
        self._next_restart_at: float | None = None
        self._log_lock = threading.Lock()

    def start(
        self, argv: list[str], ports: list[int] | None = None, *, restart: bool = False
    ) -> dict[str, Any]:
        if not argv:
            raise SupervisorError("invalid_field", "missing command")
        current = self._read()
        if not restart and current is not None and _is_ours(current):
            raise SupervisorError("already_running", "a proxy core is already running")
        for port in ports or []:
            _ensure_free(port)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._stopped = False
        if not restart:
            self._failures = 0
            self._tcp_failures = 0
            self._last_tcp = None
            self._last_http = None
            self._next_restart_at = None
            self.health = Health()
        self.health.state = "starting"
        pid = self._spawn(argv)
        payload: dict[str, Any] = {
            "state": "running",
            "pid": pid,
            "argv": argv,
            "health": "unknown",
            "restarts": self._failures,
        }
        self._write(payload)
        return payload

    def tick(self, now: float, socks_port: int, http_url: str | None = None) -> dict[str, Any]:
        """One pass: reap a dead child, maybe restart, maybe probe health."""
        current = self._read()
        if self._stopped or current is None:
            return current or {"state": "stopped"}
        if current.get("state") == "failed":
            return current
        if current.get("state") == "starting":
            if self._next_restart_at is not None and now < self._next_restart_at:
                return current
            argv = [str(part) for part in current.get("argv", [])]
            return self.start(argv, restart=True)
        if _reap(int(current["pid"])) is not None:
            return self._crashed(current, now)
        self._probe(current, now, socks_port, http_url)
        self._write(current)
        return current

    def stop(self, grace: float = 3) -> dict[str, Any]:
        """SIGTERM, then SIGKILL. A user stop does not restart."""
        self._stopped = True
        self.health.mark_stopped()
        current = self._read()
        forced = False
        if current is not None and isinstance(current.get("pid"), int):
            forced = _terminate(int(current["pid"]), grace)
        path = self.directory / "current.json"
        if path.exists():
            path.unlink()
        result = {"state": "stopped", "forced": forced}
        if forced:
            result["note"] = "force was used"
        return result

    def detach(self, systemctl_works: bool) -> dict[str, Any]:
        """Use systemd when it answers. Otherwise start a detached fallback."""
        if systemctl_works:
            return {"mode": "systemd"}
        self.directory.mkdir(parents=True, exist_ok=True)
        argv = [sys.executable, "-m", "omarchy_proxy.supervisor", str(self.directory)]
        pid = os.posix_spawn(argv[0], argv, os.environ, setsid=True)
        (self.directory / "supervisor.pid").write_text(str(pid), encoding="utf-8")
        return {
            "mode": "detached",
            "pid": pid,
            "warning": "auto-restart will not survive logout",
        }

    def write_log(self, data: bytes) -> None:
        text = data.decode(errors="replace")
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "***")
        raw = text.encode()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "core.log"
        with self._log_lock:
            if path.exists() and path.stat().st_size >= LOG_LIMIT:
                os.replace(path, path.with_name("core.log.1"))
            with path.open("ab") as handle:
                handle.write(raw)
            os.chmod(path, 0o600)

    def _spawn(self, argv: list[str]) -> int:
        read_fd, write_fd = os.pipe()
        thread = threading.Thread(target=self._pump, args=(read_fd,), daemon=True)
        thread.start()
        try:
            pid = os.posix_spawn(
                argv[0],
                argv,
                os.environ,
                file_actions=[
                    (os.POSIX_SPAWN_DUP2, write_fd, 1),
                    (os.POSIX_SPAWN_DUP2, write_fd, 2),
                    (os.POSIX_SPAWN_CLOSE, read_fd),
                    (os.POSIX_SPAWN_CLOSE, write_fd),
                ],
                setsid=True,
            )
        finally:
            os.close(write_fd)
        return pid

    def _pump(self, read_fd: int) -> None:
        try:
            while True:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                self.write_log(chunk)
        finally:
            os.close(read_fd)

    def _crashed(self, current: dict[str, Any], now: float) -> dict[str, Any]:
        self._failures += 1
        if self._failures >= _MAX_FAILURES:
            current["state"] = "failed"
            current["last_error"] = "core exited 5 times"
            self._write(current)
            return current
        delay = _BACKOFF[min(self._failures - 1, len(_BACKOFF) - 1)]
        self._next_restart_at = now + delay
        current["state"] = "starting"
        current["restarts"] = self._failures
        current["next_restart_at"] = self._next_restart_at
        self._write(current)
        return current

    def _probe(
        self, current: dict[str, Any], now: float, socks_port: int, http_url: str | None
    ) -> None:
        if socks_port > 0 and (self._last_tcp is None or now - self._last_tcp >= 5):
            self._last_tcp = now
            open_port = _tcp_open(socks_port)
            self.health.port(open_port, now)
            current["health"] = self.health.state
            if open_port:
                self._tcp_failures = 0
            else:
                self._tcp_failures += 1
                if self._tcp_failures >= 3:
                    self._tcp_failures = 0
                    _terminate(int(current["pid"]), 0)
                    self._crashed(current, now)
                    return
        if http_url and (self._last_http is None or now - self._last_http >= 60):
            self._last_http = now
            try:
                status = round_trip(socks_port, http_url, timeout=10)
            except (OSError, HealthError) as exc:
                code = exc.code if isinstance(exc, HealthError) else "socks_error"
                self.health.round_trip_result(False, code)
            else:
                ok = 200 <= status < 300
                self.health.round_trip_result(ok, None if ok else "http_status")
                if ok:
                    self._record_geo(socks_port, now)
            current["health"] = self.health.state
            if self.health.last_error:
                current["health_error"] = self.health.last_error
            else:
                current.pop("health_error", None)

    def _record_geo(self, socks_port: int, now: float) -> None:
        payload = self.geo.lookup(
            now,
            lambda: fetch_json(socks_port, DEFAULT_GEO_URL, timeout=10),
        )
        if payload:
            from omarchy_proxy.doctor import write_geo

            write_geo(self.directory, payload)

    def _read(self) -> dict[str, Any] | None:
        path = self.directory / "current.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return cast(dict[str, Any], data)

    def _write(self, payload: dict[str, Any]) -> None:
        path = self.directory / "current.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)


def _ensure_free(port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError as exc:
        raise SupervisorError("port_in_use", _holder(port)) from exc
    finally:
        sock.close()


def _holder(port: int) -> str:
    inode = _listen_inode(port)
    if inode is None:
        return f"port {port} is in use"
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        fd_dir = entry / "fd"
        try:
            links = list(fd_dir.iterdir())
        except OSError:
            continue
        for link in links:
            try:
                target = os.readlink(link)
            except OSError:
                continue
            if target == f"socket:[{inode}]":
                command = _cmdline(int(entry.name)) or []
                return f"pid {entry.name} command {' '.join(command)}"
    return f"port {port} is in use"


def _listen_inode(port: int) -> str | None:
    try:
        lines = Path("/proc/net/tcp").read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        local, state, inode = parts[1], parts[3], parts[9]
        _address, _sep, hex_port = local.partition(":")
        if state == "0A" and hex_port and int(hex_port, 16) == port:
            return inode
    return None


def _tcp_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), 0.2):
            return True
    except OSError:
        return False


def _terminate(pid: int, grace: float) -> bool:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if _reap(pid) is not None or _cmdline(pid) is None:
            return False
        time.sleep(0.02)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        forced = False
    else:
        forced = True
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        return False
    return forced


def _reap(pid: int) -> int | None:
    try:
        waited, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return None
    if waited == 0:
        return None
    return os.waitstatus_to_exitcode(status)


def serve(directory: Path) -> None:
    """Keep ticking until the process is killed."""
    supervisor = Supervisor(directory)
    queue_saved_subscriptions()
    while True:
        current = supervisor._read() or {}
        port = current.get("socks_port")
        socks = port if isinstance(port, int) else 0
        supervisor.tick(time.monotonic(), socks)
        time.sleep(0.2)


def queue_saved_subscriptions() -> list[str]:
    """Queue stale auto-refresh subscriptions once. Failures stay in the catalog."""
    from omarchy_proxy.catalog import Catalog, CatalogError
    from omarchy_proxy.subscriptions import SubscriptionError, queue_stale, refresh

    catalog = Catalog()

    def refresh_one(url: str) -> None:
        try:
            refresh(catalog, url)
        except (CatalogError, SubscriptionError):
            return

    try:
        from omarchy_proxy.settings import Settings

        hours = int(Settings().get("refresh_hours"))
        return queue_stale(catalog, refresh_one, interval=hours * 3600)
    except CatalogError:
        return []


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    serve(Path(args[0]) if args else state_dir())


if __name__ == "__main__":
    main()


def _is_ours(current: dict[str, Any]) -> bool:
    pid = current.get("pid")
    argv = current.get("argv")
    if not isinstance(pid, int) or not isinstance(argv, list):
        return False
    return _cmdline(pid) == [str(part) for part in argv]


def _cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]
