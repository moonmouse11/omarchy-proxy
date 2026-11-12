"""Pick xray or sing-box for one server and cache the core version."""

from __future__ import annotations

import os
import shutil

from omarchy_proxy.backends.base import BackendError
from omarchy_proxy.backends.singbox import SingBoxBackend
from omarchy_proxy.backends.xray import XrayBackend
from omarchy_proxy.model import Hysteria2Server, Server, TuicServer

PREFERENCES = ("auto", "xray", "sing-box")
_HINTS = {
    "xray": "yay -S xray-bin",
    "sing-box": "pacman -S sing-box",
}


Core = XrayBackend | SingBoxBackend


class Selector:
    """Choose a core. Remembers a version until the binary file changes."""

    def __init__(self) -> None:
        self.xray = XrayBackend()
        self.singbox = SingBoxBackend()
        self._versions: dict[str, tuple[int, str]] = {}

    def choose(
        self, server: Server, preference: str = "auto", mode: str = "manual"
    ) -> tuple[Core, str]:
        if preference not in PREFERENCES:
            raise BackendError("invalid_field", f"unknown backend preference: {preference}")
        if mode == "tun":
            if self._path(self.singbox) is None:
                raise BackendError(
                    "no_backend",
                    "sing-box is not installed. Install it with: pacman -S sing-box",
                )
            return self.singbox, "tun_requires_singbox"
        if isinstance(server, (Hysteria2Server, TuicServer)):
            self._require(self.singbox)
            return self.singbox, "protocol_requires_singbox"
        if preference == "sing-box":
            self._require(self.singbox)
            return self.singbox, "preference"
        if self._path(self.xray) is not None:
            return self.xray, "preference"
        if self._path(self.singbox) is not None:
            return self.singbox, "xray_missing"
        self._require(self.xray)
        return self.xray, "preference"

    def version_of(self, backend: Core) -> str:
        path = self._require(backend)
        modified = os.stat(path).st_mtime_ns
        cached = self._versions.get(path)
        if cached is not None and cached[0] == modified:
            return cached[1]
        found = backend.version()
        self._versions[path] = (modified, found)
        return found

    def _require(self, backend: Core) -> str:
        path = self._path(backend)
        if path is None:
            hint = _HINTS.get(backend.name, backend.binary_candidates[0])
            binary = backend.binary_candidates[0]
            raise BackendError(
                "backend_missing",
                f"{binary} is not installed. Install it with: {hint}",
            )
        return path

    def _path(self, backend: Core) -> str | None:
        for name in backend.binary_candidates:
            found = shutil.which(name)
            if found:
                return found
        return None
