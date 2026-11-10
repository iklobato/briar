"""The briar Typer app (spec 002, decision D29a).

Typer parses the command line; every command still gets the
`argparse.Namespace` its `run()` took in 1.x. A command on Typer declares its
options as a `CommandOptions` dataclass. A command not on Typer yet goes
through `PassThrough` to its 1.x argparse parser (and `extract` stays there
until spec 004). The app does not run the command: its result callback keeps
the namespace, and `briar.cli.main` runs it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, fields
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Type

import typer

from briar.commands.auth import AuthListOptions, AuthLoginOptions, AuthLogoutOptions, AuthRefreshOptions, AuthStatusOptions
from briar.commands.base import Command, CommandOptions, Route
from briar.commands.config import ConfigShowOptions
from briar.commands.context import (
    ContextCategoriesOptions,
    ContextDeleteOptions,
    ContextGetOptions,
    ContextGroupOptions,
    ContextListOptions,
    ContextPutOptions,
)
from briar.commands.doctor import DoctorOptions
from briar.commands.init import InitOptions
from briar.commands.journal import JournalExportOptions, JournalListOptions, JournalShowOptions
from briar.commands.runbook import RunbookExtractOptions, RunbookServeOptions, RunbookSweepOptions
from briar.commands.secrets import SecretsBootstrapOptions, SecretsDoctorOptions
from briar.commands.telemetry import (
    TelemetryErrorsOnlyOptions,
    TelemetryFullOptions,
    TelemetryOffOptions,
    TelemetryPreviewOptions,
    TelemetryResetOptions,
    TelemetryStatusOptions,
)
from briar.commands._enums import OutputFormat
from briar.commands.version import VersionOptions
from briar.config import apply_config_defaults
from briar.infer import apply_inference_defaults

ROUTES: Sequence[Type[CommandOptions]] = (
    RunbookExtractOptions,
    RunbookSweepOptions,
    RunbookServeOptions,
    ContextGroupOptions,
    ContextPutOptions,
    ContextGetOptions,
    ContextListOptions,
    ContextDeleteOptions,
    ContextCategoriesOptions,
    AuthLoginOptions,
    AuthLogoutOptions,
    AuthRefreshOptions,
    AuthListOptions,
    AuthStatusOptions,
    SecretsDoctorOptions,
    SecretsBootstrapOptions,
    JournalListOptions,
    JournalShowOptions,
    JournalExportOptions,
    VersionOptions,
    InitOptions,
    ConfigShowOptions,
    DoctorOptions,
    TelemetryStatusOptions,
    TelemetryPreviewOptions,
    TelemetryOffOptions,
    TelemetryErrorsOnlyOptions,
    TelemetryFullOptions,
    TelemetryResetOptions,
)


@dataclass(frozen=True)
class GlobalOptions:
    """The options `briar.cli.main` reads before Typer runs, so they work anywhere on the
    line. Declared here for `briar --help`, and so a bad `--format` is a usage error.
    The field names differ from every command name: Click's default map holds both."""

    output_format: OutputFormat = typer.Option(
        OutputFormat.TABLE, "--format", expose_value=False, help="output format (default: table for lists, json for single records)"
    )
    debug_logging: bool = typer.Option(False, "--verbose", "-v", expose_value=False, help="enable DEBUG-level logging (also honours BRIAR_VERBOSE=1 env var)")
    print_version: bool = typer.Option(False, "--version", "-V", expose_value=False, help="print client version and exit")


class PassThroughParser:
    """The 1.x argparse parser of the commands not on Typer yet. It is built only when one
    of them runs (about 0.1 s, mostly `extract`'s flags), and project config and git
    inference are folded into its defaults as in 1.x."""

    def __init__(self, build_parser: Callable[[], argparse.ArgumentParser], project_config: Optional[Mapping[str, object]]) -> None:
        self._build_parser = build_parser
        self._project_config = project_config

    def parse(self, argv: List[str]) -> argparse.Namespace:
        parser = self._build_parser()
        if self._project_config is not None:
            satisfied = apply_config_defaults(parser, dict(self._project_config))
            apply_inference_defaults(parser, satisfied)
        return parser.parse_args(argv)


class NamespaceBuilder:
    """The namespace a command's `run()` takes, built as 1.x built it: the global
    `format`, `verbose` and `version` (False: `main` reads those flags itself), the
    command name and the operation, then the command's own options."""

    def __init__(self, output_format: str, pass_through: PassThroughParser) -> None:
        self._output_format = output_format
        self._pass_through = pass_through

    def from_options(self, route: Route, values: Dict[str, object]) -> argparse.Namespace:
        return argparse.Namespace(format=self._output_format, verbose=False, version=False, **route.namespace_fields(), **values)

    def from_argv(self, command: str, argv: List[str]) -> argparse.Namespace:
        return self._pass_through.parse(["--format", self._output_format, command, *argv])


class ParsedValue(Protocol):
    """What Typer hands the result callback: a command's options, or a pass-through."""

    def namespace(self, source: NamespaceBuilder) -> argparse.Namespace: ...


@dataclass(frozen=True)
class PassThrough:
    """A command not on Typer yet: its arguments go, unparsed, to its 1.x argparse parser."""

    ctx: typer.Context

    def namespace(self, source: NamespaceBuilder) -> argparse.Namespace:
        return source.from_argv(self.ctx.info_name or "", list(self.ctx.args))


class ParsedCommand:
    """Typer's result callback: keeps the namespace of the command line, for `main` to run."""

    def __init__(self, builder: NamespaceBuilder) -> None:
        self._builder = builder
        self.args: Optional[argparse.Namespace] = None

    def keep(self, value: ParsedValue) -> None:
        self.args = value.namespace(self._builder)


class DefaultMap:
    """Click's default map: for each command on Typer, its options that a setting fills."""

    def __init__(self, defaults: Mapping[str, str], routes: Sequence[Type[CommandOptions]]) -> None:
        self._defaults = defaults
        self._routes = routes

    def tree(self) -> Dict[str, Any]:
        tree: Dict[str, Any] = {}
        for options_cls in self._routes:
            node = tree
            for part in options_cls.ROUTE.path():
                node = node.setdefault(part, {})
            node.update(self._filled(options_cls))
        return tree

    def _filled(self, options_cls: Type[CommandOptions]) -> Dict[str, str]:
        """An option shared with the group is filled on the group only, or the setting
        would beat a value given on the group (`briar context --store postgres get x`)."""
        names = [field.name for field in fields(options_cls) if field.name not in options_cls.INHERITED]
        return {name: self._defaults[name] for name in names if name in self._defaults}


class BriarApp:
    """Builds the Typer app: the global options, the commands on Typer, a pass-through for the rest.

    Only the command the line names (`target`) gets its real options: Typer reads every
    registered command's options on each run (about 1 ms per operation), so the others
    are registered by name and help only. They cannot run: a line that names one of
    them makes it the target. Shell completion (`EVERY_COMMAND`) gets them all.

    `rich_markup_mode=None` keeps help and usage errors as plain text: rich styling is
    forced on under GITHUB_ACTIONS or FORCE_COLOR even into a pipe, and it splits tokens
    like `--nope` with escape codes. Plain text also keeps rich out of `briar --help`."""

    HELP = "Local knowledge extractor and runbook tool."
    EVERY_COMMAND = "*"
    PASS_THROUGH_SETTINGS = {"allow_extra_args": True, "ignore_unknown_options": True, "help_option_names": []}

    def __init__(self, commands: Mapping[str, Command], parsed: ParsedCommand, target: str) -> None:
        self._commands = commands
        self._parsed = parsed
        self._target = target

    def build(self) -> typer.Typer:
        app = typer.Typer(
            name="briar",
            help=self.HELP,
            add_completion=True,
            rich_markup_mode=None,
            pretty_exceptions_enable=False,
            result_callback=self._parsed.keep,
            context_settings={"help_option_names": ["-h", "--help"]},
        )
        app.callback()(GlobalOptions)
        for name, command in self._commands.items():
            self._add(app, name, command)
        return app

    def _add(self, app: typer.Typer, name: str, command: Command) -> None:
        """`name` is the registry key, as in 1.x; the command's own `name` attribute is not read."""
        routes = [options_cls for options_cls in ROUTES if options_cls.ROUTE.command == name] if self._target in (name, self.EVERY_COMMAND) else []
        if not routes:
            app.command(name, help=command.help, context_settings=dict(self.PASS_THROUGH_SETTINGS), add_help_option=False)(PassThrough)
            return
        operations = [options_cls for options_cls in routes if options_cls.ROUTE.op]
        if not operations:
            app.command(name, help=command.help)(routes[0])
            return
        group = typer.Typer(rich_markup_mode=None)
        for group_options in [options_cls for options_cls in routes if not options_cls.ROUTE.op]:
            group.callback()(group_options)
        for options_cls in operations:
            group.command(options_cls.ROUTE.op, help=command.help_for(options_cls.ROUTE.op))(options_cls)
        app.add_typer(group, name=name, help=command.help)


@dataclass(frozen=True)
class ParseOutcome:
    """The namespace to run, or None when parsing ended the run (help, usage error)."""

    args: Optional[argparse.Namespace]
    exit_code: int


class CommandLine:
    """Parses one briar command line into the namespace its command's `run()` takes."""

    PROG = "briar"
    # Set by the shell completion script on each TAB (Click: `_<PROG>_COMPLETE`).
    COMPLETE_VAR = "_BRIAR_COMPLETE"

    def __init__(self, commands: Mapping[str, Command], output_format: str, environ: Mapping[str, str]) -> None:
        self._commands = commands
        self._output_format = output_format
        self._environ = environ

    def pass_through_commands(self) -> Dict[str, Command]:
        """The commands whose 1.x argparse parser still does the parsing."""
        migrated = {options_cls.ROUTE.command for options_cls in ROUTES}
        return {name: command for name, command in self._commands.items() if name not in migrated}

    def parse(self, argv: List[str], pass_through: Callable[[], argparse.ArgumentParser], project_config: Optional[Mapping[str, object]]) -> ParseOutcome:
        """`project_config` is None for help and usage runs: no settings are read then."""
        parsed = ParsedCommand(NamespaceBuilder(self._output_format, PassThroughParser(pass_through, project_config)))
        app = BriarApp(self._commands, parsed, self._target(argv)).build()
        try:
            app(argv, prog_name=self.PROG, default_map=self._default_map(project_config))
        except SystemExit as exc:
            return ParseOutcome(parsed.args, int(exc.code or 0))
        return ParseOutcome(parsed.args, 0)

    def _target(self, argv: List[str]) -> str:
        """The command the line names: its first word that is not an option or the value
        of `--format` (`main` has already taken the other global flags out). Shell
        completion reads the words from the environment, so it needs every command."""
        if self.COMPLETE_VAR in self._environ:
            return BriarApp.EVERY_COMMAND
        words = [token for index, token in enumerate(argv) if not token.startswith("-") and (index == 0 or argv[index - 1] != "--format")]
        return words[0] if words else ""

    def _default_map(self, project_config: Optional[Mapping[str, object]]) -> Optional[Dict[str, Any]]:
        if project_config is None:
            return None
        from briar.settings import SettingsResolver

        return DefaultMap(SettingsResolver.from_project(project_config).defaults(), ROUTES).tree()
