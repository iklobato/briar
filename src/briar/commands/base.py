"""Command base class + shared helpers.

Lives in its own module so sibling concretes can import it without
triggering the package `__init__` (which itself imports the concretes
to assemble the registry — a classic circular-import trap)."""

from __future__ import annotations

import argparse
import logging
import sys
from abc import ABC, abstractmethod
from typing import ClassVar, Dict

from briar.commands._enums import ExitCode

log = logging.getLogger(__name__)


class DeprecatedOptionAlias(argparse.Action):
    """A hidden option string that aliases a canonical flag.

    Stores the value into the shared dest (so existing read sites keep
    working unchanged) and prints a one-line deprecation note to stderr
    when the alias is actually used. A plain print, NOT `warnings.warn`:
    the suite runs with `filterwarnings=error`, and this is user
    guidance, not a code smell (same rationale as
    `briar.cli._warn_legacy_flags`)."""

    def __init__(self, option_strings, dest, canonical, **kwargs):
        self._canonical = canonical
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(f"note: {option_string} is deprecated; use {self._canonical} instead.", file=sys.stderr)
        setattr(namespace, self.dest, values)


def add_canonical_with_alias(
    parser: argparse.ArgumentParser,
    canonical: str,
    deprecated: str,
    *,
    dest: str,
    help: str,
    **kwargs,
) -> None:
    """Register `canonical` (shown in `--help`) and `deprecated` (hidden,
    warns on use) as two flags writing the same `dest`.

    `kwargs` (choices, default, type, …) apply to both so the deprecated
    spelling behaves identically to the canonical one. Register the
    canonical first so it owns the namespace default."""
    parser.add_argument(canonical, dest=dest, help=help, **kwargs)
    parser.add_argument(
        deprecated,
        dest=dest,
        action=DeprecatedOptionAlias,
        canonical=canonical,
        help=argparse.SUPPRESS,
        **kwargs,
    )


class Command(ABC):
    """Implementation contract for every CLI verb.

    Subclasses set `name` + `help` and implement `add_arguments` /
    `run`. Subclasses must NOT touch the global registry or argparse
    state directly — the entry point in `briar.cli` is the only
    place that does that."""

    name: str = ""
    help: str = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Default: no extra arguments."""

    @abstractmethod
    def run(self, args: argparse.Namespace) -> int:
        """Execute the command. Returns the process exit code."""

    @staticmethod
    def confirm(prompt: str) -> bool:
        """Yes/no prompt; treats EOF (piped input) as a no.

        Subclasses use this to gate destructive operations behind a
        --yes / interactive-prompt fork (`args.yes or self.confirm(...)`)."""
        try:
            return input(prompt).strip().lower() in {"y", "yes"}
        except EOFError:
            return False


# Module-level back-compat shim — `confirm()` was the historic name
# imported by sibling concretes. Kept so call-sites remain a one-symbol
# import without per-class qualification.
confirm = Command.confirm


class Subcommand(ABC):
    """One verb under a `SubcommandCommand` (e.g. `agent prfix`,
    `plan build`, `telemetry status`).

    Concrete subclasses declare their per-op argparse flags + run
    logic. The parent `SubcommandCommand` owns subparser wiring and
    dispatch — same Strategy + Registry shape used by every other
    plugin family in the codebase."""

    name: ClassVar[str] = ""
    help: ClassVar[str] = ""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Default: no extra arguments."""

    @abstractmethod
    def run(self, command: "SubcommandCommand", args: argparse.Namespace) -> int:
        """Execute the op. `command` is the parent `SubcommandCommand`
        instance, passed so ops can reuse its shared helpers."""


class SubcommandCommand(Command):
    """A `Command` whose surface is a registry of named sub-ops.

    Subclasses set three class attributes and inherit both
    `add_arguments` (builds one subparser per op) and `run` (registry
    lookup + dispatch). Adding an op = one `Subcommand` subclass + one
    registry entry; no edit to the dispatch code.

      * `dest`    — the subparser `dest` attribute (e.g. `"agent_op"`)
      * `op_noun` — human label used in the unknown-op error message
      * `ops`     — the `{name -> Subcommand}` registry"""

    dest: ClassVar[str] = "op"
    op_noun: ClassVar[str] = "op"
    ops: ClassVar[Dict[str, Subcommand]] = {}

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest=self.dest, required=True, metavar="OP")
        for op in self.ops.values():
            op.add_arguments(sub.add_parser(op.name, help=op.help))

    def run(self, args: argparse.Namespace) -> int:
        # argparse's `required=True` subparsers already reject unknown
        # ops at parse time; this guard is defensive (and keeps the
        # dispatch testable without a full parse).
        op = self.ops.get(getattr(args, self.dest, None))
        if op is None:
            known = ", ".join(sorted(self.ops))
            log.error("unknown %s: %s (known: %s)", self.op_noun, getattr(args, self.dest, None), known)
            return ExitCode.USAGE_ERROR
        return op.run(self, args)
