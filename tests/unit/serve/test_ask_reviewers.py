"""WP2 of the production plan (P35): the automatic review ask is OFF by default. A ready PR notifies real colleagues, so nobody is asked unless
the runbook says `serve.ask_reviewers: true`. Off, the PR is still made ready and the entry keeps no reviewers (so no reminder is owed)."""

from __future__ import annotations

import logging

import pytest

from briar.iac.runbook.models import ServeSettings
from briar.serve.state import WorkStatus
from tests.unit.serve.test_settings import Required
from tests.unit.serve.ticket_fakes import Files, Items, Plans, ScriptedAgent, Setups, Step

PR = 41


class Runs:
    """A ticket that reaches a ready PR in a repo that has usual reviewers."""

    def steps(self) -> ScriptedAgent:
        return ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()])


class TestAskReviewers:
    def test_it_is_off_by_default(self) -> None:
        assert ServeSettings.model_validate({**Required().block(), "repos": ["acme/app"]}).ask_reviewers is False

    def test_the_runbook_turns_it_on(self) -> None:
        assert ServeSettings.model_validate({**Required().block(), "repos": ["acme/app"], "ask_reviewers": True}).ask_reviewers is True

    def test_off_nobody_is_asked_and_the_pr_is_ready_with_no_reviewers_recorded(self, setups: Setups, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.ticket")
        setup = setups.make({})
        setup.sources.github_side.usual["acme/app"] = ["ann", "bob"]

        setup.work_on(Runs().steps(), Items().jira())

        entry, github = setup.entry("ENG-1"), setup.sources.github_side
        assert (entry.status, github.readies, github.asked, entry.reviewers) == (WorkStatus.DONE, [PR], [], [])
        assert "serve: ENG-1: the review ask is off (serve.ask_reviewers); nobody was asked to review" in caplog.text

    def test_on_the_usual_reviewers_are_asked(self, setups: Setups) -> None:
        setup = setups.make({"ask_reviewers": True})
        setup.sources.github_side.usual["acme/app"] = ["ann", "bob", "cy"]

        setup.work_on(Runs().steps(), Items().jira())

        assert (setup.sources.github_side.asked, setup.entry("ENG-1").reviewers) == ([(PR, ["ann", "bob"])], ["ann", "bob"])
