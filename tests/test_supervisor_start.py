"""The supervisor records a live child and does not signal a foreign pid."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from omarchy_proxy.supervisor import Supervisor, SupervisorError


def _sleep_cmd() -> list[str]:
    code = "print('hello-core', flush=True); import time; time.sleep(30)"
    return [sys.executable, "-u", "-c", code]


def _stop(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)
    os.waitpid(pid, 0)


def test_start_writes_pid_and_captures_output(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path)
    state = supervisor.start(_sleep_cmd())
    try:
        saved = json.loads((tmp_path / "current.json").read_text(encoding="utf-8"))
        assert saved["pid"] == state["pid"]
        assert saved["state"] == "running"
        assert saved["argv"] == _sleep_cmd()
        assert (tmp_path / "current.json").stat().st_mode & 0o777 == 0o600
        os.kill(state["pid"], 0)
        log = tmp_path / "core.log"
        for _ in range(50):
            if log.exists() and b"hello-core" in log.read_bytes():
                break
            time.sleep(0.02)
        assert b"hello-core" in (tmp_path / "core.log").read_bytes()
    finally:
        _stop(state["pid"])


def test_second_start_is_refused(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path)
    state = supervisor.start(_sleep_cmd())
    try:
        with pytest.raises(SupervisorError) as exc:
            supervisor.start(_sleep_cmd())
        assert exc.value.code == "already_running"
    finally:
        _stop(state["pid"])


def test_stale_pid_is_replaced_without_killing_a_foreign_process(tmp_path: Path) -> None:
    foreign = Supervisor(tmp_path / "other")
    other = foreign.start([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        payload = {"state": "running", "pid": other["pid"], "argv": ["not-ours"]}
        (tmp_path / "current.json").write_text(json.dumps(payload), encoding="utf-8")
        state = Supervisor(tmp_path).start(_sleep_cmd())
        try:
            os.kill(other["pid"], 0)
            assert state["pid"] != other["pid"]
        finally:
            _stop(state["pid"])
    finally:
        _stop(other["pid"])
