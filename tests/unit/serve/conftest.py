"""Fixtures of the routine tests of `briar serve`."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.serve.ticket_fakes import Setups


@pytest.fixture
def setups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setups:
    return Setups(tmp_path, monkeypatch)
