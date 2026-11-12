"""Command-line entry point for omarchy-proxy.

All output follows the JSONL contract defined in ``omarchy_proxy.output``:
a single JSON object per line on stdout, with ``ok: true|false`` and a
structured ``error`` block on failure. Logs go to stderr.

Currently wired: ``--version`` and the catalog commands.
"""

from __future__ import annotations

import argparse
import sys
import time
from urllib.parse import urlsplit

from omarchy_proxy import __version__
from omarchy_proxy.catalog import Catalog, CatalogError
from omarchy_proxy.doctor import doctor_report, status_report
from omarchy_proxy.manager import Manager, ManagerError
from omarchy_proxy.output import (
    EXIT_ERROR,
    CliError,
    configure_logging,
    die_unexpected,
    emit_error,
    emit_ok,
)
from omarchy_proxy.ping import is_fresh, measure, ping_many
from omarchy_proxy.settings import Settings, SettingsError
from omarchy_proxy.subscriptions import SubscriptionError, refresh
from omarchy_proxy.supervisor import state_dir
from omarchy_proxy.sysproxy import HTTP_PORT, SOCKS_PORT, env_snippet, proxychains_conf


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omarchy-proxy",
        description="Userspace proxy manager for Omarchy 4.",
    )
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument(
        "--version",
        action="store_true",
        help="Print version as JSON and exit.",
    )
    commands.add_argument(
        "--servers",
        action="store_true",
        help="List catalog servers as JSON.",
    )
    commands.add_argument(
        "--import",
        dest="import_uri",
        metavar="URI",
        help="Add a URI, a file of URIs, or a subscription URL.",
    )
    commands.add_argument("--connect", metavar="ID", help="Connect to a catalog server.")
    commands.add_argument(
        "--disconnect",
        action="store_true",
        help="Stop the proxy and revert exposure.",
    )
    commands.add_argument(
        "--remove",
        dest="remove_id",
        metavar="ID",
        help="Remove a server by id.",
    )
    commands.add_argument(
        "--favorite",
        dest="favorite_id",
        metavar="ID",
        help="Toggle the favorite flag on a server.",
    )
    commands.add_argument(
        "--status",
        action="store_true",
        help="Show proxy health from the local cache.",
    )
    commands.add_argument(
        "--doctor",
        action="store_true",
        help="Report local setup problems.",
    )
    commands.add_argument(
        "--ack-tun",
        action="store_true",
        help="Record that tun captures the machine and blocks clear internet if the core dies.",
    )
    commands.add_argument(
        "--env-snippet",
        action="store_true",
        help="Print shell exports for the local proxy.",
    )
    commands.add_argument(
        "--proxychains-conf",
        action="store_true",
        help="Print a proxychains.conf for the local SOCKS port.",
    )
    commands.add_argument(
        "--auto-refresh",
        metavar="URL",
        help="Turn on auto-refresh for one saved subscription.",
    )
    commands.add_argument(
        "--subscribe",
        metavar="URL",
        help="Remember a subscription URL without fetching it.",
    )
    commands.add_argument(
        "--refresh",
        nargs="?",
        const="",
        dest="refresh_url",
        metavar="URL",
        help="Fetch one subscription, or every saved subscription when URL is omitted.",
    )
    commands.add_argument(
        "--settings-get",
        nargs="?",
        const="",
        dest="settings_get",
        metavar="KEY",
        help="Print one setting, or all settings when KEY is omitted.",
    )
    commands.add_argument(
        "--settings-set",
        nargs=2,
        metavar=("KEY", "VALUE"),
        help="Validate and store one setting.",
    )
    commands.add_argument(
        "--ping",
        nargs="?",
        const="",
        dest="ping_id",
        metavar="ID",
        help="Measure TCP latency for one server, or for favorites with --favorites.",
    )
    parser.add_argument(
        "--favorites",
        action="store_true",
        help="With --ping, measure favorite servers.",
    )
    parser.add_argument(
        "--clear-tombstones",
        action="store_true",
        help="With --refresh, allow previously deleted servers to return.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm deletion of a subscription server.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging on stderr.",
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.version:
        return emit_ok(version=__version__)
    if args.env_snippet:
        sys.stdout.write(env_snippet(SOCKS_PORT, HTTP_PORT))
        return 0
    if args.proxychains_conf:
        sys.stdout.write(proxychains_conf(SOCKS_PORT))
        return 0
    catalog = Catalog()
    try:
        if args.servers:
            return emit_ok(servers=catalog.list())
        if args.import_uri is not None:
            return _import(catalog, args.import_uri)
        if args.connect is not None:
            return _connect(catalog, args.connect)
        if args.disconnect:
            return emit_ok(**_manager().disconnect())
        if args.settings_get is not None:
            return _settings_get(args.settings_get)
        if args.settings_set is not None:
            key, value = args.settings_set
            return emit_ok(**Settings().set(key, value))
        if args.remove_id is not None:
            warning = catalog.remove(args.remove_id, confirm_subscription=args.confirm)
            fields: dict[str, object] = {"removed": args.remove_id}
            if warning is not None:
                fields["warning"] = warning
            return emit_ok(**fields)
        if args.favorite_id is not None:
            return emit_ok(server=catalog.toggle_favorite(args.favorite_id))
        if args.status:
            return emit_ok(**status_report())
        if args.ack_tun:
            from omarchy_proxy.tun_helper import acknowledge

            acknowledge(state_dir())
            return emit_ok(tun_acknowledged=True)
        if args.doctor:
            report = doctor_report()
            if any(item["code"] == "no_backend" for item in report["issues"]):
                raise CliError("no_backend", "neither xray nor sing-box is installed", report)
            return emit_ok(**report)
        if args.ping_id is not None:
            return _ping(catalog, args.ping_id, favorites=args.favorites)
        if args.subscribe is not None:
            return emit_ok(subscription=catalog.ensure_subscription(args.subscribe))
        if args.auto_refresh is not None:
            return emit_ok(subscription=catalog.set_auto_refresh(args.auto_refresh, True))
        if args.refresh_url is not None:
            return _refresh(catalog, args.refresh_url, clear=args.clear_tombstones)
    except CatalogError as exc:
        raise CliError(exc.code, exc.message) from exc
    except SubscriptionError as exc:
        raise CliError(exc.code, exc.message) from exc
    except ManagerError as exc:
        raise CliError(exc.code, exc.message) from exc
    except SettingsError as exc:
        raise CliError(exc.code, exc.message) from exc
    raise CliError(
        code="no_command",
        message="No command given. Try --version, or see --help.",
    )


def _manager() -> Manager:
    root = Catalog().path.parent
    return Manager(state_dir(), root)


def _connect(catalog: Catalog, identity: str) -> int:
    prefs = Settings().values()
    steps: list[str] = []

    def progress(step: str) -> None:
        steps.append(step)
        emit_ok(progress=step)

    result = _manager().connect(
        catalog,
        identity,
        mode=str(prefs["mode"]),
        preference=str(prefs["backend"]),
        socks_port=int(prefs["socks_port"]),
        http_port=int(prefs["http_port"]),
        bypass=str(prefs["bypass"]),
        progress=progress,
    )
    return emit_ok(progress="done", steps=steps, **result)


def _import(catalog: Catalog, text: str) -> int:
    from pathlib import Path

    path = Path(text)
    if path.is_file():
        added = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and not line.startswith("//"):
                added.append(catalog.add(line))
        return emit_ok(servers=added)
    if urlsplit(text).scheme in {"http", "https"}:
        return emit_ok(subscription=refresh(catalog, text))
    return emit_ok(server=catalog.add(text))


def _settings_get(key: str) -> int:
    store = Settings()
    if key == "":
        return emit_ok(settings=store.values())
    return emit_ok(key=key, value=store.get(key))


def _refresh(catalog: Catalog, url: str, *, clear: bool) -> int:
    saved = [str(item["url"]) for item in catalog.subscriptions()]
    targets = [url] if url else saved
    refreshed = []
    errors = []
    for target in targets:
        try:
            refreshed.append(refresh(catalog, target, clear_tombstones=clear))
        except (SubscriptionError, CatalogError) as exc:
            errors.append({"url": target, "code": exc.code, "message": exc.message})
    if errors and not refreshed:
        raise CliError(errors[0]["code"], errors[0]["message"], {"errors": errors})
    return emit_ok(subscriptions=refreshed, errors=errors)


def _ping(catalog: Catalog, identity: str, *, favorites: bool) -> int:
    rows = catalog.list()
    if favorites or identity == "":
        chosen = [row for row in rows if row.get("favorite")]
    else:
        chosen = [row for row in rows if row.get("id") == identity]
        if not chosen:
            raise CliError("not_found", "no server with that id")
    now = time.time()
    results = []
    pending = []
    for row in chosen:
        if is_fresh(row, now):
            results.append({"id": row["id"], "ping_ms": row.get("ping_ms")})
            continue
        parts = urlsplit(str(row["uri"]))
        pending.append({"id": row["id"], "host": parts.hostname or "", "port": parts.port or 0})
    measured, _peak = ping_many(pending, measure)
    for item in measured:
        catalog.set_ping(str(item["id"]), item["ping_ms"], now)
        results.append(item)
    return emit_ok(results=results)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)

    try:
        return _run(args)
    except CliError as err:
        return emit_error(err)
    except Exception as exc:
        die_unexpected(exc)
        return EXIT_ERROR  # unreachable; die_unexpected never returns


if __name__ == "__main__":
    raise SystemExit(main())
