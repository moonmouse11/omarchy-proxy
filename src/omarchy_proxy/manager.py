"""Connect flow. Only core selection is wired so far."""

from __future__ import annotations

from dataclasses import dataclass

from omarchy_proxy.backends.detect import Selector
from omarchy_proxy.model import Server


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
