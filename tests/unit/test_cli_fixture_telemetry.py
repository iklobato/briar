"""The `cli` fixture must keep telemetry out of tests.

Without this, the first in-process `main()` on a machine with no
`~/.config/briar/telemetry.json` (every fresh CI runner) prints the
first-run banner to stderr, so whichever exact-stderr test runs first in
an xdist worker fails, and tests run with the real Sentry DSN at the
default `full` tier.
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
