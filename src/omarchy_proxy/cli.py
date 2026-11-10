"""Command-line entry point for omarchy-proxy.

All output follows the JSONL contract defined in ``omarchy_proxy.output``:
a single JSON object per line on stdout, with ``ok: true|false`` and a
structured ``error`` block on failure. Logs go to stderr.

Currently wired: ``--version``. Real subcommands land in later tasks.
"""

from __future__ import annotations

import argparse

from omarchy_proxy import __version__
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
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version as JSON and exit.",
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
