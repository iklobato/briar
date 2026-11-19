"""Fixtures of the routine tests of `briar serve`."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Iterator

import pytest
import time_machine

from tests.unit.serve.ticket_fakes import Setups

# The hour of day the tests start at, so a test that reads the UTC day twice cannot meet 00:00 UTC.
NOON_HOUR = 12


@pytest.fixture(autouse=True)
def noon_utc() -> Iterator[None]:
    """Today, at noon UTC, and the time goes on: only the hour of the day is pinned (a money test that reads
    `WorkDay().today()` twice flaked at midnight otherwise)."""
    today = dt.datetime.now(dt.timezone.utc).date()
    with time_machine.travel(dt.datetime.combine(today, dt.time(NOON_HOUR), tzinfo=dt.timezone.utc), tick=True):
        yield


@pytest.fixture
def setups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setups:
    return Setups(tmp_path, monkeypatch)
