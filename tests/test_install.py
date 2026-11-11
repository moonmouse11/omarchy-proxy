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
    started = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os,sys; pid=os.posix_spawn(sys.argv[1],[sys.argv[1],'30'],"
                "os.environ,setsid=True); print(pid)"
            ),
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
    text = (ROOT / "docs/cli-contract.md").read_text(encoding="utf-8")
    blocks = text.split("```")
    pairs: list[tuple[str, str]] = []
    for index, block in enumerate(blocks):
        command_line = block.strip()
        if not command_line.startswith("omarchy-proxy "):
            continue
        payload = ""
        for later in blocks[index + 1 :]:
            if later.startswith("json\n"):
                payload = later.removeprefix("json\n").strip()
                break
        if payload:
            pairs.append((command_line.removeprefix("omarchy-proxy "), payload))
    assert len(pairs) >= 4
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
