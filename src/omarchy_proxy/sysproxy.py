"""Apply and revert how other programs see the local proxy."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_BYPASS = "localhost,127.0.0.0/8,::1"
SOCKS_PORT = 10808
HTTP_PORT = 10809
ENV_NAME = "omarchy-proxy.conf"
_ENVIRONMENT_D = "environment.d"
_SCHEMA = "org.gnome.system.proxy"
_KEYS = (
    (_SCHEMA, "mode"),
    (_SCHEMA, "ignore-hosts"),
    (f"{_SCHEMA}.http", "host"),
    (f"{_SCHEMA}.http", "port"),
    (f"{_SCHEMA}.http", "enabled"),
    (f"{_SCHEMA}.https", "host"),
    (f"{_SCHEMA}.https", "port"),
    (f"{_SCHEMA}.socks", "host"),
    (f"{_SCHEMA}.socks", "port"),
)

Runner = Callable[[list[str]], str]


def bypass_hosts(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def no_proxy_value(raw: str) -> str:
    return ",".join(bypass_hosts(raw))


def ignore_hosts_value(raw: str) -> str:
    inner = ", ".join(f"'{host}'" for host in bypass_hosts(raw))
    return f"[{inner}]"


def proxy_env(socks_port: int, http_port: int, bypass: str) -> dict[str, str]:
    http = f"http://127.0.0.1:{http_port}"
    socks = f"socks5://127.0.0.1:{socks_port}"
    skip = no_proxy_value(bypass)
    values = {
        "http_proxy": http,
        "https_proxy": http,
        "all_proxy": socks,
        "no_proxy": skip,
    }
    values.update({key.upper(): value for key, value in values.items()})
    return values


def env_snippet(socks_port: int, http_port: int, bypass: str = DEFAULT_BYPASS) -> str:
    lines = [
        f"export {key}={value}" for key, value in proxy_env(socks_port, http_port, bypass).items()
    ]
    return "\n".join(lines) + "\n"


def proxychains_conf(socks_port: int) -> str:
    return (
        "strict_chain\n"
        "proxy_dns\n"
        "tcp_read_time_out 15000\n"
        "tcp_connect_time_out 8000\n"
        "[ProxyList]\n"
        f"socks5 127.0.0.1 {socks_port}\n"
    )


def environment_path(config_home: Path) -> Path:
    return config_home / _ENVIRONMENT_D / ENV_NAME


class Exposure:
    """One applied mode. Disconnect twice is a no-op."""

    def __init__(
        self,
        state_dir: Path,
        config_home: Path,
        runner: Runner | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.config_home = config_home
        self.runner = runner or _run

    def connect(
        self,
        mode: str,
        *,
        socks_port: int = SOCKS_PORT,
        http_port: int = HTTP_PORT,
        bypass: str = DEFAULT_BYPASS,
    ) -> dict[str, Any]:
        if mode not in {"manual", "system", "env-only"}:
            raise ValueError(mode)
        current = self._read()
        if current is not None and current.get("active"):
            self.disconnect()
        return self._apply(mode, socks_port, http_port, bypass)

    def disconnect(self) -> dict[str, Any]:
        current = self._read()
        if current is None or not current.get("active"):
            return {"active": False, "mode": "manual", "system_mode": "none"}
        if current.get("system_mode") == "gsettings":
            prior = current.get("prior")
            if isinstance(prior, dict):
                for schema, key in _KEYS:
                    saved = prior.get(f"{schema} {key}")
                    if isinstance(saved, str):
                        self.runner(["gsettings", "set", schema, key, saved])
        if current.get("system_mode") == _ENVIRONMENT_D:
            environment_path(self.config_home).unlink(missing_ok=True)
        cleared = {
            "active": False,
            "mode": "manual",
            "system_mode": "none",
            "requires_relogin": False,
        }
        self._write(cleared)
        return cleared

    def _apply(self, mode: str, socks_port: int, http_port: int, bypass: str) -> dict[str, Any]:
        if mode == "manual" or mode == "env-only":
            record = {"system_mode": "none", "requires_relogin": False}
        elif self._gsettings_ready():
            prior = {
                f"{schema} {key}": self.runner(["gsettings", "get", schema, key])
                for schema, key in _KEYS
            }
            record = {
                "system_mode": "gsettings",
                "requires_relogin": False,
                "prior": prior,
            }
        else:
            record = {"system_mode": _ENVIRONMENT_D, "requires_relogin": True}
        record["active"] = True
        record["mode"] = mode
        self._write(record)
        if record["system_mode"] == "gsettings":
            self._set_gsettings(socks_port, http_port, bypass)
        elif record["system_mode"] == _ENVIRONMENT_D:
            self._write_env(socks_port, http_port, bypass)
        return record

    def _set_gsettings(self, socks_port: int, http_port: int, bypass: str) -> None:
        host = "127.0.0.1"
        sets = [
            (_SCHEMA, "mode", "manual"),
            (_SCHEMA, "ignore-hosts", ignore_hosts_value(bypass)),
            (f"{_SCHEMA}.http", "host", host),
            (f"{_SCHEMA}.http", "port", str(http_port)),
            (f"{_SCHEMA}.http", "enabled", "true"),
            (f"{_SCHEMA}.https", "host", host),
            (f"{_SCHEMA}.https", "port", str(http_port)),
            (f"{_SCHEMA}.socks", "host", host),
            (f"{_SCHEMA}.socks", "port", str(socks_port)),
        ]
        for schema, key, value in sets:
            self.runner(["gsettings", "set", schema, key, value])

    def _gsettings_ready(self) -> bool:
        try:
            listed = self.runner(["gsettings", "list-schemas"])
        except OSError:
            return False
        return _SCHEMA in listed.split()

    def _write_env(self, socks_port: int, http_port: int, bypass: str) -> None:
        path = environment_path(self.config_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        values = proxy_env(socks_port, http_port, bypass)
        lines = [f"{key}={value}" for key, value in values.items()]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _path(self) -> Path:
        return self.state_dir / "exposure.json"

    def _read(self) -> dict[str, Any] | None:
        path = self._path()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data

    def _write(self, payload: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._path().with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._path())


def _run(args: list[str]) -> str:
    import subprocess

    completed = subprocess.run(  # noqa: S603
        args, check=False, capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.strip() or "gsettings failed")
    return completed.stdout.strip()
