"""The backend contract is what later cores must implement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omarchy_proxy.backends.base import Backend, BackendSettings
from omarchy_proxy.model import Server, Socks5Server


class _Stub:
    name = "stub"
    binary_candidates = ("stub-bin",)

    def generate_config(self, server: Server, settings: BackendSettings) -> dict[str, Any]:
        return {"host": server.host, "socks": settings["socks_port"]}

    def validate_config(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)

    def version(self) -> str:
        return "1.0.0"


def test_stub_matches_the_backend_protocol() -> None:
    backend: Backend = _Stub()
    assert isinstance(backend, Backend)
    server = Socks5Server(host="10.0.0.1", port=1080, name="n", dns_via_proxy=False)
    settings: BackendSettings = {
        "socks_port": 10808,
        "http_port": 10809,
        "health_url": "http://example.test/generate_204",
    }
    assert backend.generate_config(server, settings)["socks"] == 10808
    assert backend.version() == "1.0.0"
    assert backend.binary_candidates == ("stub-bin",)
