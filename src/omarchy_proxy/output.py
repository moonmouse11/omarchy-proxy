"""Output conventions for the omarchy-proxy CLI.

Every command emits a single JSON object per line on stdout (JSONL). Two top-level
shapes exist:

- Success: ``{"ok": true, ...command-specific fields...}``
- Error:   ``{"ok": false, "error": {"code": "...", "message": "...", ...}}``

Error codes are stable machine-readable identifiers (snake_case). Commands return:

- 0 on success
- 1 on any structured error (``ok: false`` was emitted)
- 2 on usage errors (argparse) and unexpected crashes (no JSON emitted)

The contract is documented in docs/cli-contract.md (lands with the first real
command); this module is the single place that enforces it.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

_LOG_NAME = "omarchy_proxy"


class CliError(Exception):
    """Structured CLI error. Caught at the top level and rendered as JSON.

    Plain class (not a dataclass) so that ``super().__init__(code, message)``
    populates ``args`` and the exception pickles correctly — important if a
    future supervisor ever moves work into worker processes.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details if details is not None else {}

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def emit(payload: dict[str, Any], *, stream: TextIO = sys.stdout) -> None:
    """Write one JSON object followed by a newline."""
    json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
    stream.write("\n")
    stream.flush()


def emit_ok(**fields: Any) -> int:
    """Emit a success payload and return the success exit code."""
    emit({"ok": True, **fields})
    return EXIT_OK


def emit_error(err: CliError) -> int:
    """Emit an error payload and return the error exit code.

    Reserved keys ``code`` and ``message`` are written last so a ``details``
    dict cannot shadow them — the QML contract relies on those names.
    """
    payload: dict[str, Any] = {
        "ok": False,
        "error": {**err.details, "code": err.code, "message": err.message},
    }
    emit(payload)
    return EXIT_ERROR


def die_unexpected(exc: BaseException) -> None:
    """Crash handler. Logs the traceback to stderr, emits JSON, exits.

    The QML sees a clean ``internal_error`` payload; the human gets a full
    traceback on stderr. Never returns.
    """
    log = logging.getLogger(_LOG_NAME)
    log.exception("unexpected crash: %s", exc)
    err = CliError(
        code="internal_error",
        message=f"{type(exc).__name__}: {exc}",
    )
    emit_error(err)
    sys.exit(EXIT_ERROR)


def configure_logging(*, verbose: bool = False) -> None:
    """Wire up the standard ``logging`` module.

    Logs go to stderr only; stdout is reserved for the JSON contract.
    Idempotent: a second call replaces the existing handler instead of
    adding a duplicate, so repeated invocations in one process don't
    double-print.
    """
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger(_LOG_NAME)
    root.setLevel(level)
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.propagate = False
