"""Connect flow: choose a core, start it, expose it, then wait until it is healthy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from omarchy_proxy.backends.base import Backend, BackendError, BackendSettings
from omarchy_proxy.backends.detect import Selector
from omarchy_proxy.catalog import Catalog
from omarchy_proxy.health import DEFAULT_URL
from omarchy_proxy.model import Server, server_secrets
from omarchy_proxy.supervisor import Supervisor, SupervisorError
from omarchy_proxy.sysproxy import DEFAULT_BYPASS, HTTP_PORT, SOCKS_PORT, Exposure
from omarchy_proxy.tun_helper import HELPER, TunError, acknowledged, foreign_device, privileged
from omarchy_proxy.uri import ParseError, parse_uri


class ManagerError(Exception):
    """Connect could not finish."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ConnectPlan:
    """Which core a connect would start, and why."""

    backend: str
    reason: str


def plan_connect(
    server: Server,
    preference: str = "auto",
    selector: Selector | None = None,
    mode: str = "manual",
) -> ConnectPlan:
    """Choose the core for a server. Does not start a process."""
    chosen, reason = (selector or Selector()).choose(server, preference, mode)
    return ConnectPlan(backend=chosen.name, reason=reason)


class Manager:
    """Starts one core and applies the exposure mode."""

    def __init__(
        self,
        state_dir: Path,
        config_home: Path,
        exposure: Exposure | None = None,
        supervisor: Supervisor | None = None,
        selector: Selector | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.exposure = exposure or Exposure(state_dir, config_home)
        self.supervisor = supervisor or Supervisor(state_dir)
        self.selector = selector or Selector()
        self.route_lookup: Callable[[], str] | None = None

    def connect(
        self,
        catalog: Catalog,
        identity: str,
        *,
        mode: str = "manual",
        preference: str = "auto",
        backend: Backend | None = None,
        socks_port: int = SOCKS_PORT,
        http_port: int = HTTP_PORT,
        bypass: str = DEFAULT_BYPASS,
        health_url: str = DEFAULT_URL,
        timeout: float = 15,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        def step(name: str) -> None:
            if progress is not None:
                progress(name)

        step("lookup")
        row = catalog.get(identity)
        server = parse_uri(str(row["uri"]))
        if isinstance(server, ParseError):
            raise ManagerError(server.code, server.message)
        step("config")
        if mode == "tun" and not acknowledged(self.state_dir):
            raise ManagerError(
                "tun_unacknowledged",
                "tun captures all traffic and a crash blocks clear internet until disconnect",
            )
        if mode == "tun":
            self._ensure_route_free()
        chosen, reason = self._backend(server, preference, backend, mode)
        if chosen.name == "xray" and socks_port == http_port:
            raise ManagerError("invalid_field", "xray needs different socks and http ports")
        self.supervisor.secrets = server_secrets(server)
        config = self.state_dir / "config.json"
        settings: BackendSettings = {
            "socks_port": socks_port,
            "http_port": http_port,
            "health_url": health_url,
            "tun": mode == "tun",
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps(chosen.generate_config(server, settings)), encoding="utf-8")
        os.chmod(config, 0o600)
        try:
            chosen.validate_config(config)
        except BackendError as exc:
            raise ManagerError(exc.code, exc.message) from exc
        argv = _argv(chosen, config, mode)
        env = None
        if mode == "tun":
            env = {**os.environ, "XDG_STATE_HOME": str(self.state_dir.parent)}
        step("starting")
        try:
            self.supervisor.start(argv, [socks_port, http_port], env=env)
        except SupervisorError as exc:
            raise ManagerError(exc.code, exc.message) from exc
        try:
            current = self.supervisor._read() or {}
            current.update(
                socks_port=socks_port,
                http_port=http_port,
                backend=chosen.name,
                server_id=identity,
                reason=reason,
                health_url=health_url,
            )
            self.supervisor._write(current)
            step("exposure")
            if mode == "tun":
                exposed = {"system_mode": "none", "requires_relogin": False, "mode": "tun"}
            else:
                exposed = self.exposure.connect(
                    mode, socks_port=socks_port, http_port=http_port, bypass=bypass
                )
            health = self._wait_until_healthy(socks_port, health_url, timeout)
            step("healthy")
        except Exception as exc:
            result = self.disconnect()
            if result.get("tun_error") and isinstance(exc, ManagerError):
                raise ManagerError(exc.code, f"{exc.message}; {result['tun_error']}") from exc
            raise
        used = catalog.update_last_used(identity)
        return {
            "server_id": identity,
            "backend": chosen.name,
            "reason": reason,
            "health": health,
            "system_mode": exposed.get("system_mode"),
            "last_used_at": used["last_used_at"],
        }

    def disconnect(self) -> dict[str, Any]:
        current = self.supervisor._read() or {}
        tun = current.get("reason") == "tun_requires_singbox" or (
            isinstance(current.get("argv"), list) and "tun-helper" in current["argv"]
        )
        exposed = self.exposure.disconnect()
        stopped = self.supervisor.stop()
        (self.state_dir / "config.json").unlink(missing_ok=True)
        result = {
            "state": stopped.get("state", "stopped"),
            "forced": stopped.get("forced", False),
            "system_mode": exposed.get("system_mode", "none"),
        }
        if tun:
            result.update(self._tun_down())
        return result

    def _backend(
        self, server: Server, preference: str, backend: Backend | None, mode: str = "manual"
    ) -> tuple[Backend, str]:
        if backend is not None:
            reason = "tun_requires_singbox" if mode == "tun" else "stub"
            return backend, reason
        try:
            chosen, reason = self.selector.choose(server, preference, mode)
            chosen.version()
        except BackendError as exc:
            raise ManagerError(exc.code, exc.message) from exc
        return cast(Backend, chosen), reason

    def _ensure_route_free(self) -> None:
        try:
            device = foreign_device(self._route_json())
        except TunError as exc:
            raise ManagerError(exc.code, exc.message) from exc
        if device is not None:
            raise ManagerError("route_owned", f"default route is held by {device}")

    def _route_json(self) -> str:
        if self.route_lookup is not None:
            return self.route_lookup()
        rows: list[Any] = []
        for argv in (["ip", "-j", "route"], ["ip", "-j", "-6", "route"]):
            result = subprocess.run(  # noqa: S603
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            try:
                parsed = json.loads(result.stdout or "[]")
            except json.JSONDecodeError as exc:
                raise TunError("route_unavailable", "ip route did not return JSON") from exc
            if not isinstance(parsed, list):
                raise TunError("route_unavailable", "ip route did not return a list")
            rows.extend(parsed)
        return json.dumps(rows)

    def _tun_down(self) -> dict[str, str]:
        if not HELPER.is_file():
            return {"tun_error": "tun-helper is not installed"}
        completed = subprocess.run(  # noqa: S603
            privileged("down"),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "XDG_STATE_HOME": str(self.state_dir.parent)},
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "tun-helper down failed").strip()
            return {"tun_error": detail}
        return {}

    def _wait_until_healthy(self, socks_port: int, health_url: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        health = "unknown"
        while time.monotonic() < deadline:
            state = self.supervisor.tick(time.monotonic(), socks_port, health_url)
            health = str(state.get("health") or "unknown")
            if health == "ok":
                return health
            time.sleep(0.05)
        raise ManagerError("health_timeout", "proxy did not become healthy within 15 seconds")


def _argv(backend: Backend, config: Path, mode: str = "manual") -> list[str]:
    if mode == "tun":
        return privileged("up", str(config))
    command = getattr(backend, "command", None)
    if isinstance(command, list) and command:
        return [str(part) for part in command]
    binary = None
    for name in backend.binary_candidates:
        binary = shutil.which(name)
        if binary:
            break
    if binary is None:
        raise ManagerError("backend_missing", f"{backend.name} is not installed")
    if backend.name == "sing-box":
        return [binary, "run", "-c", str(config)]
    return [binary, "run", "-config", str(config)]
