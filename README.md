# omarchy-proxy

Userspace proxy manager for Omarchy 4. A bar widget, a panel, and a window connect this machine to a remote SOCKS5, HTTP, Shadowsocks, VLESS, VMess, Trojan, Hysteria2, or TUIC server. xray or sing-box runs the tunnel. Nothing here needs root, a TUN device, or DNS changes.

## Install

```bash
git clone <this repo>
cd omarchy-proxy
make install
export PATH="$HOME/.local/bin:$PATH"
omarchy-proxy --version
```

`make install` runs `install.sh`. That script copies the Python package to `~/.local/share/omarchy-proxy/`, writes `~/.local/bin/omarchy-proxy`, and writes `~/.config/systemd/user/omarchy-proxy.service`. It does not use sudo. If xray or sing-box is missing and the script is running in a terminal, it asks before running `yay -S xray-bin`, `paru -S xray-bin`, or `pacman -S sing-box`. A non-interactive run only prints those commands.

Enable the supervisor when you want it to survive logout:

```bash
systemctl --user enable --now omarchy-proxy.service
```

Without that unit, a connection still works. Auto-restart then stops when you log out.

The bar widget is a separate Omarchy plugin. From a git remote:

```bash
omarchy plugin add <git-url>
```

The shortcut `SUPER+SHIFT+P` is in your Hyprland bindings, not in the plugin manifest.

## First run

```bash
omarchy-proxy --doctor
omarchy-proxy --import 'socks5://127.0.0.1:1080#laptop'
omarchy-proxy --servers
omarchy-proxy --connect <id>
omarchy-proxy --status
omarchy-proxy --disconnect
```

A subscription URL is imported with the same `--import` flag. `--subscribe URL` remembers the URL and does not fetch it. `--refresh` fetches one URL, or every saved URL when you omit the argument.

The panel button **Finish setup** opens a floating terminal with the same package commands when neither core is installed.

## Modes

`omarchy-proxy --settings-set mode <manual|system|env-only>`

- **manual** (default) changes nothing outside this program. Point an app at `127.0.0.1:10808` (SOCKS) or `127.0.0.1:10809` (HTTP).
- **system** sets the GNOME proxy keys when that schema exists. Otherwise it writes `~/.config/environment.d/omarchy-proxy.conf` and `--status` sets `requires_relogin`. Disconnect restores the previous values and deletes only that file.
- **env-only** does not change desktop settings. Use `omarchy-proxy --env-snippet` or `--proxychains-conf`.

`backend` is `auto`, `xray`, or `sing-box`. Hysteria2 and TUIC always use sing-box. Ports and the bypass list (`localhost,127.0.0.0/8,::1` by default) are settings too. They apply on the next connect.

## Troubleshooting

- `omarchy-proxy --doctor` lists backends, ports, paths, the supervisor, and the last 50 log lines.
- `no_backend` means neither core is on `PATH`.
- `port_in_use` names the process already bound to the local port. This program does not kill it.
- `health_timeout` means the core started but the check URL did not succeed within 15 seconds. Exposure is reverted.
- The core log is `$XDG_STATE_HOME/omarchy-proxy/core.log` (`~/.local/state/omarchy-proxy/core.log`).

## Security

Catalog, settings, generated core config, state, and logs are mode `0600`. The log redacts secrets from the active server by substring. Treat `core.log` as sensitive anyway: a secret split across two reads can remain. `--status` reports health and the exit IP. It does not print server passwords. The catalog file does contain full URIs.

## Uninstall

```bash
make uninstall
```

That stops the user unit, removes the binary, the copied package, and the unit file, and leaves `~/.config/omarchy-proxy` and `~/.local/state/omarchy-proxy` in place.

```bash
./uninstall.sh --purge
```

`--purge` also deletes the catalog, settings, and state.

## Development

```bash
make dev
make test
make lint
make typecheck
make coverage
```

Command output shapes are in `docs/cli-contract.md`. URI examples are in `docs/uri-formats.md`.

## License

MIT. See `LICENSE`.
