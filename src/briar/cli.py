"""Top-level entry point.

`main()` is the program — bound by the `briar` console-script in
pyproject.toml. Phase 13 demoted the static-only `Cli` namespace
class that used to wrap everything; helpers are module functions
now (`_extract_global_flags`, `_build_parser`,
`_install_default_journal`)."""

from __future__ import annotations

import warnings

# pydantic's plugin loader scans installed packages for plugins at import
# time. When logfire is installed but its pinned opentelemetry-sdk symbol
# drifts (ReadableLogRecord was renamed upstream), the loader emits a
# UserWarning to stderr on every CLI invocation. briar does not use
# logfire, so the warning is pure noise. Filter it before anything
# imports pydantic transitively (storage, telemetry, several adapters).
warnings.filterwarnings(
    "ignore",
    message=r".*ImportError while loading the `logfire-plugin` Pydantic plugin.*",
    category=UserWarning,
)

import argparse
import logging
import sys
from typing import Dict, List, Optional, Set, Tuple

from briar.commands import Command, CommandRegistry
from briar.errors import CliError
from briar.formatting import FORMATTERS
from briar.logging import configure as configure_logging
from briar.logging import env_verbose

# Two flag families:
#   GLOBAL_FLAGS_WITH_VALUE: `--format yaml` / `--format=yaml`
#   GLOBAL_BOOL_FLAGS:       `--verbose` / `-v` (no value)
_GLOBAL_FLAGS_WITH_VALUE: Set[str] = {"--format"}
_GLOBAL_BOOL_FLAGS: Set[str] = {"--verbose", "-v"}


def _extract_global_flags(argv: List[str]) -> Tuple[Dict[str, str], Set[str], List[str]]:
    """Pull global flags out of argv regardless of position. Handles
    `--flag value`, `--flag=value`, and bare boolean flags."""
    extracted: Dict[str, str] = {}
    bool_flags: Set[str] = set()
    rest: List[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _GLOBAL_BOOL_FLAGS:
            bool_flags.add(token)
            i += 1
            continue
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
                extracted[flag] = token[len(prefix) :]
                matched_equals = True
                break
        if matched_equals:
            i += 1
            continue
        rest.append(token)
        i += 1
    return extracted, bool_flags, rest


def _build_parser(commands: Dict[str, Command]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="briar",
        description="Local agent runner, knowledge extractor, scaffold and runbook tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--format",
        choices=list(FORMATTERS.keys()),
        default="table",
        help="output format (default: table for lists, json for single records)",
    )
    # `--verbose` is consumed in `_extract_global_flags`, but declare
    # it here too so `briar --help` mentions it.
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="enable DEBUG-level logging (also honours BRIAR_VERBOSE=1 env var)",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name, cmd in commands.items():
        sp = sub.add_parser(name, help=cmd.help)
        cmd.add_arguments(sp)
    return parser


def _install_default_journal(log: logging.Logger) -> None:
    """Install the process-wide default journal. Honours
    BRIAR_JOURNAL=off to disable entirely; otherwise wires the
    configured store (BRIAR_JOURNAL_STORE, default `file`) and the
    sinks listed in BRIAR_JOURNAL_SINKS (default `file`).

    Failures here are warnings, not errors — journaling is a
    cross-cutting concern and a misconfigured store should not
    block the actual command the user wanted to run."""
    import os

    if os.environ.get("BRIAR_JOURNAL", "").lower() in {"off", "0", "no"}:
        return
    try:
        from pathlib import Path

        from briar.journal import Journal, make_journal_store
        from briar.journal._journal import set_active_journal
        from briar.journal.sinks import JOURNAL_SINKS

        store_name = os.environ.get("BRIAR_JOURNAL_STORE", "file")
        root = Path(os.environ.get("BRIAR_JOURNAL_ROOT", "./journal"))
        store = make_journal_store(store_name, file_root=root)
        sink_names = [s.strip() for s in os.environ.get("BRIAR_JOURNAL_SINKS", "file").split(",") if s.strip()]
        sinks = [JOURNAL_SINKS[name] for name in sink_names if name in JOURNAL_SINKS]
        set_active_journal(Journal(store, sinks=sinks))
        log.debug("journal: store=%s sinks=%s root=%s", store_name, sink_names, root)
    except Exception:  # noqa: BLE001 — journaling is best-effort
        log.exception("journal: install failed; continuing without journaling")


# Console-script entry point declared in pyproject.toml.
def main(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(argv) if argv else sys.argv[1:]
    try:
        kv, flags, remaining = _extract_global_flags(raw_argv)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Configure logging before anything else so module imports that
    # log at import-time still inherit the right level.
    verbose = "--verbose" in flags or "-v" in flags or env_verbose()
    configure_logging(verbose=verbose)
    log = logging.getLogger("briar.cli")
    log.debug("argv=%r verbose=%s", raw_argv, verbose)

    # Help and no-command invocations are pure metadata: argparse
    # prints usage/help and exits at parse_args() below without ever
    # dispatching a command. Skip credential bootstrap, journal, and
    # telemetry for them so `briar -h` and `briar <cmd> -h` do no
    # network or filesystem I/O — no remote bootstrap fetch, no
    # ./journal directory created just to print help.
    # `-h`/`--help` anywhere in argv makes argparse short-circuit to a
    # help action; an empty `remaining` means no subcommand, which
    # argparse rejects with a usage error. Either way, no command runs.
    metadata_only = "-h" in raw_argv or "--help" in raw_argv or not remaining

    if not metadata_only:
        # Bootstrap credentials from any configured remote vault BEFORE
        # the command registry imports (which transitively trigger
        # provider / writer adapter construction that may read env vars
        # at import time). auto_bootstrap() iterates the BOOTSTRAPS
        # registry — typically a no-op locally; runs any configured
        # bootstrap on hosts that have its credentials set.
        from briar.credentials._bootstraps import auto_bootstrap

        # Cascade — every available bootstrap runs in registry order.
        # Log each result independently so one backend's failure doesn't
        # obscure another's success.
        for result in auto_bootstrap():
            if not result.ok:
                log.warning("credential-bootstrap: %s failed — %s", result.backend, result.error)
            elif result.count:
                log.info("credential-bootstrap: %s hydrated %d env vars", result.backend, result.count)

        # Install the default journal. Commands that use
        # `with briar.journal.session(...)` will record + persist;
        # uninstrumented commands are unaffected (null-object default
        # protects every call site from "is journaling active" guards).
        _install_default_journal(log)

        # Install telemetry — opt-out via BRIAR_TELEMETRY / DO_NOT_TRACK.
        # Default is `full` (errors + usage); no PII / no values / no
        # prompts ever leave the machine. See `briar.telemetry`.
        try:
            from briar import telemetry

            telemetry.install()
            telemetry.banner_if_needed()
        except Exception:  # noqa: BLE001 — telemetry NEVER blocks the CLI
            log.debug("telemetry: install failed", exc_info=True)

    commands = CommandRegistry.build()
    parser = _build_parser(commands)

    normalised: List[str] = []
    for flag, value in kv.items():
        normalised.extend([flag, value])
    normalised.extend(remaining)

    args = parser.parse_args(normalised)
    log.debug("dispatching command=%s args=%r", args.command, vars(args))

    # Wrap dispatch in a telemetry span so duration + outcome land
    # in one place. The span captures uncaught exceptions, scrubs
    # them, and ships an error event — then re-raises into the
    # existing CliError / KeyboardInterrupt / catch-all flow.
    from briar import telemetry

    try:
        with telemetry.command_span(args.command, args):
            rc = commands[args.command].run(args)
        return rc
    except CliError as exc:
        log.error("command %s failed: %s", args.command, exc)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        log.warning("interrupted by user (KeyboardInterrupt)")
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception:  # noqa: BLE001 — top-level catch-all logs the trace
        log.exception("command %s crashed unexpectedly", args.command)
        return 2
    finally:
        telemetry.shutdown()
