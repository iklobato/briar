"""WP3 of the production plan (P6, P40): a CI that is already red on the base is not the fault of the ticket. Before the fix loop of a failed CI,
briar compares the jobs that fail on the PR with the jobs that failed on the latest run of the base branch, by name: when every one of them
is red on the base too, the task stops with the reason and no fix call is paid (a real run spent US$ 0.29 and 0.35 that way). A job that
fails only on the PR is still fixed."""

from __future__ import annotations

from typing import List

import pytest

from briar.extract._provider import CiState
from briar.serve.state import WorkStatus
from tests.unit.serve.ticket_fakes import Files, Items, Plans, ScriptedAgent, Setup, Setups, Step

# The calls before the fix: the plan, the build and the self review.
BEFORE_THE_FIX = 3
WITH_THE_FIX = 4


class Ticket:
    """A ticket whose first CI run fails on `failed` while the base has `base` red, then passes."""

    def with_failures(self, setups: Setups, failed: List[str], base: List[str]) -> "Result":
        setup = setups.make({})
        github = setup.sources.github_side
        github.ci[:] = [CiState.FAILED, CiState.PASSED]
        github.failed_jobs[:] = failed
        github.base_failing[:] = base
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), Step(writes={"fix.txt": "fixed\n"})])

        setup.work_on(agent, Items().jira())

        return Result(setup, agent)


class Result:
    def __init__(self, setup: Setup, agent: ScriptedAgent) -> None:
        self.setup = setup
        self.agent = agent

    def status(self) -> WorkStatus:
        return self.setup.entry("ENG-1").status


class TestFixOrNot:
    def test_a_job_red_on_the_base_too_is_not_fixed(self, setups: Setups) -> None:
        done = Ticket().with_failures(setups, ["test"], ["test"])

        assert (done.status(), len(done.agent.requests)) == (WorkStatus.BLOCKED, BEFORE_THE_FIX)
        assert "CI fails on the base branch too (test)" in done.setup.entry("ENG-1").report

    def test_several_jobs_all_red_on_the_base_are_named_in_order(self, setups: Setups) -> None:
        done = Ticket().with_failures(setups, ["test", "lint"], ["lint", "test", "deploy"])

        assert (done.status(), len(done.agent.requests)) == (WorkStatus.BLOCKED, BEFORE_THE_FIX)
        assert "CI fails on the base branch too (lint, test)" in done.setup.entry("ENG-1").report

    @pytest.mark.parametrize(
        "failed, base",
        [(["test", "lint"], ["lint"]), (["test"], ["lint"]), (["test"], [])],
        ids=["one job is new", "another job is red on the base", "the base is green"],
    )
    def test_a_failure_of_the_pr_is_fixed(self, setups: Setups, failed: List[str], base: List[str]) -> None:
        done = Ticket().with_failures(setups, failed, base)

        assert (done.status(), len(done.agent.requests)) == (WorkStatus.DONE, WITH_THE_FIX)

    def test_the_base_that_is_asked_is_the_base_branch_of_the_task(self, setups: Setups) -> None:
        done = Ticket().with_failures(setups, ["test"], ["test"])

        assert done.setup.sources.github_side.base_asked == [("acme/app", "main")]

    def test_a_failure_with_no_job_names_is_fixed_as_before(self, setups: Setups) -> None:
        """No log was found (`list_ci_failures` gave nothing): there is nothing to compare, so the check never stops a fix."""
        done = Ticket().with_failures(setups, [], ["test"])

        assert (done.status(), len(done.agent.requests)) == (WorkStatus.DONE, WITH_THE_FIX)
