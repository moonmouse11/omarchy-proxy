"""User preferences. Separate from the catalog so secrets stay in servers.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from omarchy_proxy.catalog import default_path
from omarchy_proxy.sysproxy import DEFAULT_BYPASS, HTTP_PORT, SOCKS_PORT

_CHOICES = {
    "mode": ("manual", "system", "env-only", "tun"),
    "backend": ("auto", "xray", "sing-box"),
}
_DEFAULTS: dict[str, Any] = {
    "mode": "manual",
    "backend": "auto",
    "socks_port": SOCKS_PORT,
    "http_port": HTTP_PORT,
    "bypass": DEFAULT_BYPASS,
    "refresh_hours": 24,
}


class SettingsError(Exception):
    """A settings read or write was rejected."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Settings:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path().parent / "settings.json"

    def values(self) -> dict[str, Any]:
        stored = self._read()
        merged = dict(_DEFAULTS)
        merged.update(stored)
        return merged

    def get(self, key: str) -> Any:
        if key not in _DEFAULTS:
            raise SettingsError("unknown_setting", f"unknown setting: {key}")
        return self.values()[key]

    def set(self, key: str, raw: str) -> dict[str, Any]:
        value = _coerce(key, raw)
        stored = self._read()
        stored[key] = value
        self._write(stored)
        return {"key": key, "value": value}

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SettingsError("invalid_settings", "settings file is not JSON") from exc
        if not isinstance(data, dict):
            raise SettingsError("invalid_settings", "settings file is not an object")
        return {key: value for key, value in data.items() if key in _DEFAULTS}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)


def _coerce(key: str, raw: str) -> Any:
    if key not in _DEFAULTS:
        raise SettingsError("unknown_setting", f"unknown setting: {key}")
    if key in _CHOICES:
        if raw not in _CHOICES[key]:
            raise SettingsError("invalid_field", f"{key} must be one of {', '.join(_CHOICES[key])}")
        return raw
    if key in {"socks_port", "http_port", "refresh_hours"}:
        try:
            number = int(raw)
        except ValueError as exc:
            raise SettingsError("invalid_field", f"{key} must be an integer") from exc
        if key == "refresh_hours" and number < 1:
            raise SettingsError("invalid_field", "refresh_hours must be at least 1")
        if key != "refresh_hours" and not 1 <= number <= 65535:
            raise SettingsError("invalid_field", f"{key} must be a TCP port")
        return number
    return raw
