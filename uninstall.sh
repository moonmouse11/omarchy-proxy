#!/bin/bash
# Stop the supervisor and remove installed files.
# Catalog, settings, and state stay unless --purge is passed.

set -euo pipefail

home_dir=${HOME:?HOME is required}
python=$(command -v python3)
prefix="$home_dir/.local"
share="$prefix/share/omarchy-proxy"
config_root=${XDG_CONFIG_HOME:-$home_dir/.config}
state_root=${XDG_STATE_HOME:-$home_dir/.local/state}
unit="$config_root/systemd/user/omarchy-proxy.service"
purge=0

if [[ ${1:-} == --purge ]]; then
    purge=1
elif [[ -n ${1:-} ]]; then
    printf 'usage: uninstall.sh [--purge]\n' >&2
    exit 2
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now omarchy-proxy.service >/dev/null 2>&1 || true
fi

stop_group() {
    local pid=$1
    if [[ ! $pid =~ ^[0-9]+$ ]] || [[ ! -d /proc/$pid ]]; then
        return
    fi
    kill -TERM -- "-$pid" 2>/dev/null || true
    local waited=0
    while [[ $waited -lt 30 && -d /proc/$pid ]]; do
        sleep 0.1
        waited=$((waited + 1))
    done
    if [[ -d /proc/$pid ]]; then
        kill -KILL -- "-$pid" 2>/dev/null || true
    fi
}

pidfile="$state_root/omarchy-proxy/supervisor.pid"
if [[ -f $pidfile ]]; then
    pid=$(tr -d '[:space:]' < "$pidfile" || true)
    if [[ -r /proc/$pid/cmdline ]]; then
        cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" || true)
        if [[ $cmdline == *omarchy_proxy* ]]; then
            stop_group "$pid"
        fi
    fi
    rm -f "$pidfile"
fi

current="$state_root/omarchy-proxy/current.json"
if [[ -f $current ]]; then
    core_pid=$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid",""))' "$current" 2>/dev/null || true)
    stop_group "$core_pid"
    rm -f "$current"
fi

rm -f "$unit" "$prefix/bin/omarchy-proxy"
rm -rf "$share"

if [[ $purge -eq 1 ]]; then
    rm -rf "$config_root/omarchy-proxy" "$state_root/omarchy-proxy"
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi
