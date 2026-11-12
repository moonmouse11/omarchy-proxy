import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_STATE_HOME"] = str(home / ".local/state")
    env["PATH"] = "/usr/bin:/bin"
    return env


def test_install_writes_unit_and_version(tmp_path: Path) -> None:
    home = tmp_path / "my home"
    home.mkdir()
    subprocess.run(["bash", str(ROOT / "install.sh")], check=True, env=_env(home), cwd=ROOT)
    unit = (home / ".config/systemd/user/omarchy-proxy.service").read_text(encoding="utf-8")
    assert "WantedBy=default.target" in unit
    assert "omarchy_proxy.supervisor" in unit
    assert "Restart=on-failure" in unit
    share = home / ".local/share/omarchy-proxy"
    assert f'Environment="PYTHONPATH={share}"' in unit
    assert "my\\shome" in unit
    binary = home / ".local/bin/omarchy-proxy"
    assert oct(binary.stat().st_mode & 0o777) == "0o755"
    completed = subprocess.run(
        [str(binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=_env(home),
    )
    assert json.loads(completed.stdout)["version"] == "0.1.0"
    config = home / ".config/omarchy-proxy"
    config.mkdir()
    (config / "servers.json").write_text("{}", encoding="utf-8")
    subprocess.run(["bash", str(ROOT / "uninstall.sh")], check=True, env=_env(home))
    assert not binary.exists()
    assert not (home / ".config/systemd/user/omarchy-proxy.service").exists()
    assert (config / "servers.json").exists()
    subprocess.run(["bash", str(ROOT / "install.sh")], check=True, env=_env(home), cwd=ROOT)
    subprocess.run(["bash", str(ROOT / "uninstall.sh"), "--purge"], check=True, env=_env(home))
    assert not config.exists()


def test_uninstall_stops_the_core(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".local/state/omarchy-proxy"
    state.mkdir(parents=True)
    sleep = subprocess.check_output(["sh", "-c", "command -v sleep"], text=True).strip()
    # A short-lived parent, so a killed child is reaped by init and leaves /proc.
    started = subprocess.run(
        [
            sys.executable,
            "-c",
            "from omarchy_proxy.supervisor import spawn_session; "
            "import sys; print(spawn_session([sys.argv[1], '30']))",
            sleep,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    pid = int(started.stdout.strip())
    (state / "current.json").write_text(json.dumps({"pid": pid}), encoding="utf-8")
    try:
        subprocess.run(["bash", str(ROOT / "uninstall.sh")], check=True, env=_env(home))
        assert not (state / "current.json").exists()
        assert not Path(f"/proc/{pid}").exists()
    finally:
        subprocess.run(["kill", "-KILL", "--", f"-{pid}"], check=False)


def test_cli_contract_examples(tmp_path: Path) -> None:
    pairs = (
        ("--version", '{"ok": true, "version": "0.1.0"}'),
        ("--servers", '{"ok": true, "servers": []}'),
        (
            "--settings-get",
            '{"ok": true, "settings": {"backend": "auto", "bypass": "localhost,127.0.0.0/8,::1",'
            ' "http_port": 10809, "mode": "manual", "refresh_hours": 24, "socks_port": 10808}}',
        ),
        ("--settings-set mode manual", '{"key": "mode", "ok": true, "value": "manual"}'),
    )
    env = _env(tmp_path / "home")
    binary = ROOT / ".venv/bin/omarchy-proxy"
    for args, expected in pairs:
        completed = subprocess.run(
            [str(binary), *args.split()],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout) == json.loads(expected)
