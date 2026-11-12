"""The supervisor records a live child and does not signal a foreign pid."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

import omarchy_proxy.supervisor as supervisor_mod
from omarchy_proxy.supervisor import Supervisor, SupervisorError, spawn_session


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


def test_setsid_wrapper_still_counts_as_our_process(monkeypatch: pytest.MonkeyPatch) -> None:
    argv = ["/bin/sleep", "30"]
    wrapper = [sys.executable, "-c", supervisor_mod._SETSID_WRAPPER, *argv]
    monkeypatch.setattr(supervisor_mod, "_cmdline", lambda pid: wrapper if pid == 5 else None)
    assert supervisor_mod._is_ours({"pid": 5, "argv": argv})
    assert not supervisor_mod._is_ours({"pid": 5, "argv": ["other"]})


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


def test_spawn_session_when_setsid_flag_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.posix_spawn

    def without_setsid(path: str, argv: list[str], env: dict[str, str], **kwargs: object) -> int:
        if kwargs.get("setsid"):
            raise NotImplementedError("posix_spawn: setsid unavailable on this platform")
        return real(path, argv, env, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("omarchy_proxy.supervisor.os.posix_spawn", without_setsid)
    pid = spawn_session([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and os.getsid(pid) != pid:
            time.sleep(0.02)
        assert os.getsid(pid) == pid
    finally:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
