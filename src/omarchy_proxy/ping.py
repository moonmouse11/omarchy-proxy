"""TCP handshake time for catalog servers. At most eight run at once."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

PING_TTL = 60


def is_fresh(row: dict[str, Any], now: float) -> bool:
    stamp = row.get("ping_at")
    if not isinstance(stamp, int | float):
        return False
    return now - float(stamp) < PING_TTL


def measure(host: str, port: int, timeout: float = 2) -> int | None:
    """Milliseconds to complete a TCP handshake, or None if it fails."""
    started = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout)
    except OSError:
        return None
    sock.close()
    return int((time.perf_counter() - started) * 1000)


def ping_many(
    rows: list[dict[str, Any]],
    measure_one: Callable[[str, int], int | None],
    *,
    limit: int = 8,
) -> tuple[list[dict[str, Any]], int]:
    """Ping every row. Returns results and the highest number in flight."""
    sem = threading.Semaphore(limit)
    lock = threading.Lock()
    current = 0
    peak = 0

    def one(row: dict[str, Any]) -> dict[str, Any]:
        nonlocal current, peak
        with sem:
            with lock:
                current += 1
                peak = max(peak, current)
            try:
                ms = measure_one(str(row["host"]), int(row["port"]))
            finally:
                with lock:
                    current -= 1
        return {"id": row["id"], "ping_ms": ms}

    with ThreadPoolExecutor(max_workers=limit) as pool:
        results = list(pool.map(one, rows))
    return results, peak
