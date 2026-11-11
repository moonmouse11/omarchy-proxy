"""On-disk server catalog.

The file is one JSON document. Saves write a temporary file in the same
directory and then rename it, so a crash leaves the previous complete file.
The file mode is ``0600``. A broader mode is tightened on load.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_UNSET = object()


class CatalogError(Exception):
    """The catalog file cannot be used."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def default_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / "omarchy-proxy" / "servers.json"


class Catalog:
    """Load and save ``servers.json``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()
        self.tightened = False
        self._token: object = _UNSET

    def load(self) -> dict[str, Any]:
        with self._lock():
            data = self._read()
            self._token = self._mtime()
            return data

    def save(self, data: dict[str, Any]) -> None:
        if data.get("version") != SCHEMA_VERSION or not isinstance(data.get("servers"), list):
            raise CatalogError("invalid_catalog", "refusing to save a catalog with the wrong shape")
        with self._lock():
            if self._token is not _UNSET and self._mtime() != self._token:
                raise CatalogError("catalog_locked", "catalog changed since it was read")
            self._write(data)
            self._token = self._mtime()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty()
        self._tighten()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CatalogError("invalid_catalog", "catalog file is not JSON") from exc
        if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
            raise CatalogError("unsupported_version", "catalog schema version is not supported")
        if not isinstance(data.get("servers"), list):
            raise CatalogError("invalid_catalog", "catalog servers field is missing")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)

    def _lock(self) -> AbstractContextManager[None]:
        return _locked(self.path)

    def _mtime(self) -> int | None:
        if not self.path.exists():
            return None
        return self.path.stat().st_mtime_ns

    def _tighten(self) -> None:
        mode = self.path.stat().st_mode & 0o777
        if mode & ~0o600:
            os.chmod(self.path, 0o600)
            self.tightened = True


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _empty() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "servers": []}
