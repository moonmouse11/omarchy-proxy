"""Command-line entry point for omarchy-proxy.

Phase 1.1: only --version is wired up. Real subcommands land in later tasks.
"""

from __future__ import annotations

import argparse
import json
import sys

from omarchy_proxy import __version__


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        json.dump({"version": __version__}, sys.stdout)
        sys.stdout.write("\n")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
