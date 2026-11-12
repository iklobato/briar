"""What `briar serve` works on, in order, and the checks before it starts
(spec 003 PR 1: S3, S9, S14, S22, S29). The adapters are fakes (`fakes.py`)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from briar.auth.check import CheckState, LoginCheckFailed
from briar.errors import CliError
from briar.extract._provider import ReviewComment
from briar.serve.state import WorkEntry, WorkSource, WorkStatus
from briar.serve.work import ModelLogin, PriorityRank, ServeCheck, WorkFinder, WorkKind, WriteCheck
from tests.unit.serve.fakes import Companies, FakeGithub, FakeIssues, FakeJira, FakeSources, Tickets

PR_NUMBER = 12
ISSUE_HIGH = 7
ISSUE_PLAIN = 8


@pytest.fixture
def companies(tmp_path: Path) -> Companies:
    return Companies(tmp_path / "knowledge")


class TestOrder:
    def test_reviews_first_then_priority_then_the_oldest(self, companies: Companies) -> None:
        tickets = Tickets()
        jira = FakeJira({"ENG": [tickets.jira("ENG-2", "Low", "2026-01-02"), tickets.jira("ENG-1", "High", "2026-01-03")]})
        issues = FakeIssues({"acme/web": [tickets.issue(ISSUE_PLAIN, [], "2026-01-01"), tickets.issue(ISSUE_HIGH, ["p0"], "2026-01-05")]})
        github = FakeGithub(comments={("acme/app", PR_NUMBER): [ReviewComment(id="c1", author="reviewer", body="rename this")]})
        company = companies.company({"repos": ["acme/web"], "jira": {"ENG": "acme/app"}, "priority_labels": ["p0", "p1"]})
        asyncio.run(company.state.record(WorkEntry(key="ENG-9", source=WorkSource.JIRA, repo="acme/app", title="t", status=WorkStatus.FOUND, pr=PR_NUMBER)))

        items = WorkFinder(company, FakeSources(github, issues, jira)).find()

        assert [(item.kind, item.key) for item in items] == [
            (WorkKind.REVIEW, "ENG-9"),
            (WorkKind.TICKET, "acme/web#7"),
            (WorkKind.TICKET, "ENG-1"),
            (WorkKind.TICKET, "ENG-2"),
            (WorkKind.TICKET, "acme/web#8"),
        ]
        assert (jira.calls, issues.calls) == (["assigned ENG"], ["assigned acme/web octo"])

    @pytest.mark.parametrize("status", [WorkStatus.PR_OPEN, WorkStatus.DONE, WorkStatus.BLOCKED])
    def test_a_ticket_briar_took_is_not_found_again(self, companies: Companies, status: WorkStatus) -> None:
        jira = FakeJira({"ENG": [Tickets().jira("ENG-1", "High", "2026-01-03")]})
        company = companies.company({"jira": {"ENG": "acme/app"}})
        asyncio.run(company.state.record(WorkEntry(key="ENG-1", source=WorkSource.JIRA, repo="acme/app", title="t", status=status)))

        assert WorkFinder(company, FakeSources(jira_adapter=jira)).find() == []

    @pytest.mark.parametrize("status", [WorkStatus.FOUND, WorkStatus.PLANNED])
    def test_a_ticket_that_stopped_halfway_is_taken_again(self, companies: Companies, status: WorkStatus) -> None:
        """A `serve` that stopped in the middle picks the ticket up again (S17)."""
        jira = FakeJira({"ENG": [Tickets().jira("ENG-1", "High", "2026-01-03")]})
        company = companies.company({"jira": {"ENG": "acme/app"}})
        asyncio.run(company.state.record(WorkEntry(key="ENG-1", source=WorkSource.JIRA, repo="acme/app", title="t", status=status)))

        assert [item.key for item in WorkFinder(company, FakeSources(jira_adapter=jira)).find()] == ["ENG-1"]

    def test_a_review_counts_only_new_comments_of_other_people(self, companies: Companies) -> None:
        comments = [
            ReviewComment(id="c1", author="reviewer", body="done before"),
            ReviewComment(id="c2", author="octo", body="briar replied"),
            ReviewComment(id="c3", author="bot[bot]", body="lint: long line"),
        ]
        company = companies.company({"repos": ["acme/app"]})
        entry = WorkEntry(
            key="acme/app#3", source=WorkSource.GITHUB, repo="acme/app", title="t", status=WorkStatus.FOUND, pr=PR_NUMBER, handled_comments=["c1"]
        )
        asyncio.run(company.state.record(entry))

        items = WorkFinder(company, FakeSources(FakeGithub(comments={("acme/app", PR_NUMBER): comments}))).find()

        assert [(item.kind, item.pr, item.comments) for item in items] == [(WorkKind.REVIEW, PR_NUMBER, ("c3",))]


class TestPriorityRank:
    def test_jira_names_and_github_labels(self) -> None:
        rank = PriorityRank()

        assert [rank.jira("Highest"), rank.jira("Lowest"), rank.jira("Urgent!")] == [0, len(PriorityRank.JIRA) - 1, PriorityRank.UNKNOWN]
        assert [rank.github(["bug", "p1"], ["p0", "p1"]), rank.github(["bug"], ["p0"])] == [1, PriorityRank.UNKNOWN]


class TestWriteCheck:
    """S29: checked with no write; the line says what cannot be written."""

    def test_a_repo_the_token_cannot_push_to_fails(self, companies: Companies) -> None:
        company = companies.company({"repos": ["acme/app", "acme/web"]})

        lines = WriteCheck(company, FakeSources(FakeGithub(read_only=("acme/web",)))).results()

        assert [(line.state, line.line()) for line in lines] == [
            (CheckState.OK, "github (acme): can write acme/app"),
            (
                CheckState.FAILED,
                "github (acme): cannot write acme/web (the token cannot push: give it Contents and Pull requests read and write); give this login write access",
            ),
        ]

    def test_jira_needs_the_transition_permission_only_with_status_names(self, companies: Companies) -> None:
        jira = FakeJira(missing=("TRANSITION_ISSUES",))
        company = companies.company({"jira": {"ENG": "acme/app"}, "status": {"started": "In Progress"}})

        lines = WriteCheck(company, FakeSources(jira_adapter=jira)).results()

        assert jira.calls == ["can_write ENG ADD_COMMENTS,TRANSITION_ISSUES"]
        assert lines[-1].line() == "jira (acme): cannot write ENG (missing the Jira permission TRANSITION_ISSUES); give this login write access"

    def test_jira_without_status_names_needs_comments_only(self, companies: Companies) -> None:
        jira = FakeJira(missing=("TRANSITION_ISSUES",))

        lines = WriteCheck(companies.company({"jira": {"ENG": "acme/app"}}), FakeSources(jira_adapter=jira)).results()

        assert (jira.calls, lines[-1].state) == (["can_write ENG ADD_COMMENTS"], CheckState.OK)


class TestServeCheck:
    def test_no_model_login_stops_first(self, companies: Companies) -> None:
        with pytest.raises(CliError) as caught:
            ServeCheck([companies.company({"repos": ["acme/app"]})], {}).require()

        assert str(caught.value) == "no model login: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (in the environment or secrets.env)"

    def test_a_missing_login_stops_with_every_line(self, companies: Companies, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("briar.serve.work.WorkSources", lambda logins: FakeSources())

        with pytest.raises(LoginCheckFailed, match=r"  - github \(acme\): no login saved \(BRIAR_ACME_GITHUB_TOKEN\)"):
            ServeCheck([companies.company({"repos": ["acme/app"]})], {"ANTHROPIC_API_KEY": "PLACEHOLDER-not-a-secret"}).require()

    def test_the_model_login_takes_either_name(self) -> None:
        assert [ModelLogin({"CLAUDE_CODE_OAUTH_TOKEN": "x"}).problem(), ModelLogin({"ANTHROPIC_API_KEY": " "}).problem() != ""] == ["", True]
