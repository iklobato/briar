"""Every 1.x flag reaches its command's `run()` with the same name and value.

Spec 002 moves the parser from argparse to Typer while every command keeps
receiving the same namespace. `flags_1x.json` is the contract: it was
generated from the 1.x argparse parser (`UPDATE_GOLDEN=1`, see
`TestFlagInventory`) and the parametrised tests below read only that file,
so they keep checking the new parser after the old one is gone.

Not covered here on purpose: the global flags (`test_global_flags.py`), the
deprecated aliases and `--advanced-help` (removed by spec 002 / spec 004).
Spec 002 PR 6 deletes the `completion` entries from the file with the command.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Tuple

import pytest

INVENTORY = Path(__file__).with_name("flags_1x.json")
UPDATE_ENV = "UPDATE_GOLDEN"


class ValueCodec:
    """Turns namespace values into JSON-safe values and back, so a `Path` default and a
    `Path` the parser built compare equal after a round trip through the file."""

    def encode(self, value: Any) -> Any:
        if isinstance(value, Path):
            return {"path": str(value)}
        if isinstance(value, (list, tuple)):
            return [self.encode(item) for item in value]
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return {"repr": repr(value)}


class SampleValues:
    """Non-default text for an action: the first and second value a test passes."""

    INT_SAMPLE = 7
    FLOAT_SAMPLE = "0.5"
    FIRST, SECOND = 1, 2

    def first(self, action: argparse.Action) -> str:
        return self._pick(action, self.FIRST)

    def second(self, action: argparse.Action) -> str:
        return self._pick(action, self.SECOND)

    def convert(self, action: argparse.Action, text: str) -> Any:
        return action.type(text) if callable(action.type) else text

    def _pick(self, action: argparse.Action, nth: int) -> str:
        if action.choices:
            choices = [str(choice) for choice in action.choices]
            others = [choice for choice in choices if choice != action.default] or choices
            return others[min(nth, len(others)) - 1]
        if action.type is int:
            bump = 1 if action.default == self.INT_SAMPLE else 0
            return str(self.INT_SAMPLE + nth - 1 + bump)
        if action.type is float:
            return self.FLOAT_SAMPLE
        return f"x-{action.dest}-{nth}"


class FlagInventory:
    """Reads the 1.x argparse tree into test cases. Works only while the 1.x parser exists."""

    SKIPPED_ACTIONS = ("DeprecatedOptionAlias", "AdvancedHelpAction", "_HelpAction", "_SubParsersAction")
    TOP_LEVEL_DESTS = ("format", "verbose", "version", "command")
    APPEND_ACTIONS = ("_AppendAction", "_ReplaceDefaultAppend")

    def __init__(self, codec: ValueCodec, samples: SampleValues) -> None:
        self._codec = codec
        self._samples = samples
        self._cases: Dict[str, List[Dict[str, Any]]] = {}

    def build(self) -> Dict[str, List[Dict[str, Any]]]:
        from briar.cli import _build_parser
        from briar.commands import CommandRegistry

        self._cases = {"options": [], "defaults": [], "dash_values": []}
        self._walk(_build_parser(CommandRegistry.build()), [], [])
        return self._cases

    def _walk(self, parser: argparse.ArgumentParser, path: List[str], inherited: List[argparse.Action]) -> None:
        own = [action for action in parser._actions if type(action).__name__ not in self.SKIPPED_ACTIONS]
        subparsers = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
        if not subparsers:
            self._add_leaf(path, inherited + own)
            return
        for sub in subparsers:
            for name, child in sub.choices.items():
                self._walk(child, path + [name], inherited + own if path else [])

    def _add_leaf(self, path: List[str], actions: List[argparse.Action]) -> None:
        base = path + self._required_argv(actions)
        self._cases["defaults"].append({"path": path, "argv": base, "expected": self._bare_namespace(actions)})
        optional = [action for action in actions if action.option_strings and not action.required]
        for action in optional:
            tokens, expected = self._sample(action)
            self._cases["options"].append({"path": path, "argv": base + tokens, "dest": action.dest, "expected": self._codec.encode(expected)})
        free_text = [action for action in optional if self._takes_free_text(action)]
        if free_text:
            dashed = f"-x-{free_text[0].dest}"
            self._cases["dash_values"].append(
                {"path": path, "argv": base + [f"{free_text[0].option_strings[0]}={dashed}"], "dest": free_text[0].dest, "expected": dashed}
            )

    def _required_argv(self, actions: List[argparse.Action]) -> List[str]:
        tokens: List[str] = []
        for action in actions:
            if action.required and action.option_strings:
                tokens += [action.option_strings[0], self._samples.first(action)]
        return tokens + [self._samples.first(action) for action in actions if not action.option_strings]

    def _bare_namespace(self, actions: List[argparse.Action]) -> Dict[str, Any]:
        """What `run()` gets when only the required inputs are given: their sample values,
        and every other option's default. A SUPPRESS default leaves the parent's value."""
        expected: Dict[str, Any] = {}
        for action in actions:
            if action.dest in self.TOP_LEVEL_DESTS or action.default is argparse.SUPPRESS:
                continue
            given = action.required or not action.option_strings
            value = self._samples.convert(action, self._samples.first(action)) if given else action.default
            expected[action.dest] = self._codec.encode(value)
        return expected

    def _sample(self, action: argparse.Action) -> Tuple[List[str], Any]:
        flag = action.option_strings[0]
        kind = type(action).__name__
        if kind == "_StoreTrueAction":
            return [flag], True
        first = self._samples.first(action)
        if kind in self.APPEND_ACTIONS:
            second = self._samples.second(action)
            return [flag, first, flag, second], [self._samples.convert(action, first), self._samples.convert(action, second)]
        return [flag, first], self._samples.convert(action, first)

    def _takes_free_text(self, action: argparse.Action) -> bool:
        return type(action).__name__ == "_StoreAction" and not action.choices and action.type in (None, str)


class InventoryFile:
    """The committed `flags_1x.json`, read once per kind for parametrisation."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def cases(self, kind: str) -> List[Dict[str, Any]]:
        # A missing file or kind still collects, so `UPDATE_GOLDEN=1` can write it; the
        # inventory test fails until the file matches the parser again.
        return json.loads(self._path.read_text()).get(kind, []) if self._path.exists() else []

    def case_id(self, case: Dict[str, Any]) -> str:
        return " ".join(case["path"] + [case.get("dest", "")]).strip()


class TestFlagInventory:
    def test_inventory_file_matches_the_1x_parser(self) -> None:
        """Regenerate with UPDATE_GOLDEN=1 while the 1.x parser exists (spec 002 PR 1).
        Delete this test in spec 002 PR 6, when argparse is gone outside `extract`."""
        built = FlagInventory(ValueCodec(), SampleValues()).build()
        if os.environ.get(UPDATE_ENV):
            INVENTORY.write_text(json.dumps(built, indent=1, sort_keys=True) + "\n")
        assert built == json.loads(INVENTORY.read_text())


@pytest.mark.parametrize(
    "case", InventoryFile(INVENTORY).cases("options") + InventoryFile(INVENTORY).cases("dash_values"), ids=InventoryFile(INVENTORY).case_id
)
def test_each_flag_reaches_run_with_its_value(cli: Callable[..., SimpleNamespace], recorded_run: Dict[str, argparse.Namespace], case: Dict[str, Any]) -> None:
    run = cli(*case["argv"])

    assert run.code == 0, run.err
    assert ValueCodec().encode(getattr(recorded_run["args"], case["dest"])) == case["expected"]


@pytest.mark.parametrize("case", InventoryFile(INVENTORY).cases("defaults"), ids=InventoryFile(INVENTORY).case_id)
def test_bare_command_hands_run_its_defaults(cli: Callable[..., SimpleNamespace], recorded_run: Dict[str, argparse.Namespace], case: Dict[str, Any]) -> None:
    run = cli(*case["argv"])

    assert run.code == 0, run.err
    codec = ValueCodec()
    assert {dest: codec.encode(getattr(recorded_run["args"], dest)) for dest in case["expected"]} == case["expected"]
