"""Catalog commands through the real CLI process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, "-m", "omarchy_proxy", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_import_then_list(tmp_path: Path) -> None:
    added = run_cli(tmp_path, "--import", "socks5://10.1.0.1:1080#One")
    assert added.returncode == 0, added.stderr
    server = json.loads(added.stdout)["server"]
    assert server["name"] == "One"
    assert server["source"] == "manual"
    listed = run_cli(tmp_path, "--servers")
    assert listed.returncode == 0
    payload = json.loads(listed.stdout)
    assert payload["ok"] is True
    assert [row["id"] for row in payload["servers"]] == [server["id"]]


def test_bad_import_is_a_structured_error(tmp_path: Path) -> None:
    result = run_cli(tmp_path, "--import", "not-a-proxy")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_uri"


def test_favorite_and_remove(tmp_path: Path) -> None:
    added = json.loads(run_cli(tmp_path, "--import", "socks5://10.1.0.2:1080#Two").stdout)
    identity = added["server"]["id"]
    starred = run_cli(tmp_path, "--favorite", identity)
    assert starred.returncode == 0
    assert json.loads(starred.stdout)["server"]["favorite"] is True
    removed = run_cli(tmp_path, "--remove", identity)
    assert removed.returncode == 0
    assert json.loads(removed.stdout)["removed"] == identity
    missing = run_cli(tmp_path, "--remove", identity)
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["error"]["code"] == "not_found"


def test_subscription_remove_needs_confirm(tmp_path: Path) -> None:
    catalog_file = tmp_path / "omarchy-proxy" / "servers.json"
    catalog_file.parent.mkdir()
    from omarchy_proxy.catalog import Catalog

    row = Catalog(catalog_file).add(
        "socks5://10.1.0.3:1080#Sub",
        source="subscription:https://feeds.example/list",
    )
    refused = run_cli(tmp_path, "--remove", row["id"])
    assert refused.returncode == 1
    assert json.loads(refused.stdout)["error"]["code"] == "confirm_delete"
    confirmed = run_cli(tmp_path, "--remove", row["id"], "--confirm")
    assert confirmed.returncode == 0
    body = json.loads(confirmed.stdout)
    assert "warning" in body
    assert Catalog(catalog_file).load()["tombstones"]["https://feeds.example/list"] == [row["id"]]
