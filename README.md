# omarchy-proxy

Userspace proxy manager for Omarchy 4. Bar widget, panel, and app window for SOCKS5, HTTP, VLESS, Shadowsocks, Trojan, VMess, Hysteria2, and TUIC — backed by xray or sing-box. No root, no TUN, no DNS manipulation.

> **Status:** early development. See `openspec/changes/add-proxy-manager/` for the spec, design, and task list.

## Install (development)

```bash
git clone <this repo>
cd omarchy-proxy
make dev          # creates .venv and installs the package editable
make test         # runs the unit test suite
make install      # installs to ~/.local/share/omarchy-proxy (no sudo)
```

## Quick start

```bash
omarchy-proxy --version
```

Real commands (`--status`, `--connect`, `--import`, …) are implemented in later tasks and documented in `docs/cli-contract.md` when they land.

## Layout

- `src/omarchy_proxy/` — Python backend (CLI and supervisor)
- `tests/` — pytest suite
- `Widget.qml`, `AppWindow.qml` — Omarchy UI surfaces (placeholders until task 10)
- `openspec/changes/add-proxy-manager/` — spec-driven design for the whole feature

## License

MIT — see `LICENSE`.
