"""Privileged tun helper. Two commands: up <config> and down.

up rejects a config outside the state directory, installs the nftables table
omarchy-proxy, then replaces itself with sing-box. down deletes that table
and the omarchy-tun interface. The table is not removed when sing-box exits.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

INTERFACE = "omarchy-tun"
TABLE = "omarchy-proxy"
SUDOERS = Path("/etc/sudoers.d/omarchy-proxy")
HELPER = Path("/usr/local/bin/tun-helper")
INSTALL_COMMANDS = (
    "sudo install -m 755 ~/.local/share/omarchy-proxy/tun-helper /usr/local/bin/tun-helper",
    "sudo install -m 440 ~/.local/share/omarchy-proxy/omarchy-proxy.sudoers "
    "/etc/sudoers.d/omarchy-proxy",
)
REMOVE_SUDOERS = "sudo rm -f /etc/sudoers.d/omarchy-proxy"


class TunError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def acknowledge(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "tun-acknowledged"
    path.write_text("acknowledged\n", encoding="utf-8")
    os.chmod(path, 0o600)


def acknowledged(state_dir: Path) -> bool:
    return (state_dir / "tun-acknowledged").is_file()


def config_allowed(path: Path, state_dir: Path) -> bool:
    try:
        resolved = path.resolve()
        root = state_dir.resolve()
    except OSError:
        return False
    return resolved.parent == root and resolved.name == "config.json"


def ruleset(addresses: list[str]) -> str:
    allows = [
        '        oifname "lo" accept',
        "        meta l4proto udp udp dport { 67, 68, 546, 547 } accept",
    ]
    for address in addresses:
        family = "ip6" if ":" in address else "ip"
        allows.append(f"        {family} daddr {address} accept")
    body = "\n".join(allows)
    return (
        f"table inet {TABLE} {{\n"
        "    chain output {\n"
        "        type filter hook output priority 0; policy drop;\n"
        f"{body}\n"
        "    }\n"
        "}\n"
    )


def privileged(action: str, *extra: str) -> list[str]:
    """Root command allowed by the printed sudoers file."""
    if action not in {"up", "down"}:
        raise TunError("invalid_field", "usage: tun-helper up <config> | down")
    return ["sudo", "-n", "--preserve-env=XDG_STATE_HOME", str(HELPER), action, *extra]


def foreign_device(route_json: str) -> str | None:
    """Return the default-route device when another tunnel already owns it."""
    try:
        rows = json.loads(route_json)
    except json.JSONDecodeError as exc:
        raise TunError("route_unavailable", "ip route did not return JSON") from exc
    if not isinstance(rows, list):
        raise TunError("route_unavailable", "ip route did not return a list")
    for row in rows:
        if not isinstance(row, dict) or row.get("dst") != "default":
            continue
        device = row.get("dev")
        if not isinstance(device, str) or device == "" or device == INTERFACE:
            continue
        if device.startswith(("tun", "wg", "awg")) or "tailscale" in device:
            return device
    return None


def server_addresses(config: dict[str, Any]) -> list[str]:
    outbounds = config.get("outbounds")
    if not isinstance(outbounds, list):
        raise TunError("invalid_config", "config has no outbounds")
    host = ""
    for outbound in outbounds:
        if isinstance(outbound, dict) and outbound.get("tag") == "proxy":
            found = outbound.get("server")
            if isinstance(found, str):
                host = found
    if host == "":
        raise TunError("invalid_config", "config has no proxy server")
    if _is_ip(host):
        return [host]
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise TunError("invalid_config", f"could not resolve {host}") from exc
    addresses = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise TunError("invalid_config", f"could not resolve {host}")
    return addresses


def up(
    config_path: Path,
    state_dir: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    replace: Callable[[str, list[str]], None] | None = None,
) -> None:
    if not config_allowed(config_path, state_dir):
        raise TunError("invalid_field", "config path is outside the state directory")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise TunError("invalid_config", "config is not an object")
    text = ruleset(server_addresses(config))
    execute = run or _run
    completed = execute(["nft", "-f", "-"], input=text)
    if completed.returncode != 0:
        raise TunError("tun_failed", completed.stderr.strip() or "nft rejected the table")
    found = shutil.which("sing-box")
    if found is None and replace is None:
        raise TunError(
            "no_backend",
            "sing-box is not installed. Install it with: pacman -S sing-box",
        )
    binary = found or "sing-box"
    argv = [binary, "run", "-c", str(config_path)]
    if replace is None:
        os.execv(binary, argv)  # noqa: S606
    replace(binary, argv)


def down(*, run: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
    execute = run or _run
    execute(["nft", "delete", "table", "inet", TABLE])
    execute(["ip", "link", "delete", INTERFACE])


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "omarchy-proxy"
    try:
        if args == ["down"]:
            down()
            return 0
        if len(args) == 2 and args[0] == "up":
            up(Path(args[1]), state)
            return 0
    except TunError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print("usage: tun-helper up <config> | down", file=sys.stderr)
    return 2


def _run(argv: list[str], input: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        argv,
        check=False,
        capture_output=True,
        text=True,
        input=input,
    )


def _is_ip(host: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, host)
        return True
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, host)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
