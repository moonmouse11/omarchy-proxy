#!/bin/bash
# Copy the backend to ~/.local and write the systemd --user unit.
# Package managers run only after an explicit yes on a terminal.

set -euo pipefail

with_helper=0
if [[ ${1:-} == --with-helper ]]; then
    with_helper=1
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
home_dir=${HOME:?HOME is required}
prefix="$home_dir/.local"
share="$prefix/share/omarchy-proxy"
config_root=${XDG_CONFIG_HOME:-$home_dir/.config}
state_root=${XDG_STATE_HOME:-$home_dir/.local/state}
unit_dir="$config_root/systemd/user"
python=$(command -v python3)

mkdir -p "$share" "$prefix/bin" "$unit_dir" "$state_root/omarchy-proxy"
if ! rm -rf "$share/omarchy_proxy" 2>/dev/null; then
    sudo rm -rf "$share/omarchy_proxy"
fi
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

cat > "$share/tun-helper" << EOF
#!/bin/sh
export PYTHONPATH="$share"
export PYTHONDONTWRITEBYTECODE=1
exec "$python" -B -m omarchy_proxy.tun_helper "\$@"
EOF
chmod 755 "$share/tun-helper"
user_name=$(id -un)
cat > "$share/omarchy-proxy.sudoers" << EOF
Defaults!/usr/local/bin/tun-helper env_keep += "XDG_STATE_HOME"
$user_name ALL=(root) NOPASSWD:SETENV: /usr/local/bin/tun-helper up *, /usr/local/bin/tun-helper down
EOF
chmod 644 "$share/omarchy-proxy.sudoers"
plugin_dir="$config_root/omarchy/plugins/omarchy-proxy"
if [[ $with_helper -eq 1 ]]; then
    mkdir -p "$plugin_dir"
    for name in manifest.json Widget.qml Panel.qml AppWindow.qml Client.js; do
        cp -a "$root/$name" "$plugin_dir/$name"
    done
    section=right
    if [[ -t 0 ]]; then
        printf 'Bar section (left, centerBegin, center, centerEnd, tray, right) [%s]: ' "$section" >&2
        read -r section || section=right
        section=${section:-right}
        case $section in
            left | centerBegin | center | centerEnd | tray | right) ;;
            *)
                printf 'unknown bar section: %s\n' "$section" >&2
                exit 1
                ;;
        esac
    fi
    if [[ ${OMARCHY_PROXY_SKIP_ENABLE:-} != 1 ]] && command -v omarchy >/dev/null 2>&1; then
        shell_up=0
        if omarchy-shell shell rescanPlugins >/dev/null 2>&1; then
            shell_up=1
        fi
        discovered=0
        if [[ $shell_up -eq 1 ]]; then
            for _ in $(seq 1 40); do
                if omarchy plugin list --json 2>/dev/null | jq -e 'any(.[]; .id == "omarchy-proxy")' >/dev/null; then
                    discovered=1
                    break
                fi
                sleep 0.05
            done
        fi
        if [[ $discovered -eq 1 ]]; then
            omarchy plugin enable omarchy-proxy --section "$section" || shell_up=0
        fi
        if [[ $shell_up -eq 0 || $discovered -eq 0 ]]; then
            printf 'omarchy-shell is not responding. Files are in %s. Restart the shell, then: omarchy plugin enable omarchy-proxy --section %s\n' "$plugin_dir" "$section" >&2
        fi
    fi
fi

if [[ $with_helper -eq 1 || -t 0 ]]; then
    sudo visudo -cf "$share/omarchy-proxy.sudoers"
    sudo install -m 755 "$share/tun-helper" /usr/local/bin/tun-helper
    sudo install -m 440 "$share/omarchy-proxy.sudoers" /etc/sudoers.d/omarchy-proxy
    printf 'Installed. Proxy is in the %s section of the bar. Next: omarchy-proxy --version\n' "${section:-right}"
else
    printf '%s\n' \
        "sudo install -m 755 \"$share/tun-helper\" /usr/local/bin/tun-helper" \
        "sudo install -m 440 \"$share/omarchy-proxy.sudoers\" /etc/sudoers.d/omarchy-proxy"
    printf 'Installed. The commands above are not run. Next: export PATH="$HOME/.local/bin:$PATH" && omarchy-proxy --version\n'
fi
