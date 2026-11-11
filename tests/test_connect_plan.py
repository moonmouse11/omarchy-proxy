"""Connect chooses a core before any process is started."""

from __future__ import annotations

import pytest

from omarchy_proxy.manager import plan_connect
from omarchy_proxy.model import Hysteria2Server, VlessServer

UUID = "01234567-89ab-cdef-0123-456789abcdef"


def test_hysteria2_connect_uses_singbox_despite_xray_preference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def lookup(name: str) -> str | None:
        return "/bin/sing-box" if name == "sing-box" else "/bin/xray"

    monkeypatch.setattr("omarchy_proxy.backends.detect.shutil.which", lookup)
    server = Hysteria2Server(host="h.example", port=443, name="h", password="p")
    plan = plan_connect(server, "xray")
    assert plan.backend == "sing-box"
    assert plan.reason == "protocol_requires_singbox"


def test_vless_connect_keeps_the_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omarchy_proxy.backends.detect.shutil.which", lambda _name: "/bin/xray")
    server = VlessServer(host="v.example", port=443, name="v", uuid=UUID)
    plan = plan_connect(server, "xray")
    assert plan.backend == "xray"
    assert plan.reason == "preference"
