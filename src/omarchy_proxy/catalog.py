"""On-disk server catalog.

The file is one JSON document. Saves write a temporary file in the same
directory and then rename it, so a crash leaves the previous complete file.
The file mode is ``0600``. A broader mode is tightened on load.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from omarchy_proxy.uri import ParseError, parse_uri, serialize_uri

SCHEMA_VERSION = 1
_ID_NAME = "id"
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
        self._mutate(lambda current: current.update(data))

    def add(
        self, text: str, *, source: str = "manual", now: datetime | None = None
    ) -> dict[str, Any]:
        """Insert a URI, or refresh the existing row when the identity matches."""
        server = parse_uri(text)
        if isinstance(server, ParseError):
            raise CatalogError(server.code, server.message)
        uri = serialize_uri(server)
        identity = server_id(uri)
        stamp = _iso(now)
        found: dict[str, Any] | None = None

        def apply(data: dict[str, Any]) -> None:
            nonlocal found
            for row in data["servers"]:
                if row.get("id") == identity:
                    row["name"] = server.name
                    row["uri"] = uri
                    row["last_seen_at"] = stamp
                    found = row
                    return
            found = {
                "id": identity,
                "name": server.name,
                "uri": uri,
                "source": source,
                "favorite": False,
                "added_at": stamp,
                "last_used_at": None,
                "ping_ms": None,
                "last_seen_at": stamp,
            }
            data["servers"].append(found)

        self._mutate(apply)
        assert found is not None
        return found

    def remove(
        self, identity: str, *, active_id: str | None = None, confirm_subscription: bool = False
    ) -> str | None:
        """Drop one server. Returns a warning when a subscription tombstone was written."""
        if active_id is not None and identity == active_id:
            raise CatalogError("active_server", "disconnect before deleting the active server")
        warning: str | None = None

        def apply(data: dict[str, Any]) -> None:
            nonlocal warning
            row = next((item for item in data["servers"] if item.get("id") == identity), None)
            if row is None:
                raise CatalogError("not_found", "no server with that id")
            source = str(row.get("source") or "")
            subscription = _subscription_url(source)
            if subscription is not None and not confirm_subscription:
                raise CatalogError(
                    "confirm_delete",
                    "deleting a subscription server needs confirmation",
                )
            data["servers"] = [item for item in data["servers"] if item.get("id") != identity]
            if subscription is None:
                return
            stones = data.setdefault("tombstones", {})
            hashes = stones.setdefault(subscription, [])
            if identity not in hashes:
                hashes.append(identity)
            warning = "this server returns if tombstones for this subscription are cleared"

        self._mutate(apply)
        return warning

    def get(self, identity: str) -> dict[str, Any]:
        for row in self.load()["servers"]:
            if isinstance(row, dict) and row.get("id") == identity:
                return row
        raise CatalogError("not_found", "no server with that id")

    def list(self) -> list[dict[str, Any]]:
        rows = list(self.load()["servers"])
        rows.sort(key=_order)
        return rows

    def rename(self, identity: str, name: str) -> dict[str, Any]:
        cleaned = name.strip()
        if not cleaned:
            raise CatalogError("missing_field", "name is empty")
        found: dict[str, Any] | None = None

        def apply(data: dict[str, Any]) -> None:
            nonlocal found
            for row in data["servers"]:
                if row.get("id") == identity:
                    row["name"] = cleaned
                    row["uri"] = _uri_named(str(row["uri"]), cleaned)
                    found = row
                    return
            raise CatalogError("not_found", "no server with that id")

        self._mutate(apply)
        assert found is not None
        return found

    def toggle_favorite(self, identity: str) -> dict[str, Any]:
        found: dict[str, Any] | None = None

        def apply(data: dict[str, Any]) -> None:
            nonlocal found
            for row in data["servers"]:
                if row.get("id") == identity:
                    row["favorite"] = not bool(row.get("favorite"))
                    found = row
                    return
            raise CatalogError("not_found", "no server with that id")

        self._mutate(apply)
        assert found is not None
        return found

    def update_last_used(self, identity: str, *, now: datetime | None = None) -> dict[str, Any]:
        stamp = _iso(now)
        found: dict[str, Any] | None = None

        def apply(data: dict[str, Any]) -> None:
            nonlocal found
            for row in data["servers"]:
                if row.get("id") == identity:
                    row["last_used_at"] = stamp
                    found = row
                    return
            raise CatalogError("not_found", "no server with that id")

        self._mutate(apply)
        assert found is not None
        return found

    def _mutate(self, change: Callable[[dict[str, Any]], None]) -> None:
        with self._lock():
            if self._token is not _UNSET and self._mtime() != self._token:
                raise CatalogError("catalog_locked", "catalog changed since it was read")
            data = self._read()
            change(data)
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
    return {"version": SCHEMA_VERSION, "servers": [], "tombstones": {}}


def _subscription_url(source: str) -> str | None:
    for prefix in ("manual+subscription:", "subscription:"):
        if source.startswith(prefix):
            url = source.removeprefix(prefix)
            return url or None
    return None


def server_id(uri: str) -> str:
    """Short hash of the canonical URI with the display name ignored."""
    server = parse_uri(uri)
    if isinstance(server, ParseError):
        base = urlsplit(uri)._replace(fragment="").geturl()
    else:
        base = serialize_uri(replace(server, name=_ID_NAME))
    return hashlib.sha256(base.encode()).hexdigest()[:16]


def _uri_named(uri: str, name: str) -> str:
    server = parse_uri(uri)
    if isinstance(server, ParseError):
        raise CatalogError(server.code, server.message)
    return serialize_uri(replace(server, name=name))


def _iso(now: datetime | None) -> str:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _order(row: dict[str, Any]) -> tuple[int, int, str, str]:
    used = row.get("last_used_at")
    recent = used if isinstance(used, str) else ""
    name = row.get("name")
    return (
        0 if row.get("favorite") else 1,
        0 if recent else 1,
        "" if not recent else _invert(recent),
        name if isinstance(name, str) else "",
    )


def _invert(stamp: str) -> str:
    """Lexicographic reverse of an ISO-8601 UTC stamp."""
    return "".join(chr(ord("9") - ord(char)) if char.isdigit() else char for char in stamp)
