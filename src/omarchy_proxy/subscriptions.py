"""Download a subscription and merge it into the catalog in one write."""

from __future__ import annotations

import base64
import binascii
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from omarchy_proxy import __version__
from omarchy_proxy.catalog import Catalog, CatalogError

_SCHEMES = (
    "socks5",
    "socks5h",
    "http",
    "https",
    "ss",
    "vless",
    "vmess",
    "trojan",
    "hysteria2",
    "hy2",
    "tuic",
)
DEFAULT_INTERVAL = 24 * 60 * 60

MAX_BYTES = 5 * 1024 * 1024
CONNECT_TIMEOUT = 15
TOTAL_TIMEOUT = 30
MAX_REDIRECTS = 3


class SubscriptionError(Exception):
    """The subscription body was not retrieved."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS


def fetch(
    url: str,
    *,
    connect_timeout: float = CONNECT_TIMEOUT,
    total_timeout: float = TOTAL_TIMEOUT,
    max_bytes: int = MAX_BYTES,
) -> bytes:
    """GET a subscription. Raises ``SubscriptionError`` instead of partial data."""
    if urlsplit(url).scheme not in {"http", "https"}:
        raise SubscriptionError("invalid_scheme", "subscription URLs must be http or https")
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={"User-Agent": f"omarchy-proxy/{__version__}"},
    )
    opener = urllib.request.build_opener(_Redirects)
    deadline = time.monotonic() + total_timeout
    try:
        response = opener.open(request, timeout=connect_timeout)
    except TimeoutError as exc:
        raise SubscriptionError("fetch_timeout", "subscription fetch timed out") from exc
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in {301, 302, 303, 307, 308}:
            raise SubscriptionError("too_many_redirects", "more than 3 redirects") from exc
        raise SubscriptionError("fetch_failed", f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise SubscriptionError("fetch_timeout", "subscription fetch timed out") from exc
        raise SubscriptionError("fetch_failed", str(exc.reason)) from exc
    with response:
        _limit_socket(response, min(connect_timeout, _remaining(deadline)))
        chunks: list[bytes] = []
        total = 0
        while True:
            if _remaining(deadline) <= 0:
                raise SubscriptionError("fetch_timeout", "subscription fetch timed out")
            try:
                block = response.read(65536)
            except TimeoutError as exc:
                raise SubscriptionError("fetch_timeout", "subscription fetch timed out") from exc
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise SubscriptionError("too_large", "subscription is larger than 5 MB")
            chunks.append(block)
    return b"".join(chunks)


def parse_document(body: bytes) -> list[str]:
    """Return URI lines from base64, plain text, or a JSON array."""
    text = body.decode("utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list) and all(isinstance(item, str) for item in data):
            return [item.strip() for item in data if item.strip()]
    lines = _lines(text)
    if lines and _has_scheme(lines[0]):
        return lines
    decoded = _decode_base64(stripped)
    if decoded is None:
        raise SubscriptionError("invalid_document", "subscription body is not a URI list")
    return _lines(decoded)


def refresh(
    catalog: Catalog,
    url: str,
    *,
    fetcher: Any = fetch,
    now: datetime | None = None,
    clear_tombstones: bool = False,
) -> dict[str, Any]:
    """Fetch one URL and apply it. A failure leaves existing servers in place."""
    catalog.ensure_subscription(url)
    try:
        uris = parse_document(fetcher(url))
        return catalog.apply_subscription(url, uris, now=now, clear_tombstones=clear_tombstones)
    except (SubscriptionError, CatalogError) as exc:
        catalog.note_subscription_error(url, exc.message, now=now)
        raise


def stale_subscriptions(
    catalog: Catalog, now: datetime | None = None, interval: float = DEFAULT_INTERVAL
) -> list[str]:
    moment = now or datetime.now(UTC)
    due: list[str] = []
    for item in catalog.subscriptions():
        if not item.get("auto_refresh"):
            continue
        stamp = item.get("last_fetch_at")
        if not isinstance(stamp, str):
            due.append(str(item["url"]))
            continue
        fetched = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        if moment - fetched >= timedelta(seconds=interval):
            due.append(str(item["url"]))
    return due


def queue_stale(
    catalog: Catalog,
    refresh_one: Any,
    now: datetime | None = None,
    interval: float = DEFAULT_INTERVAL,
) -> list[str]:
    """Start refreshes and return without waiting for them."""
    urls = stale_subscriptions(catalog, now, interval)
    for url in urls:
        threading.Thread(target=refresh_one, args=(url,), daemon=True).start()
    return urls


def _lines(text: str) -> list[str]:
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        kept.append(line)
    return kept


def _has_scheme(line: str) -> bool:
    scheme = line.split(":", 1)[0].lower()
    return scheme in _SCHEMES


def _decode_base64(text: str) -> str | None:
    compact = "".join(text.split())
    pad = "=" * (-len(compact) % 4)
    try:
        raw = base64.b64decode(compact + pad, validate=True)
    except binascii.Error:
        try:
            raw = base64.urlsafe_b64decode(compact + pad)
        except binascii.Error:
            return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = _lines(decoded)
    if not lines or not _has_scheme(lines[0]):
        return None
    return decoded


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _limit_socket(response: object, seconds: float) -> None:
    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is not None and hasattr(sock, "settimeout"):
        sock.settimeout(max(0.01, seconds))
