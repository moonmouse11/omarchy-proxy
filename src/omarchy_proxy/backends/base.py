"""Contract every proxy core must satisfy.

A backend turns one catalog server into a JSON config, checks that config
with the core's own tool, and reports the installed version.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable

from omarchy_proxy.model import Server


class BackendError(Exception):
    """The selected core cannot be used."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BackendSettings(TypedDict):
    """The few runtime values a generated config needs."""

    socks_port: int
    http_port: int
    health_url: str
    tun: NotRequired[bool]


@runtime_checkable
class Backend(Protocol):
    """One installed proxy core, such as xray or sing-box."""

    name: str
    binary_candidates: tuple[str, ...]

    def generate_config(self, server: Server, settings: BackendSettings) -> dict[str, Any]:
        """Build a JSON config for this server. Do not write it."""
        ...

    def validate_config(self, path: Path) -> None:
        """Run the core's checker. Raise if the file is rejected."""
        ...

    def version(self) -> str:
        """Return the installed core version, such as ``1.8.0``."""
        ...
