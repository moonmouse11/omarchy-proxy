"""Local diagnosis. Reads files and probes ports. Does not use the network."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omarchy_proxy.catalog import default_path
from omarchy_proxy.supervisor import state_dir

_HINTS = {
    "xray": "yay -S xray-bin",
    "sing-box": "pacman -S sing-box",
}
_PORTS = (10808, 10809)


def status_report(directory: Path | None = None) -> dict[str, Any]:
    """Assemble --status from files already on disk."""
    root = directory or state_dir()
    current = _read_json(root / "current.json")
    report: dict[str, Any] = {
        "state": current.get("state", "stopped") if current else "stopped",
        "health": current.get("health", "unknown") if current else "unknown",
    }
    if current and isinstance(current.get("health_error"), str):
        report["health_error"] = current["health_error"]
    geo = _read_json(root / "geo.json")
    if geo and isinstance(geo.get("query"), str):
        report["exit_ip"] = geo["query"]
        if isinstance(geo.get("country"), str):
            report["country"] = geo["country"]
    return report


def doctor_report(
    directory: Path | None = None,
    catalog_path: Path | None = None,
    *,
    lookup: Callable[[str], str | None] | None = None,
    secrets: list[str] | None = None,
) -> dict[str, Any]:
    root = directory or state_dir()
    catalog = catalog_path or default_path()
    find = lookup or _which
    backends = []
    for name, hint in _HINTS.items():
        found = find(name)
        if found:
            backends.append({"name": name, "path": found, "hint": hint})
    issues: list[dict[str, str]] = []
    if not backends:
        issues.append(
            {
                "code": "no_backend",
                "message": "neither xray nor sing-box is installed",
                "hint": "yay -S xray-bin  or  pacman -S sing-box",
            }
        )
    return {
        "backends": backends,
        "ports": [{"port": port, "free": _free(port)} for port in _PORTS],
        "paths": {
            "catalog": str(catalog),
            "state": str(root),
            "catalog_mode": _mode(catalog),
        },
        "supervisor": _read_json(root / "current.json") or {"state": "stopped"},
        "log": _tail(root / "core.log", secrets or []),
        "issues": issues,
    }


def _which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def _free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _mode(path: Path) -> str | None:
    if not path.exists():
        return None
    return oct(path.stat().st_mode & 0o777)


def _tail(path: Path, secrets: list[str]) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]
    redacted = []
    for line in lines:
        for secret in secrets:
            if secret:
                line = line.replace(secret, "***")
        redacted.append(line)
    return redacted


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_geo(directory: Path, payload: dict[str, str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "geo.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
