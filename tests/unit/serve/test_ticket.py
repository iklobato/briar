"""The ticket routine of `briar serve` (spec 003 PR 2: S18 to S27) end to end, with real
git against a local origin, a scripted agent, and GitHub and Jira fakes that record
what briar writes."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from briar.extract._provider import CiState
from briar.serve.flow import TaskFlow
from briar.serve.state import WorkStatus
from tests.unit.serve.ticket_fakes import ISSUE, FakeGithubSide, Files, Items, Plans, ScriptedAgent, Setups, Step

PR = FakeGithubSide.NUMBER
MAX_USD = 5.0
STEP_COST = 0.1
THIRD_STEP, FOURTH_STEP = 2, 3
# The text in a recorded (repo, number, text) comment.
TEXT = 2


class TestHappyPath:
    def test_every_step_of_a_ticket_is_logged(self, setups: Setups, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.flow")
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        lines = [record.getMessage().strip() for record in caplog.records]
        link = f"https://github.com/acme/app/pull/{PR}"
        assert lines == ["serve: ENG-1 is planned", f"serve: ENG-1 is pr-open {link}", f"serve: ENG-1 is done {link}"]

    def test_a_jira_ticket_goes_from_plan_to_green_ci(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        tracker, github = setup.sources.tracker_side, setup.sources.github_side
        assert [key for key, _ in tracker.comments] == ["ENG-1", "ENG-1"]
        assert tracker.comments[0][1].startswith("briar plan for ENG-1:\n\nAdd the feature file\n\nSteps:\n1. write feature.txt")
        assert tracker.comments[1][1] == f"briar opened https://github.com/acme/app/pull/{PR}"
        assert tracker.moves == [("ENG-1", "In Progress"), ("ENG-1", "In Review")]
        repo, draft = github.pulls[0]
        assert (repo, draft.head, draft.base, draft.title, draft.draft) == ("acme/app", "briar/ENG-1", "main", "ENG-1: Add the feature", False)
        assert "Ticket: ENG-1" in draft.body
        assert "briar/ENG-1" in setup.origin.branches()
        entry = setup.entry("ENG-1")
        assert (entry.status, entry.pr, entry.branch) == (WorkStatus.DONE, PR, "briar/ENG-1")

    def test_the_agent_gets_the_worktree_the_budget_and_read_only_steps(self, setups: Setups, tmp_path: Path) -> None:
        setup = setups.make({})
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()])

        setup.work_on(agent, Items().jira())

        plan, build, review = agent.requests
        assert [request.access.value for request in agent.requests] == ["read", "edit", "read"]
        assert plan.cwd == tmp_path / "cache" / "worktrees" / "acme" / "briar__ENG-1"
        assert (plan.max_usd, build.max_usd) == (MAX_USD, pytest.approx(MAX_USD - STEP_COST))
        assert str(setup.company.knowledge_file()) in plan.system_prompt
        assert "We need feature.txt" in plan.prompt
        assert "+done" in review.prompt

    def test_a_github_issue_is_closed_by_the_pr_and_not_moved(self, setups: Setups) -> None:
        setup = setups.make({"repos": ["acme/app"]})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().issue())

        github = setup.sources.github_side
        assert github.pulls[0][1].head == f"briar/issue-{ISSUE}"
        assert f"Closes #{ISSUE}" in github.pulls[0][1].body
        assert [(repo, number) for repo, number, _ in github.comments] == [("acme/app", ISSUE), ("acme/app", ISSUE)]
        assert setup.sources.tracker_side.moves == []


class TestLoops:
    def test_a_failed_verify_is_fixed_by_the_agent(self, setups: Setups) -> None:
        setup = setups.make({})
        agent = ScriptedAgent([Plans().plan(), Step(), Step(writes=Files().feature()), Plans().approved()])

        setup.work_on(agent, Items().jira())

        assert "`test -f feature.txt` failed (exit 1)" in agent.requests[THIRD_STEP].prompt
        assert setup.entry("ENG-1").status is WorkStatus.DONE

    def test_a_self_review_finding_is_fixed(self, setups: Setups) -> None:
        setup = setups.make({})
        fix = Step(writes={"feature.txt": "done, see ENG-1\n"})
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().rejected("feature.txt: say why"), fix, Plans().approved()])

        setup.work_on(agent, Items().jira())

        assert "- feature.txt: say why" in agent.requests[FOURTH_STEP].prompt
        assert setup.entry("ENG-1").status is WorkStatus.DONE

    def test_a_failed_ci_is_fixed_and_pushed(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.sources.github_side.ci[:] = [CiState.FAILED, CiState.PASSED]
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), Step(writes={"more.txt": "ci\n"})])

        setup.work_on(agent, Items().jira())

        assert "1 failed: test_feature" in agent.requests[FOURTH_STEP].prompt
        assert setup.entry("ENG-1").status is WorkStatus.DONE


class TestLimits:
    """S23: on a limit, push, a draft PR with the report, the report on the ticket."""

    def test_verify_that_never_passes_hits_max_loops(self, setups: Setups) -> None:
        setup = setups.make({"max_loops": 1})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes={"other.txt": "x"}), Step(writes={"other.txt": "y"})]), Items().jira())

        github, tracker = setup.sources.github_side, setup.sources.tracker_side
        assert github.pulls[0][1].draft is True
        expected = "briar stopped: max_loops (1) reached while fixing: the verify commands. Spent US$ 0.30 of 5.0, 2 of 1 fix loops."
        assert (github.pulls[0][1].body, tracker.comments[-1][1]) == (expected, expected)
        assert setup.entry("ENG-1").status is WorkStatus.BLOCKED

    def test_ci_that_never_ends_hits_the_ci_wait(self, setups: Setups, monkeypatch: pytest.MonkeyPatch) -> None:
        setup = setups.make({})
        monkeypatch.setattr(TaskFlow, "SECONDS_PER_MINUTE", 0)
        setup.sources.github_side.ci[:] = [CiState.PENDING]

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        github = setup.sources.github_side
        assert (github.drafts, [repo for repo, _ in github.pulls]) == ([PR], ["acme/app"])
        assert github.comments[-1][TEXT].startswith("briar stopped: CI did not pass within ci_wait_minutes (30)")
        assert setup.entry("ENG-1").status is WorkStatus.BLOCKED

    def test_a_plan_that_spends_the_budget_stops_with_no_pr(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Step(cost=MAX_USD)]), Items().jira())

        assert setup.sources.github_side.pulls == []
        assert setup.sources.tracker_side.comments[-1][1].startswith("briar stopped: the task spent its max_usd of 5.0")
        entry = setup.entry("ENG-1")
        assert (entry.status, entry.pr) == (WorkStatus.BLOCKED, 0)

    def test_a_refused_pull_request_blocks_the_ticket_for_good(self, setups: Setups) -> None:
        """A GitHub refusal is a report, not a crash: a crash left the ticket `planned`, so
        every cycle planned, built and paid for it again."""
        setup = setups.make({})
        setup.sources.github_side.refusals.append("HTTP 422: A pull request already exists")

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        entry = setup.entry("ENG-1")
        report = "briar stopped: HTTP 422: A pull request already exists. Spent US$ 0.30 of 5.0, 0 of 3 fix loops."
        assert (entry.status, entry.taken(), entry.pr) == (WorkStatus.BLOCKED, True, 0)
        assert entry.report == f"{report} The draft PR failed: HTTP 422: A pull request already exists."
        assert setup.sources.tracker_side.comments[-1][1] == entry.report
        assert "briar/ENG-1" in setup.origin.branches()
