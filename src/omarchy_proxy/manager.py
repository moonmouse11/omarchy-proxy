"""Connect flow: choose a core, start it, expose it, then wait until it is healthy."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from omarchy_proxy.backends.base import Backend, BackendError, BackendSettings
from omarchy_proxy.backends.detect import Selector
from omarchy_proxy.catalog import Catalog
from omarchy_proxy.health import DEFAULT_URL
from omarchy_proxy.model import Server
from omarchy_proxy.supervisor import Supervisor, SupervisorError
from omarchy_proxy.sysproxy import DEFAULT_BYPASS, HTTP_PORT, SOCKS_PORT, Exposure
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
    server: Server, preference: str = "auto", selector: Selector | None = None
) -> ConnectPlan:
    """Choose the core for a server. Does not start a process."""
    chosen, reason = (selector or Selector()).choose(server, preference)
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
        chosen, reason = self._backend(server, preference, backend)
        config = self.state_dir / "config.json"
        settings: BackendSettings = {
            "socks_port": socks_port,
            "http_port": http_port,
            "health_url": health_url,
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps(chosen.generate_config(server, settings)), encoding="utf-8")
        os.chmod(config, 0o600)
        try:
            chosen.validate_config(config)
        except BackendError as exc:
            raise ManagerError(exc.code, exc.message) from exc
        argv = _argv(chosen, config)
        step("starting")
        try:
            self.supervisor.start(argv, [socks_port, http_port])
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
            )
            self.supervisor._write(current)
            step("exposure")
            exposed = self.exposure.connect(
                mode, socks_port=socks_port, http_port=http_port, bypass=bypass
            )
            health = self._wait_until_healthy(socks_port, health_url, timeout)
            step("healthy")
        except Exception:
            self.disconnect()
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
        exposed = self.exposure.disconnect()
        stopped = self.supervisor.stop()
        (self.state_dir / "config.json").unlink(missing_ok=True)
        return {
            "state": stopped.get("state", "stopped"),
            "forced": stopped.get("forced", False),
            "system_mode": exposed.get("system_mode", "none"),
        }

    def _backend(
        self, server: Server, preference: str, backend: Backend | None
    ) -> tuple[Backend, str]:
        if backend is not None:
            return backend, "stub"
        try:
            chosen, reason = self.selector.choose(server, preference)
        except BackendError as exc:
            raise ManagerError(exc.code, exc.message) from exc
        return cast(Backend, chosen), reason

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


def _argv(backend: Backend, config: Path) -> list[str]:
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
