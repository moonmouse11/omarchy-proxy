"""Catalog file durability, permissions, and load time."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from omarchy_proxy.catalog import SCHEMA_VERSION, Catalog, CatalogError


def test_save_is_atomic_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "servers.json"
    catalog = Catalog(path)
    catalog.save({"version": SCHEMA_VERSION, "servers": [{"id": "old"}]})

    def boom(_src: str | Path, _dst: str | Path) -> None:
        raise OSError("killed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="killed"):
        catalog.save({"version": SCHEMA_VERSION, "servers": [{"id": "new"}]})
    assert Catalog(path).load()["servers"] == [{"id": "old"}]


def test_broader_permissions_are_tightened(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    catalog = Catalog(path)
    catalog.save({"version": SCHEMA_VERSION, "servers": []})
    os.chmod(path, 0o644)
    loaded = Catalog(path)
    assert loaded.load()["version"] == SCHEMA_VERSION
    assert loaded.tightened is True
    assert path.stat().st_mode & 0o777 == 0o600


def test_fresh_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    Catalog(path).save({"version": SCHEMA_VERSION, "servers": []})
    assert path.stat().st_mode & 0o777 == 0o600


def test_thousand_entries_load_under_100ms(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    servers = [{"id": f"s{i}", "name": f"n{i}", "uri": f"socks5://h{i}:1"} for i in range(1000)]
    Catalog(path).save({"version": SCHEMA_VERSION, "servers": servers})
    started = time.perf_counter()
    loaded = Catalog(path).load()
    elapsed = time.perf_counter() - started
    assert len(loaded["servers"]) == 1000
    assert elapsed < 0.1


def test_stale_save_does_not_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    first = Catalog(path)
    second = Catalog(path)
    assert first.load()["servers"] == []
    assert second.load()["servers"] == []
    first.save({"version": SCHEMA_VERSION, "servers": [{"id": "a"}]})
    with pytest.raises(CatalogError, match="changed") as caught:
        second.save({"version": SCHEMA_VERSION, "servers": [{"id": "b"}]})
    assert caught.value.code == "catalog_locked"
    assert Catalog(path).load()["servers"] == [{"id": "a"}]


def test_wrong_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    Catalog(path).save({"version": SCHEMA_VERSION, "servers": []})
    path.write_text('{"version": 2, "servers": []}', encoding="utf-8")
    with pytest.raises(CatalogError, match="version"):
        Catalog(path).load()
