"""Backend selection branches and the version cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from omarchy_proxy.backends.base import BackendError
from omarchy_proxy.backends.detect import Selector
from omarchy_proxy.model import Hysteria2Server, Socks5Server, TuicServer, VlessServer

UUID = "01234567-89ab-cdef-0123-456789abcdef"


def _which(found: dict[str, str | None]):
    def lookup(name: str) -> str | None:
        return found.get(name)

    return lookup


def _both() -> dict[str, str | None]:
    return {"xray": "/bin/xray", "sing-box": "/bin/sing-box"}


def test_shared_protocol_follows_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omarchy_proxy.backends.detect.shutil.which", _which(_both()))
    server = VlessServer(host="v.example", port=443, name="v", uuid=UUID)
    selector = Selector()
    backend, reason = selector.choose(server, "auto")
    assert backend.name == "xray"
    assert reason == "preference"
    backend, reason = selector.choose(server, "xray")
    assert backend.name == "xray"
    socks = Socks5Server(host="10.0.0.1", port=1080, name="s")
    backend, reason = selector.choose(socks, "sing-box")
    assert backend.name == "sing-box"
    assert reason == "preference"


def test_hysteria2_and_tuic_ignore_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omarchy_proxy.backends.detect.shutil.which", _which(_both()))
    selector = Selector()
    hy2 = Hysteria2Server(host="h.example", port=443, name="h", password="p")
    tuic = TuicServer(host="t.example", port=443, name="t", uuid=UUID, password="p")
    for server in (hy2, tuic):
        backend, reason = selector.choose(server, "xray")
        assert backend.name == "sing-box"
        assert reason == "protocol_requires_singbox"


def test_missing_singbox_does_not_fall_back_to_xray(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def lookup(name: str) -> str | None:
        seen.append(name)
        return "/bin/xray" if name == "xray" else None

    monkeypatch.setattr("omarchy_proxy.backends.detect.shutil.which", lookup)
    server = Hysteria2Server(host="h.example", port=443, name="h", password="p")
    with pytest.raises(BackendError) as exc:
        Selector().choose(server, "xray")
    assert exc.value.code == "backend_missing"
    assert "sing-box" in exc.value.message
    assert "pacman -S sing-box" in exc.value.message
    assert seen == ["sing-box"]


def test_missing_xray_falls_back_for_a_shared_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    found = {"xray": None, "sing-box": "/bin/sing-box"}
    monkeypatch.setattr("omarchy_proxy.backends.detect.shutil.which", _which(found))
    server = VlessServer(host="v.example", port=443, name="v", uuid=UUID)
    backend, reason = Selector().choose(server, "auto")
    assert backend.name == "sing-box"
    assert reason == "xray_missing"


def test_version_is_cached_until_the_binary_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "xray"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("omarchy_proxy.backends.detect.shutil.which", _which({"xray": str(binary)}))
    selector = Selector()
    calls = {"n": 0}

    def version() -> str:
        calls["n"] += 1
        return "1.8.4"

    selector.xray.version = version  # type: ignore[method-assign]
    assert selector.version_of(selector.xray) == "1.8.4"
    assert selector.version_of(selector.xray) == "1.8.4"
    assert calls["n"] == 1
    binary.write_text("#!/bin/sh\n# changed\n", encoding="utf-8")
    assert selector.version_of(selector.xray) == "1.8.4"
    assert calls["n"] == 2
