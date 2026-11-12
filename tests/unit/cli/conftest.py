"""Fixtures for the CLI characterisation tests (spec 002)."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest
from pytest_mock import MockerFixture


class Tripwire:
    """Stands in for every startup step that touches disk or network, and notes each call.
    Metadata-only invocations (`--version`, help, no command) must leave it empty."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def bootstrap(self) -> List[Any]:
        self.calls.append("credential bootstrap")
        return []

    def telemetry(self, project_config: Optional[Dict[str, object]] = None) -> None:
        self.calls.append("telemetry")

    def config(self, start: Optional[Path] = None) -> Dict[str, object]:
        self.calls.append("project config")
        return {}


@pytest.fixture
def tripwire(cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> Tripwire:
    """Runs after `cli`, so its config stub is replaced by the tripwire's."""
    wire = Tripwire()
    monkeypatch.setattr("briar.credentials._bootstraps.auto_bootstrap", wire.bootstrap)
    monkeypatch.setattr("briar.telemetry.install", wire.telemetry)
    monkeypatch.setattr("briar.config.load_project_config", wire.config)
    return wire


@pytest.fixture
def recorded_run(mocker: MockerFixture) -> Dict[str, argparse.Namespace]:
    """Replace every command's `run()` with a recorder; returns where it puts the namespace."""
    from briar.commands import CommandRegistry

    seen: Dict[str, argparse.Namespace] = {}

    def record(self: Any, args: argparse.Namespace) -> int:
        seen["args"] = args
        return 0

    for command_cls in CommandRegistry.COMMANDS:
        mocker.patch.object(command_cls, "run", record)
    return seen
