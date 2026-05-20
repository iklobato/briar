"""Top-level entry point.

Pre-extracts global flags (--format) from argv before argparse sees
them, so a flag in either position lands at argparse's required
"before the subcommand" slot.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Tuple

from briar.commands import build_registry
from briar.errors import CliError
from briar.formatting import FORMATTERS


_GLOBAL_FLAGS_WITH_VALUE = frozenset({"--format"})


def _extract_global_flags(argv: List[str]) -> Tuple[Dict[str, str], List[str]]:
    """Pull global flags out of argv regardless of position.

    Both `--flag value` and `--flag=value` forms are handled."""
    extracted: Dict[str, str] = {}
    rest: List[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _GLOBAL_FLAGS_WITH_VALUE:
            if i + 1 >= len(argv):
                raise CliError(f"{token} requires a value")
            extracted[token] = argv[i + 1]
            i += 2
            continue
        matched_equals = False
        for flag in _GLOBAL_FLAGS_WITH_VALUE:
            prefix = f"{flag}="
            if token.startswith(prefix):
                extracted[flag] = token[len(prefix):]
                matched_equals = True
                break
        if matched_equals:
            i += 1
            continue
        rest.append(token)
        i += 1
    return extracted, rest


def _build_parser(commands: Dict[str, "object"]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="briar",
        description="Local extraction + scaffolding tool. No remote calls to Briar.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--format", choices=list(FORMATTERS.keys()), default="table",
        help="output format (default: table for lists, json for single records)",
    )

    sub = parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND",
    )
    for name, cmd in commands.items():
        sp = sub.add_parser(name, help=cmd.help)
        cmd.add_arguments(sp)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    try:
        globals_kv, remaining = _extract_global_flags(raw_argv)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    commands = build_registry()
    parser = _build_parser(commands)

    normalised: List[str] = []
    for flag, value in globals_kv.items():
        normalised.extend([flag, value])
    normalised.extend(remaining)

    args = parser.parse_args(normalised)

    try:
        return commands[args.command].run(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
