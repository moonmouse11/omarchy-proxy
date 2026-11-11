"""Catalog identity, dedup, and list order."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from omarchy_proxy.catalog import Catalog, CatalogError, server_id


def test_reimport_updates_the_same_row(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    first = catalog.add("socks5://10.0.0.1:1080#One", now=datetime(2026, 1, 1, tzinfo=UTC))
    second = catalog.add("socks5://10.0.0.1:1080#Two", now=datetime(2026, 1, 2, tzinfo=UTC))
    assert second["id"] == first["id"]
    assert len(catalog.list()) == 1
    assert second["name"] == "Two"
    assert second["added_at"] == "2026-01-01T00:00:00Z"
    assert second["last_seen_at"] == "2026-01-02T00:00:00Z"
    assert second["source"] == "manual"
    assert second["favorite"] is False
    assert second["last_used_at"] is None


VMESS = (
    "vmess://eyJ2IjoiMiIsInBzIjoiVk1lc3MiLCJhZGQiOiJ2bWVzcy5leGFtcGxlLmNvbSIsInBvcnQiOiI0NDMi"
    "LCJpZCI6IjAxMjM0NTY3LTg5YWItY2RlZi0wMTIzLTQ1Njc4OWFiY2RlZiIsImFpZCI6IjAiLCJuZXQiOiJ3cy"
    "IsInBhdGgiOiIvdm1lc3MiLCJob3N0IjoiY2RuLmV4YW1wbGUuY29tIiwidGxzIjoidGxzIiwic25pIjoidm1l"
    "c3MuZXhhbXBsZS5jb20ifQ"
)


def test_vmess_name_does_not_change_identity(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    first = catalog.add(VMESS + "#One")
    second = catalog.add(VMESS + "#Two")
    assert second["id"] == first["id"]
    assert len(catalog.list()) == 1
    renamed = catalog.rename(first["id"], "Other")
    assert renamed["id"] == first["id"]
    again = catalog.add(renamed["uri"])
    assert again["id"] == first["id"]
    assert len(catalog.list()) == 1
    assert again["name"] == "Other"


def test_rename_keeps_the_id(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    added = catalog.add("socks5://10.0.0.2:1080#Old")
    renamed = catalog.rename(added["id"], "New")
    assert renamed["id"] == added["id"]
    assert renamed["name"] == "New"
    assert catalog.get(added["id"])["name"] == "New"
    assert server_id(renamed["uri"]) == added["id"]


def test_list_orders_favorites_then_recent_then_name(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    plain = catalog.add("socks5://10.0.0.3:1#Bravo")
    older = catalog.add("socks5://10.0.0.4:1#Alpha")
    newer = catalog.add("socks5://10.0.0.5:1#Zulu")
    fav = catalog.add("socks5://10.0.0.6:1#Mike")
    catalog.update_last_used(older["id"], now=datetime(2026, 1, 1, tzinfo=UTC))
    catalog.update_last_used(newer["id"], now=datetime(2026, 6, 1, tzinfo=UTC))
    catalog.toggle_favorite(fav["id"])
    names = [row["name"] for row in catalog.list()]
    assert names == ["Mike", "Zulu", "Alpha", "Bravo"]
    assert plain["id"] != fav["id"]


def test_remove_and_missing(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    added = catalog.add("socks5://10.0.0.7:1#Gone")
    catalog.remove(added["id"])
    assert catalog.list() == []
    with pytest.raises(CatalogError) as exc:
        catalog.get(added["id"])
    assert exc.value.code == "not_found"


def test_bad_uri_is_rejected(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "servers.json")
    with pytest.raises(CatalogError) as exc:
        catalog.add("not-a-proxy")
    assert exc.value.code == "invalid_uri"
