"""Verify the task-1.1 contract: `python -m omarchy_proxy --version` works."""

from __future__ import annotations

import json
import subprocess
import sys


def test_version_flag_prints_json() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "omarchy_proxy", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["version"]
    assert isinstance(payload["version"], str)


def test_no_args_prints_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "omarchy_proxy"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower() or "usage:" in result.stderr.lower()
