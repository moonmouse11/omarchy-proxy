# omarchy-proxy

[![Tests](https://github.com/moonmouse11/omarchy-proxy/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/moonmouse11/omarchy-proxy/actions/workflows/test.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=moonmouse11_omarchy-proxy&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=moonmouse11_omarchy-proxy)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

![Omarchy 4](https://img.shields.io/badge/Omarchy-4-1e1e2e)
![Arch Linux](https://img.shields.io/badge/Arch_Linux-1793D1?logo=archlinux&logoColor=white)

Bar widget, panel, and window for Omarchy 4. The machine connects to a remote SOCKS5, HTTP, Shadowsocks, VLESS, VMess, Trojan, Hysteria2, or TUIC server. xray or sing-box runs the tunnel. This version does not capture all traffic: only programs that use the local proxy. A TUN mode that takes the whole machine is required follow-up work and is not in this release. The installer does not use root.

The Tests workflow has two jobs. **unit** runs the pytest suite. **docker** is the connection check: one real proxied HTTP request for each protocol, through xray 1.8.24 and sing-box 1.10.7. There is no third workflow for that.

## Install

Two pieces. The plugin is the bar UI. `install.sh` is the `omarchy-proxy` command and the user service. The bar calls that command, so both are required.

```bash
omarchy plugin add https://github.com/moonmouse11/omarchy-proxy.git --enable
~/.config/omarchy/plugins/omarchy-proxy/install.sh
```

`install.sh` does not use sudo. It copies the Python package to `~/.local/share/omarchy-proxy/`, writes `~/.local/bin/omarchy-proxy`, and writes `~/.config/systemd/user/omarchy-proxy.service`. It does not enable the service. In a terminal, if xray or sing-box is missing, it asks before `yay -S xray-bin`, `paru -S xray-bin`, or `pacman -S sing-box`. Without a terminal it only prints those commands.

`~/.local/bin` is already on the PATH of an Omarchy login. Check with `omarchy-proxy --version`. A new shell is enough when the directory was not on the PATH yet. The printed version is `0.1.0`.

The service keeps the supervisor across logout. A connection still works without it. Auto-restart then stops at logout.

```bash
systemctl --user enable --now omarchy-proxy.service
```

The bar icon is in the **right** section. Left click opens the panel. Right click opens the window. The plugin does not install a key binding. Add one in `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + SHIFT + P", "Proxy window", "omarchy-shell -q omarchy-proxy window")
o.window({ class = "^org.quickshell$", title = "^Proxy$" }, { tag = "+floating-window" })
```

## First run

```bash
omarchy-proxy --doctor
omarchy-proxy --import 'socks5://127.0.0.1:1080#laptop'
omarchy-proxy --servers
omarchy-proxy --connect <id>
omarchy-proxy --status
omarchy-proxy --disconnect
```

`--import` also takes a file of URIs, or an `http`/`https` subscription URL, which it fetches. `--subscribe URL` stores the URL and does not fetch. `--refresh` fetches one URL, or every saved URL when the argument is omitted.

**Finish setup** in the panel opens a floating terminal with the package commands. It does not run `install.sh`. Use that button only when neither core is installed. The command itself is already in place from the install step above.

`--connect` waits until the local proxy answers, up to 15 seconds. `--status` then reads the cached result. It does not open a new connection.

## Modes

`omarchy-proxy --settings-set mode <manual|system|env-only>`

- **manual** (default) changes nothing outside this program. Apps use `127.0.0.1:10808` (SOCKS) or `127.0.0.1:10809` (HTTP). Traffic that never uses those ports keeps the machine's real address.
- **system** sets the GNOME proxy keys when that schema exists. Otherwise it writes `~/.config/environment.d/omarchy-proxy.conf`, and `--status` reports `requires_relogin`. Disconnect restores the previous values and deletes only that file. Programs that ignore the system proxy are not captured.
- **env-only** does not change desktop settings. Use `omarchy-proxy --env-snippet` or `--proxychains-conf`. Those two commands print the default ports 10808 and 10809, not a custom port from settings.
- **tun** sends the machine's traffic through sing-box. xray is not used in this mode. The first connect asks for acknowledgement: a crash leaves clear internet blocked until disconnect. `install.sh` prints the `sudo` commands that install `tun-helper` and does not run them. `--doctor` reports `tun_helper_missing` until that file is in `/etc/sudoers.d`. Connect reports `route_owned` when another tunnel, including omarchy-vpn, already holds the default route, and starts nothing.

`backend` is `auto`, `xray`, or `sing-box`. Hysteria2 and TUIC always use sing-box. Ports and the bypass list (`localhost,127.0.0.0/8,::1` by default) apply on the next connect.

## Troubleshooting

- `omarchy-proxy --doctor` lists backends, ports, paths, the supervisor, and the last 50 log lines.
- `no_backend` means neither core is on `PATH`.
- `port_in_use` names the process already bound to the local port. This program does not kill it.
- `health_timeout` means the core started and the check URL did not succeed within 15 seconds. Exposure is reverted.
- `route_owned` means another tunnel already has the default route. Tun mode does not start.
- `tun_helper_missing` means `mode` is `tun` and the sudoers file for `tun-helper` is not installed. The hint is the same pair of commands `install.sh` prints and does not run.
- The core log is `~/.local/state/omarchy-proxy/core.log`, or under `$XDG_STATE_HOME` when that is set.

## Security

Catalog, settings, generated core config, state, and logs are mode `0600`. The log redacts secrets from the active server by substring. Treat `core.log` as sensitive anyway: a secret split across two reads can remain. `--status` reports health and the exit IP. It does not print server passwords. The catalog file does contain full URIs.

## Uninstall

Disconnect first, then remove the command and the plugin. The catalog and state stay.

```bash
omarchy-proxy --disconnect
~/.config/omarchy/plugins/omarchy-proxy/uninstall.sh
omarchy plugin remove omarchy-proxy
```

`uninstall.sh --purge` also deletes `~/.config/omarchy-proxy` and `~/.local/state/omarchy-proxy`.

From a git clone, `make install` and `make uninstall` call the same scripts.

## License

MIT. See `LICENSE`.
