"""Command base class + shared helpers.

Lives in its own module so sibling concretes can import it without
triggering the package `__init__` (which itself imports the concretes
to assemble the registry — a classic circular-import trap)."""

# No `from __future__ import annotations` here: Typer resolves a dataclass field's type
# in the module of the subclass that inherits it, so the option classes below need real
# types, not strings naming this module's imports.

import argparse
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from enum import Enum
from typing import Callable, ClassVar, Dict, Mapping, Protocol, Tuple

import typer

from briar.commands._enums import ExitCode

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Route:
    """Where a command's options go: the command and, for a command with operations,
    the namespace attribute its dispatcher reads and the operation name."""

    command: str
    op_dest: str = ""
    op: str = ""

    def path(self) -> Tuple[str, ...]:
        return (self.command, self.op) if self.op else (self.command,)

    def namespace_fields(self) -> Dict[str, str]:
        named = {"command": self.command}
        if self.op_dest:
            named[self.op_dest] = self.op
        return named


class NamespaceSource(Protocol):
    """Builds the namespace a command's `run()` takes (`briar.cli_app.NamespaceBuilder`)."""

    def from_options(self, route: Route, values: Dict[str, object]) -> argparse.Namespace: ...


@dataclass(frozen=True)
class CommandOptions:
    """A command's options on Typer (spec 002): one field per 1.x option, named after its
    1.x dest, with a `typer.Option` default. Typer builds the instance from the command
    line; `run()` gets the same namespace 1.x gave it."""

    ROUTE: ClassVar[Route] = Route("")
    # 1.x defaults Typer cannot hold, declared with a None default and made here when the
    # option is not given: a choice whose 1.x default ("") is not one of its choices, or a
    # default read from the environment at run time.
    UNSET_AS: ClassVar[Mapping[str, Callable[[], object]]] = {}

    def namespace(self, source: NamespaceSource) -> argparse.Namespace:
        return source.from_options(self.ROUTE, self.values())

    def values(self) -> Dict[str, object]:
        """The option values by 1.x dest; the Typer context is not an option."""
        named = {field.name: getattr(self, field.name) for field in fields(self)}
        return {name: self._plain(name, value) for name, value in named.items() if not isinstance(value, typer.Context)}

    def _plain(self, name: str, value: object) -> object:
        if value is None:
            make_default = self.UNSET_AS.get(name)
            return make_default() if make_default else None
        return value.value if isinstance(value, Enum) else value


class Command(ABC):
    """Implementation contract for every CLI verb.

    Subclasses set `name` + `help` and implement `add_arguments` /
    `run`. Subclasses must NOT touch the global registry or argparse
    state directly — the entry point in `briar.cli` is the only
    place that does that."""

    name: str = ""
    help: str = ""
    OP_HELP: ClassVar[Mapping[str, str]] = {}

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Default: no extra arguments."""

    @abstractmethod
    def run(self, args: argparse.Namespace) -> int:
        """Execute the command. Returns the process exit code."""

    def help_for(self, op: str) -> str:
        """The help line of one operation (`OP_HELP`); without one, the command's own."""
        return self.OP_HELP.get(op, self.help)


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

    def help_for(self, op: str) -> str:
        return self.ops[op].help

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
