"""Top-level entry point.

Pre-extracts global flags (--api-base / --workspace / --profile /
--format) from argv before argparse sees them, so a flag in either
position (`briar --format yaml agents list` or
`briar agents list --format yaml`) lands at argparse's required
"before the subcommand" slot. This is the cleanest way to work around
argparse's subparser constraint.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Tuple

from briar.commands import build_registry
from briar.credentials import CredentialsStore
from briar.errors import CliError
from briar.formatting import FORMATTERS
from briar.http import ApiClient
from briar.profile import (
    config_path_for,
    migrate_legacy_config_if_present,
    resolve_profile,
)


_GLOBAL_FLAGS_WITH_VALUE = frozenset({
    "--api-base", "--workspace", "--profile", "--format",
})


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
        description="Terminal client for the Briar API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--api-base", help="override the API base URL for this call",
    )
    parser.add_argument(
        "--workspace", dest="workspace_override",
        help="override the pinned workspace id for this call only",
    )
    parser.add_argument(
        "--profile", help="config profile to use (also $BRIAR_PROFILE)",
    )
    parser.add_argument(
        "--format", choices=list(FORMATTERS.keys()), default="table",
        help=(
            "output format (default: table for lists, "
            "json for single records)"
        ),
    )

    sub = parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND",
    )
    for name, cmd in commands.items():
        sp = sub.add_parser(name, help=cmd.help)
        cmd.add_arguments(sp)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    migrate_legacy_config_if_present()

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

    profile = resolve_profile(args.profile)
    store = CredentialsStore(config_path_for(profile), profile)
    if args.api_base:
        store.creds.api_base = args.api_base
    if args.workspace_override:
        # Per-call overrides are intentionally NOT persisted; flip
        # the default via `briar workspace use` or `briar config set`.
        store.creds.workspace = args.workspace_override

    client = ApiClient(store)

    try:
        return commands[args.command].run(client, args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
