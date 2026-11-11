#!/bin/bash
# Copy the backend to ~/.local and write the systemd --user unit.
# Package managers run only after an explicit yes on a terminal.

set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
home_dir=${HOME:?HOME is required}
prefix="$home_dir/.local"
share="$prefix/share/omarchy-proxy"
config_root=${XDG_CONFIG_HOME:-$home_dir/.config}
state_root=${XDG_STATE_HOME:-$home_dir/.local/state}
unit_dir="$config_root/systemd/user"
python=$(command -v python3)

mkdir -p "$share" "$prefix/bin" "$unit_dir" "$state_root/omarchy-proxy"
rm -rf "$share/omarchy_proxy"
cp -a "$root/src/omarchy_proxy" "$share/omarchy_proxy"
find "$share/omarchy_proxy" -type d -name __pycache__ -prune -exec rm -rf {} +

cat > "$prefix/bin/omarchy-proxy" << EOF
#!/bin/sh
export PYTHONPATH="$share"
exec "$python" -m omarchy_proxy "\$@"
EOF
chmod 755 "$prefix/bin/omarchy-proxy"

escape() {
    printf '%s' "${1// /\\s}"
}
state_dir="$state_root/omarchy-proxy"
exec_start="$(escape "$python") -m omarchy_proxy.supervisor $(escape "$state_dir")"
unit=$(PYTHONPATH="$root/src" "$python" -c 'from omarchy_proxy.supervisor import unit_text; import sys; print(unit_text(sys.argv[1]), end="")' "$exec_start")
if [[ $share == *" "* ]]; then
    environment="Environment=\"PYTHONPATH=$share\""
else
    environment="Environment=PYTHONPATH=$share"
fi
unit=${unit/$'[Service]\n'/"[Service]
$environment
"}
printf '%s' "$unit" > "$unit_dir/omarchy-proxy.service"

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

offer() {
    local binary=$1 manager=$2 package=$3
    if command -v "$binary" >/dev/null 2>&1; then
        return
    fi
    if ! command -v "$manager" >/dev/null 2>&1; then
        return
    fi
    printf 'Missing %s. Install with: %s -S %s\n' "$binary" "$manager" "$package" >&2
    if [[ ! -t 0 ]]; then
        return
    fi
    printf 'Run that command now? [y/N] ' >&2
    local answer
    read -r answer || return
    if [[ $answer == y || $answer == Y ]]; then
        "$manager" -S "$package"
    fi
}

offer xray yay xray-bin
offer xray paru xray-bin
offer sing-box pacman sing-box

printf 'Installed. Next: export PATH="$HOME/.local/bin:$PATH" && omarchy-proxy --version\n'
