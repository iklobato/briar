"""The `cli` fixture must keep telemetry out of tests.

On a fresh home (every CI runner) a run must print nothing about
telemetry, send nothing (the 2.0 default tier is off) and write nothing
under the real `~/.config`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Mapping, Optional

import pytest
from briar import telemetry
from briar.telemetry import TelemetryTier

PROJECT_TIER = "errors-only"


class StartupFakes:
    """Stands in for the project-config reader and `telemetry.install`, and keeps what
    `main()` handed to telemetry."""

    def __init__(self, project_config: Dict[str, object]) -> None:
        self._project_config = project_config
        self.installed_with: List[Optional[Mapping[str, object]]] = []

    def load_project_config(self, start: Optional[Path] = None) -> Dict[str, object]:
        return dict(self._project_config)

    def install(self, project_config: Optional[Mapping[str, object]] = None) -> None:
        self.installed_with.append(project_config)


def test_cli_fixture_prints_no_banner_and_sends_nothing_on_a_fresh_home(cli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("BRIAR_TELEMETRY", "DO_NOT_TRACK"):
        monkeypatch.delenv(var, raising=False)

    result = cli("doctor")

    assert result.code == 0, result.err
    assert "anonymous error reports" not in result.err
    assert telemetry.active_config().tier is TelemetryTier.OFF
    assert not (tmp_path / ".config").exists()


def test_startup_hands_the_project_config_to_telemetry(cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
    """`telemetry = "..."` in `.briar.toml` sets the tier (decision C7), so `main()` reads the
    project config before it installs telemetry and passes it on."""
    fakes = StartupFakes({"telemetry": PROJECT_TIER})
    monkeypatch.setattr("briar.config.load_project_config", fakes.load_project_config)
    monkeypatch.setattr("briar.telemetry.install", fakes.install)

    run = cli("doctor")

    assert run.code == 0, run.err
    assert fakes.installed_with == [{"telemetry": PROJECT_TIER}]
