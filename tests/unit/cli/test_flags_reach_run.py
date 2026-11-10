"""Every 1.x flag reaches its command's `run()` with the same name and value.

Spec 002 moves the parser from argparse to Typer while every command keeps
receiving the same namespace. `flags_1x.json` is the contract: it was
generated once from the 1.x argparse parser (spec 002 PR 1; the generator is in
commit 14869ca),
and the parametrised tests below read only that file, so they keep checking
the new parser after the old one is gone. The generator went in PR 2, when
the first commands left argparse and it had no full 1.x parser to read.

Not covered here on purpose: the global flags (`test_global_flags.py`), the
deprecated aliases and `--advanced-help` (removed by spec 002 / spec 004).
The `completion` entries went with the command in spec 002 PR 6.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List

import pytest

INVENTORY = Path(__file__).with_name("flags_1x.json")


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


class InventoryFile:
    """The committed `flags_1x.json`, read once per kind for parametrisation."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def cases(self, kind: str) -> List[Dict[str, Any]]:
        return json.loads(self._path.read_text())[kind]

    def case_id(self, case: Dict[str, Any]) -> str:
        return " ".join(case["path"] + [case.get("dest", "")]).strip()


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
