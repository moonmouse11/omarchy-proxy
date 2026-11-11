"""Command-line entry point for omarchy-proxy.

All output follows the JSONL contract defined in ``omarchy_proxy.output``:
a single JSON object per line on stdout, with ``ok: true|false`` and a
structured ``error`` block on failure. Logs go to stderr.

Currently wired: ``--version`` and the catalog commands.
"""

from __future__ import annotations

import argparse

from omarchy_proxy import __version__
from omarchy_proxy.catalog import Catalog, CatalogError
from omarchy_proxy.output import (
    EXIT_ERROR,
    CliError,
    configure_logging,
    die_unexpected,
    emit_error,
    emit_ok,
)


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
        help="Add a proxy URI to the catalog.",
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
    catalog = Catalog()
    try:
        if args.servers:
            return emit_ok(servers=catalog.list())
        if args.import_uri is not None:
            return emit_ok(server=catalog.add(args.import_uri))
        if args.remove_id is not None:
            warning = catalog.remove(args.remove_id, confirm_subscription=args.confirm)
            fields: dict[str, object] = {"removed": args.remove_id}
            if warning is not None:
                fields["warning"] = warning
            return emit_ok(**fields)
        if args.favorite_id is not None:
            return emit_ok(server=catalog.toggle_favorite(args.favorite_id))
    except CatalogError as exc:
        raise CliError(exc.code, exc.message) from exc
    raise CliError(
        code="no_command",
        message="No command given. Try --version, or see --help.",
    )


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
