"""The ticket routine of `briar serve` (spec 003 PR 2: S18 to S27) end to end, with real
git against a local origin, a scripted agent, and GitHub and Jira fakes that record
what briar writes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

import pytest

from briar.extract._provider import CiState
from briar.serve.git import RepoCheckout, Shell
from briar.serve.state import WorkSource, WorkStatus
from briar.serve.task import TaskPrep
from briar.serve.ticket import TicketRoutine, TicketRun
from briar.serve.work import ServeCompany, WorkItem, WorkKind
from tests.unit.serve.fakes import Companies
from tests.unit.serve.ticket_fakes import FakeGithubSide, LocalOrigin, Plans, RoutineSources, ScriptedAgent, Settings, Step

PR = FakeGithubSide.NUMBER
ISSUE = 7
MAX_USD = 5.0
STEP_COST = 0.1
THIRD_STEP, FOURTH_STEP = 2, 3
# The text in a recorded (repo, number, text) comment.
TEXT = 2


class Files:
    def feature(self) -> Mapping[str, str]:
        return {"feature.txt": "done\n"}


@dataclass(frozen=True)
class Setup:
    """A company `acme` that watches Jira project ENG for repo acme/app, a local origin, and the fakes."""

    company: ServeCompany
    sources: RoutineSources
    origin: LocalOrigin
    cache: Path

    def work_on(self, agent: ScriptedAgent, item: WorkItem) -> None:
        prep = TaskPrep(self.sources, Shell(), self.cache)
        asyncio.run(TicketRoutine(self.company, lambda context: agent, prep).work_on(item))

    def entry(self, key: str) -> Any:
        return self.company.state.book().entry(key)


class Setups:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._tmp_path = tmp_path
        self._monkeypatch = monkeypatch

    def make(self, extra: Dict[str, Any]) -> Setup:
        origin = LocalOrigin(self._tmp_path)
        self._monkeypatch.setattr(RepoCheckout, "HOST", f"file://{origin.create()}")
        self._monkeypatch.setattr(TicketRun, "CI_POLL_SECONDS", 0)
        company = Companies(self._tmp_path / "knowledge").company({**Settings().block(["test -f feature.txt"]), **extra})
        return Setup(company, RoutineSources(), origin, self._tmp_path / "cache")


class Items:
    def jira(self) -> WorkItem:
        return WorkItem(WorkKind.TICKET, "ENG-1", WorkSource.JIRA, "acme/app", "Add the feature")

    def issue(self) -> WorkItem:
        return WorkItem(WorkKind.TICKET, f"acme/app#{ISSUE}", WorkSource.GITHUB, "acme/app", "Add the feature")


@pytest.fixture
def setups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setups:
    return Setups(tmp_path, monkeypatch)


class TestHappyPath:
    def test_every_step_of_a_ticket_is_logged(self, setups: Setups, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.ticket")
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
        monkeypatch.setattr(TicketRun, "SECONDS_PER_MINUTE", 0)
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


class TestReviewItems:
    def test_a_review_item_is_left_for_part_3(self, setups: Setups, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.ticket")
        setup = setups.make({})
        agent = ScriptedAgent([])

        setup.work_on(agent, WorkItem(WorkKind.REVIEW, "ENG-1", WorkSource.JIRA, "acme/app", "t", pr=PR, comments=("c1",)))

        assert (agent.requests, setup.sources.github_side.pulls) == ([], [])
        assert f"serve: 1 new comments on acme/app#{PR} (the review routine is not built yet)" in caplog.text
