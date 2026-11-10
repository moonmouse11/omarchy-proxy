"""Verify the CLI output contract: JSON shape, exit codes, error path."""

from __future__ import annotations

import json
import subprocess
import sys


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "omarchy_proxy", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_version_flag_prints_ok_with_version() -> None:
    result = run_cli("--version")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["version"]
    assert isinstance(payload["version"], str)
    # stderr must not contain JSON; logs belong there
    assert not result.stderr.startswith("{")


def test_no_command_emits_structured_error() -> None:
    result = run_cli()
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "no_command"
    assert "message" in payload["error"]


def test_unknown_flag_is_a_usage_error() -> None:
    result = run_cli("--does-not-exist")
    assert result.returncode == 2
    # argparse prints usage on stderr, no JSON on stdout
    assert result.stdout == ""
    assert "usage:" in result.stderr.lower()


def test_verbose_enables_debug_logging() -> None:
    result = run_cli("--verbose", "--version")
    assert result.returncode == 0
    # verbose should not pollute stdout; it goes to stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True


def test_cli_error_pickles_cleanly() -> None:
    """CliError must pickle so it can cross process boundaries later."""
    import pickle

    from omarchy_proxy.output import CliError

    err = CliError(code="x", message="y", details={"k": 1})
    round_tripped = pickle.loads(pickle.dumps(err))
    assert round_tripped.code == "x"
    assert round_tripped.message == "y"
    assert round_tripped.details == {"k": 1}
    assert round_tripped.args == ("x", "y")


def test_emit_error_details_cannot_shadow_reserved_keys() -> None:
    """A caller passing details={'code': '...'} must not corrupt the contract."""
    import io

    from omarchy_proxy.output import CliError, emit_error

    buf = io.StringIO()
    from omarchy_proxy import output

    original_emit = output.emit
    try:
        output.emit = lambda payload, *, stream=None: json.dump(payload, buf)
        err = CliError(code="real", message="real", details={"code": "SHADOW"})
        emit_error(err)
    finally:
        output.emit = original_emit

    payload = json.loads(buf.getvalue())
    assert payload["error"]["code"] == "real"
    assert payload["error"]["message"] == "real"


def test_configure_logging_is_idempotent() -> None:
    """Calling configure_logging twice must not stack handlers."""
    import logging

    from omarchy_proxy.output import configure_logging

    configure_logging()
    configure_logging()
    configure_logging()
    root = logging.getLogger("omarchy_proxy")
    assert len(root.handlers) == 1


def test_die_unexpected_logs_traceback_to_stderr() -> None:
    """Crash handler must keep the traceback on stderr, not just the JSON."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from omarchy_proxy.output import configure_logging, die_unexpected\n"
            "configure_logging()\n"
            "try:\n"
            "    raise ValueError('boom')\n"
            "except ValueError as e:\n"
            "    die_unexpected(e)\n",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["error"]["code"] == "internal_error"
    assert "ValueError" in result.stderr
    assert "Traceback" in result.stderr
