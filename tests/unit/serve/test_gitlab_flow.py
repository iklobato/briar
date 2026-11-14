"""`briar serve` end to end on `code_host: gitlab` (test plan F-GL, cases G2 and G3 on the
fake): the real `GitlabHost` and `GitlabStyle` over a GitLab fake with state, real git
against a local origin, and a scripted agent."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from briar.extract._providers.gitlab_host import GitlabHost
from briar.serve.flow import TaskFlow
from briar.serve.git import Shell
from briar.serve.host import GitlabStyle
from briar.serve.routine import ServeRoutine
from briar.serve.state import WorkStatus
from briar.serve.task import TaskPrep
from briar.serve.work import ServeCompany
from tests.unit.fake_logins import FakeLogins
from tests.unit.serve.fakes import Companies
from tests.unit.serve.gitlab_server import GitlabServer, PipelinePlan
from tests.unit.serve.ticket_fakes import FakeTrackerSide, Files, Items, LocalOrigin, Plans, ScriptedAgent, Settings, Step

FAKE_TOKEN = "PLACEHOLDER-not-a-secret"
FIRST_MR = 1
# The agent call that fixes a failed CI: plan, build, self-review, then the fix.
CI_FIX_CALL = 3
TWO_PUSHED_HEADS = 2
CLEAN = "! grep -qs bad state.txt"


@dataclass(frozen=True)
class GitlabSources:
    """The work sources of a company on GitLab, over the fake."""

    base: str
    host: GitlabHost
    tracker: FakeTrackerSide = field(default_factory=FakeTrackerSide)

    def code_host(self) -> GitlabHost:
        return self.host

    def style(self) -> GitlabStyle:
        return GitlabStyle(self.base, self.host)

    def issues(self) -> FakeTrackerSide:
        return self.tracker

    def jira(self) -> FakeTrackerSide:
        return self.tracker


@dataclass(frozen=True)
class GitlabSetup:
    company: ServeCompany
    sources: GitlabSources
    server: GitlabServer
    cache: Path

    def work_on(self, agent: ScriptedAgent) -> None:
        prep = TaskPrep(self.sources, Shell(), self.cache)  # type: ignore[arg-type]  # a structural stand-in for WorkSources, as RoutineSources is
        asyncio.run(ServeRoutine(self.company, lambda context: agent, prep).work_on(Items().jira()))


class GitlabSetups:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._tmp_path = tmp_path
        self._monkeypatch = monkeypatch

    def make(self, pipelines: PipelinePlan) -> GitlabSetup:
        remotes = LocalOrigin(self._tmp_path).create()
        self._monkeypatch.setattr(TaskFlow, "CI_POLL_SECONDS", 0)
        server = GitlabServer(remotes / "acme" / "app.git", pipelines)
        host = GitlabHost(FakeLogins({}, {"BRIAR_ACME_GITLAB_TOKEN": FAKE_TOKEN}).logins().gitlab(), server)
        company = Companies(self._tmp_path / "knowledge").company({**Settings().block([CLEAN]), "code_host": "gitlab"})
        return GitlabSetup(company, GitlabSources(f"file://{remotes}", host), server, self._tmp_path / "cache")


@pytest.fixture
def gitlab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitlabSetups:
    return GitlabSetups(tmp_path, monkeypatch)


class TestTicketOnGitlab:
    def test_a_ticket_ends_as_a_ready_merge_request_with_the_jira_moves(self, gitlab: GitlabSetups) -> None:
        setup = gitlab.make(PipelinePlan())

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]))

        mr = setup.server.merge_requests[FIRST_MR]
        assert (mr["source_branch"], mr["target_branch"], mr["title"]) == ("briar/ENG-1", "main", "ENG-1: Add the feature")
        tracker = setup.sources.tracker
        assert tracker.moves == [("ENG-1", "In Progress"), ("ENG-1", "In Review")]
        assert tracker.comments[-1][1].endswith(f"/acme/app/-/merge_requests/{FIRST_MR}")
        assert setup.company.state.book().entry("ENG-1").status is WorkStatus.DONE

    def test_a_failed_merged_results_pipeline_reaches_the_agent_and_the_fix_goes_green(self, gitlab: GitlabSetups) -> None:
        """A real run (2026-11-13): CI on `refs/merge-requests/<iid>/merge` was read as pending
        until `ci_wait_minutes`, so the failed job never reached the agent."""
        setup = gitlab.make(PipelinePlan(statuses=["failed", "success"], merged_results=True))
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), Step(writes={"fix.txt": "fixed\n"})])

        setup.work_on(agent)

        assert "CI fails on the merge request #1" in agent.requests[CI_FIX_CALL].prompt
        assert "job log: the test failed" in agent.requests[CI_FIX_CALL].prompt
        assert (len(setup.server.heads), setup.server.merge_requests[FIRST_MR]["title"]) == (TWO_PUSHED_HEADS, "ENG-1: Add the feature")
        assert setup.company.state.book().entry("ENG-1").status is WorkStatus.DONE
