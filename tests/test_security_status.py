"""--status does not add a secret that is not already in the catalog URI."""

from __future__ import annotations

import json
from pathlib import Path

from omarchy_proxy.catalog import Catalog
from omarchy_proxy.cli import main
from omarchy_proxy.doctor import status_report


def test_status_omits_the_proxy_password(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    secret = "s3cret-password"
    Catalog().add(f"socks5://user:{secret}@example.com:1080#named")
    assert main(["--status"]) == 0
    report = status_report()
    assert secret not in json.dumps(report)
    assert "uri" not in report
    listed = json.dumps(Catalog().list())
    assert secret in listed
    catalog = tmp_path / "config" / "omarchy-proxy" / "servers.json"
    assert catalog.stat().st_mode & 0o777 == 0o600
