"""The `cli` fixture must keep telemetry out of tests.

On a fresh home (every CI runner) a run must print nothing about
telemetry, send nothing (the 2.0 default tier is off) and write nothing
under the real `~/.config`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from briar import telemetry
from briar.telemetry import TelemetryTier


def test_cli_fixture_prints_no_banner_and_sends_nothing_on_a_fresh_home(cli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("BRIAR_TELEMETRY", "DO_NOT_TRACK"):
        monkeypatch.delenv(var, raising=False)

    result = cli("version")

    assert result.code == 0, result.err
    assert "anonymous error reports" not in result.err
    assert telemetry.active_config().tier is TelemetryTier.OFF
    assert not (tmp_path / ".config").exists()
