"""Shell-completion script generation — dependency-free.

`briar completion bash|zsh` introspects the argparse tree once and emits a
static completion script with the command/subcommand/flag names baked in,
so completion is instant (no `briar` subprocess per <TAB>) and needs no
third-party package (the project is stdlib-only).

The shell text lives in `completion_templates/*.tmpl` (loaded as package
data) — this module only introspects the parser and substitutes the
`@@MARKER@@` placeholders, so no shell strings are hardcoded in the logic.

Install:
    # bash
    eval "$(briar completion bash)"        # or write to a completions.d file
    # zsh
    eval "$(briar completion zsh)"
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from typing import Dict, List

SUPPORTED_SHELLS = ("bash", "zsh")

_GLOBAL_FLAGS = ("--help", "--version", "--verbose", "--format")
_TEMPLATE_PACKAGE = "briar.completion_templates"


@lru_cache(maxsize=None)
def _template(name: str) -> str:
    """Read a `*.tmpl` from the package-data template dir (cached)."""
    return files(_TEMPLATE_PACKAGE).joinpath(f"{name}.tmpl").read_text(encoding="utf-8")


@dataclass(frozen=True)
class _CommandSpec:
    """Completion-relevant shape of one top-level command: its own flags
    plus, for subcommand commands, the per-subcommand flag lists."""

    name: str
    flags: List[str] = field(default_factory=list)
    subcommands: Dict[str, List[str]] = field(default_factory=dict)


def _flags_of(parser: argparse.ArgumentParser) -> List[str]:
    """Visible long option strings declared directly on a parser (skips
    suppressed flags and the subparsers action)."""
    out: List[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            continue
        if action.help is argparse.SUPPRESS:
            continue
        out.extend(opt for opt in action.option_strings if opt.startswith("--"))
    return sorted(set(out))


def _subparsers(parser: argparse.ArgumentParser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def introspect(commands) -> List[_CommandSpec]:
    """Build a `_CommandSpec` per registered command by replaying its
    `add_arguments` onto a throwaway parser."""
    specs: List[_CommandSpec] = []
    for name, command in commands.items():
        parser = argparse.ArgumentParser(prog=name, add_help=False)
        command.add_arguments(parser)
        sub_action = _subparsers(parser)
        subcommands: Dict[str, List[str]] = {}
        if sub_action is not None:
            for sub_name, sub_parser in sub_action.choices.items():
                subcommands[sub_name] = _flags_of(sub_parser)
        specs.append(_CommandSpec(name=name, flags=_flags_of(parser), subcommands=subcommands))
    return specs


def _fill(template_name: str, **markers: str) -> str:
    text = _template(template_name)
    for key, value in markers.items():
        text = text.replace(f"@@{key}@@", value)
    return text


def _case_block(spec: _CommandSpec, *, sub_template: str) -> str:
    """One `case` arm for a command: a leaf line for flat commands, or the
    subcommand dispatch block for commands with sub-ops."""
    if not spec.subcommands:
        return _fill("leaf", NAME=spec.name, FLAGS=" ".join(spec.flags))
    inner = "\n".join(_fill("inner", SUB=sub, FLAGS=" ".join(flags)) for sub, flags in sorted(spec.subcommands.items()))
    return _fill(
        sub_template,
        NAME=spec.name,
        SUBNAMES=" ".join(sorted(spec.subcommands)),
        INNER=inner,
    )


def _script(specs: List[_CommandSpec], *, outer_template: str, sub_template: str) -> str:
    cases = "\n".join(_case_block(spec, sub_template=sub_template) for spec in specs)
    return _fill(
        outer_template,
        COMMANDS=" ".join(spec.name for spec in specs),
        GLOBALS=" ".join(_GLOBAL_FLAGS),
        CASES=cases,
    )


def render(shell: str, commands) -> str:
    """The completion script for `shell` ('bash' or 'zsh')."""
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(f"unsupported shell: {shell!r} (choose from {', '.join(SUPPORTED_SHELLS)})")
    specs = introspect(commands)
    return _script(specs, outer_template=shell, sub_template=f"{shell}_subcommand")
