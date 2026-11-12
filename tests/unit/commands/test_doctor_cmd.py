"""`briar doctor`: the settings table, then the checks (decisions C16, F2, F3 of
docs/decisions/2026-11-10-briar-2-final-trims.md)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Dict

import pytest


@dataclass(frozen=True)
class TelemetryCase:
    """The env one run gets, and the telemetry row `doctor` must print for it."""

    env: Dict[str, str] = field(default_factory=dict)
    value: str = ""
    source: str = ""


class TestJson:
    def test_prints_one_object_with_the_settings_and_the_checks(self, cli: Callable[..., SimpleNamespace]) -> None:
        run = cli("--format", "json", "doctor")

        report = json.loads(run.out)
        assert sorted(report) == ["checks", "settings"]
        assert {row["setting"] for row in report["settings"]} >= {"company", "root", "telemetry"}
        assert {row["check"] for row in report["checks"]} >= {"python", "git remote"}

    @pytest.mark.parametrize(
        "case",
        [TelemetryCase({"BRIAR_TELEMETRY": "off"}, "off", "env (BRIAR_TELEMETRY)"), TelemetryCase({"DO_NOT_TRACK": "1"}, "off", "env (DO_NOT_TRACK)")],
        ids=["BRIAR_TELEMETRY", "DO_NOT_TRACK"],
    )
    def test_the_telemetry_row_names_its_source(self, cli: Callable[..., SimpleNamespace], case: TelemetryCase) -> None:
        run = cli("--format", "json", "doctor", env=case.env)

        telemetry = next(row for row in json.loads(run.out)["settings"] if row["setting"] == "telemetry")
        assert (telemetry["value"], telemetry["source"]) == (case.value, case.source)


class TestTable:
    def test_prints_the_settings_table_first(self, cli: Callable[..., SimpleNamespace]) -> None:
        run = cli("doctor")

        assert run.out.index("setting") < run.out.index("check")

    def test_a_bracketed_word_in_a_cell_is_printed_as_written(self, cli: Callable[..., SimpleNamespace]) -> None:
        """The table renderer read `[repo]` as markup and dropped it from the git remote hint."""
        run = cli("doctor")

        assert "set [repo] in config" in " ".join(run.out.split())
