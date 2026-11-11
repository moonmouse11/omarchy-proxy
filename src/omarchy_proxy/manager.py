"""Connect flow. Core selection and exposure are wired. The process start is not."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omarchy_proxy.backends.detect import Selector
from omarchy_proxy.model import Server
from omarchy_proxy.sysproxy import DEFAULT_BYPASS, HTTP_PORT, SOCKS_PORT, Exposure


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
    """Applies the exposure mode. Starting the core is a later step."""

    def __init__(
        self, state_dir: Path, config_home: Path, exposure: Exposure | None = None
    ) -> None:
        self.exposure = exposure or Exposure(state_dir, config_home)

    def connect(
        self,
        mode: str = "manual",
        *,
        socks_port: int = SOCKS_PORT,
        http_port: int = HTTP_PORT,
        bypass: str = DEFAULT_BYPASS,
    ) -> dict[str, Any]:
        return self.exposure.connect(
            mode, socks_port=socks_port, http_port=http_port, bypass=bypass
        )

    def disconnect(self) -> dict[str, Any]:
        return self.exposure.disconnect()
