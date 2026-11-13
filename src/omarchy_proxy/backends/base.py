"""Contract every proxy core must satisfy.

A backend turns one catalog server into a JSON config, checks that config
with the core's own tool, and reports the installed version.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable

from omarchy_proxy.model import Server

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """First ``x.y.z`` triple in a ``--version`` banner, or None."""
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def find_binary(candidates: tuple[str, ...], name: str) -> str:
    """First candidate on PATH. Raises when the core is not installed."""
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    raise BackendError("backend_missing", f"{name} is not installed")


def checked_version(binary: str, name: str, minimum: tuple[int, int, int]) -> str:
    """Installed version of the core at ``binary``. Raises below ``minimum``."""
    result = subprocess.run(  # noqa: S603
        [binary, "version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise BackendError("backend_missing", f"{name} version failed")
    found = parse_version(result.stdout)
    if found is None or found < minimum:
        shown = ".".join(str(part) for part in found) if found else result.stdout.strip()
        raise BackendError(
            "backend_too_old",
            f"{name} {shown} is older than {'.'.join(map(str, minimum))}",
        )
    return ".".join(str(part) for part in found)


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
