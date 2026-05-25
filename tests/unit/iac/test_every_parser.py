"""EveryParser — DSL for `every:` schedule expressions."""

from __future__ import annotations

import pytest
import schedule as _schedule

from briar.errors import ConfigError
from briar.iac.runbook.scheduler import EveryParser


@pytest.fixture
def scheduler():
    """Fresh, isolated schedule.Scheduler instance per test."""
    return _schedule.Scheduler()


class TestParser:
    @pytest.mark.parametrize("expr", [
        "minute", "5 minutes", "hour", "4 hours",
        "day", "day at 03:17", "hour at :15", "monday", "monday at 09:00",
    ])
    def test_valid_expressions_parse(self, expr: str, scheduler) -> None:
        job = EveryParser.parse(expr, scheduler=scheduler)
        assert isinstance(job, _schedule.Job)

    @pytest.mark.parametrize("expr", [
        "",
        "garbage",
        "every minute",  # the parser doesn't want "every"
        "10",  # count alone
        "fortnight",  # unknown unit
    ])
    def test_invalid_expressions_raise_configerror(self, expr: str, scheduler) -> None:
        with pytest.raises(ConfigError, match="cannot parse"):
            EveryParser.parse(expr, scheduler=scheduler)

    def test_weekday_with_count_raises(self, scheduler) -> None:
        # "2 monday" is forbidden — weekdays don't take counts.
        with pytest.raises(ConfigError, match="cannot have a count"):
            EveryParser.parse("2 monday", scheduler=scheduler)

    def test_case_insensitive(self, scheduler) -> None:
        job1 = EveryParser.parse("DAY AT 03:17", scheduler=scheduler)
        job2 = EveryParser.parse("day at 03:17", scheduler=scheduler)
        assert type(job1) is type(job2)

    def test_singular_vs_plural(self, scheduler) -> None:
        # "5 minute" and "5 minutes" both work (singular accepted with count)
        EveryParser.parse("5 minutes", scheduler=scheduler)
        EveryParser.parse("5 minute", scheduler=scheduler)
