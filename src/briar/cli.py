"""Top-level entry point.

`Cli.main` is the actual program. The module-level `main()` is a thin
shim retained because the console-script declared in pyproject.toml
binds to it — moving the binding would break installed shells."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Dict, List, Set, Tuple

from briar.commands import Command, build_registry
from briar.errors import CliError
from briar.formatting import FORMATTERS
from briar.logging import configure as configure_logging
from briar.logging import env_verbose

# Two flag families:
#   GLOBAL_FLAGS_WITH_VALUE: `--format yaml` / `--format=yaml`
#   GLOBAL_BOOL_FLAGS:       `--verbose` / `-v` (no value)
_GLOBAL_FLAGS_WITH_VALUE: Set[str] = {"--format"}
_GLOBAL_BOOL_FLAGS: Set[str] = {"--verbose", "-v"}


class Cli:
    """Argparse driver. Static-only — no instance state."""

    @classmethod
    def main(cls, argv: List[str] = []) -> int:
        raw_argv = list(argv) if argv else sys.argv[1:]
        try:
            kv, flags, remaining = cls._extract_global_flags(raw_argv)
        except CliError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        # Configure logging before anything else so module imports that
        # log at import-time still inherit the right level.
        verbose = "--verbose" in flags or "-v" in flags or env_verbose()
        configure_logging(verbose=verbose)
        log = logging.getLogger("briar.cli")
        log.debug("argv=%r verbose=%s", raw_argv, verbose)

        # Bootstrap credentials from any configured remote vault BEFORE
        # the command registry imports (which transitively trigger
        # provider / writer adapter construction that may read env vars
        # at import time). auto_bootstrap() iterates the BOOTSTRAPS
        # registry — typically a no-op locally, runs InfisicalBootstrap
        # on hosts that have the universal-auth machine identity set.
        from briar.credentials._bootstraps import auto_bootstrap

        result = auto_bootstrap()
        if not result.ok:
            log.warning("credential-bootstrap: %s failed — %s", result.backend, result.error)
        elif result.count:
            log.info("credential-bootstrap: %s hydrated %d env vars", result.backend, result.count)

        # Install the default journal. Commands that use
        # `with briar.journal.session(...)` will record + persist;
        # uninstrumented commands are unaffected (null-object default
        # protects every call site from "is journaling active" guards).
        cls._install_default_journal(log)

        # Install telemetry — opt-out via BRIAR_TELEMETRY / DO_NOT_TRACK.
        # Default is `full` (errors + usage); no PII / no values / no
        # prompts ever leave the machine. See `briar.telemetry`.
        try:
            from briar import telemetry

            telemetry.install()
            telemetry.banner_if_needed()
        except Exception:  # noqa: BLE001 — telemetry NEVER blocks the CLI
            log.debug("telemetry: install failed", exc_info=True)

        commands = build_registry()
        parser = cls._build_parser(commands)

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

    @staticmethod
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

    @classmethod
    def _extract_global_flags(cls, argv: List[str]) -> Tuple[Dict[str, str], Set[str], List[str]]:
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

    @staticmethod
    def _build_parser(commands: Dict[str, Command]) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="briar",
            description="Local extraction + scaffolding tool. No remote calls to Briar.",
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


# Console-script entry point declared in pyproject.toml.
def main(argv: List[str] = []) -> int:
    return Cli.main(argv)
