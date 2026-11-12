"""ExtractActiveWork (open PRs) + ExtractActiveTickets (open tickets).

These are provider-agnostic extractors: they call a RepositoryProvider /
TrackerProvider and normalise the result into one ExtractedSection per
repo/project. We inject a fake provider (patching `_provider` /
`_tracker`) and assert the exact normalised rows, counts, truncation,
author filtering, and empty/missing handling.
"""

from __future__ import annotations

import argparse
from typing import Any, Dict, List
from unittest import mock

import pytest

from briar.auth.check import LoginRejected
from briar.extract._provider import PullRequest
from briar.extract._tracker import Ticket
from briar.extract._trackers.jira import JiraOpenCount
from briar.extract.active_tickets import ExtractActiveTickets
from briar.extract.active_work import ExtractActiveWork

pytestmark = pytest.mark.unit


def _pr(number: int, *, author: str = "alice", title: str = "fix", draft: bool = False, comments: int = 0) -> PullRequest:
    return PullRequest(
        number=number,
        title=title,
        author=author,
        is_draft=draft,
        head_ref="feature",
        base_ref="main",
        review_comment_count=comments,
        created_at="2026-05-01T00:00:00Z",
    )


def _work_args(**over: Any) -> argparse.Namespace:
    base = dict(
        active_repo=["o/r"],
        provider="github",
        company="",
        active_authors_allow=[],
        active_authors_block=[],
        active_assignees_allow=[],
        active_assignees_block=[],
    )
    base.update(over)
    return argparse.Namespace(**base)


class _FakeRepoProvider:
    def __init__(self, prs: List[PullRequest]) -> None:
        self._prs = prs
        self.calls: list = []

    def is_available(self) -> bool:
        return True

    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]:
        self.calls.append((repo, state, max_count))
        return self._prs


# ─── ExtractActiveWork ───────────────────────────────────────────────


def test_active_work_normalises_pr_rows() -> None:
    provider = _FakeRepoProvider([_pr(1, comments=3), _pr(2, draft=True, author="bob")])
    ext = ExtractActiveWork()
    with mock.patch.object(ext, "_provider", return_value=provider):
        section = ext.extract(_work_args())

    assert section.title == "Active work — 1 repo(s)"
    repo_section = section.subsections[0]
    assert repo_section.title == "o/r — 2 open PR(s)"
    rows = repo_section.data["open_prs"]
    assert [r["number"] for r in rows] == [1, 2]
    assert rows[0]["review_comments"] == 3
    assert rows[1]["draft"] is True
    assert rows[1]["user"] == "bob"
    # The provider is asked for OPEN PRs, over-fetching 4x the cap.
    assert provider.calls == [("o/r", "open", 100)]
    assert "[draft]" in repo_section.body


def test_active_work_truncates_to_25_per_repo() -> None:
    provider = _FakeRepoProvider([_pr(n) for n in range(40)])
    ext = ExtractActiveWork()
    with mock.patch.object(ext, "_provider", return_value=provider):
        section = ext.extract(_work_args())

    rows = section.subsections[0].data["open_prs"]
    assert len(rows) == 25
    assert section.subsections[0].title == "o/r — 25 open PR(s)"
    # Keeps the first 25 in provider order.
    assert [r["number"] for r in rows] == list(range(25))


def test_active_work_caps_title_at_80_chars() -> None:
    long_title = "x" * 200
    provider = _FakeRepoProvider([_pr(1, title=long_title)])
    ext = ExtractActiveWork()
    with mock.patch.object(ext, "_provider", return_value=provider):
        section = ext.extract(_work_args())
    assert section.subsections[0].data["open_prs"][0]["title"] == "x" * 80


def test_active_work_author_allow_filter() -> None:
    provider = _FakeRepoProvider([_pr(1, author="alice"), _pr(2, author="bob")])
    ext = ExtractActiveWork()
    with mock.patch.object(ext, "_provider", return_value=provider):
        section = ext.extract(_work_args(active_authors_allow=["alice"]))
    rows = section.subsections[0].data["open_prs"]
    assert [r["user"] for r in rows] == ["alice"]


def test_active_work_empty_repo_renders_placeholder() -> None:
    provider = _FakeRepoProvider([])
    ext = ExtractActiveWork()
    with mock.patch.object(ext, "_provider", return_value=provider):
        section = ext.extract(_work_args())
    repo_section = section.subsections[0]
    assert repo_section.title == "o/r — 0 open PR(s)"
    assert repo_section.body == "_no open PRs_"
    assert repo_section.data["open_prs"] == []


def test_active_work_multiple_repos() -> None:
    provider = _FakeRepoProvider([_pr(1)])
    ext = ExtractActiveWork()
    with mock.patch.object(ext, "_provider", return_value=provider):
        section = ext.extract(_work_args(active_repo=["o/r1", "o/r2"]))
    assert section.title == "Active work — 2 repo(s)"
    assert len(section.subsections) == 2


def test_active_work_unavailable_without_repos() -> None:
    """is_available gates on the --active-repo presence: empty → skip."""
    ext = ExtractActiveWork()
    assert ext.is_available(_work_args(active_repo=[])) is False


def test_active_work_unavailable_when_provider_unavailable() -> None:
    provider = _FakeRepoProvider([])
    provider.is_available = lambda: False  # type: ignore[assignment]
    ext = ExtractActiveWork()
    with mock.patch.object(ext, "_provider", return_value=provider):
        assert ext.is_available(_work_args()) is False


# ─── ExtractActiveTickets ────────────────────────────────────────────


def _ticket(key: str, *, title: str = "bug", reporter: str = "carol", assignee: str = "dan", status: str = "open", labels: List[str] | None = None) -> Ticket:
    return Ticket(
        key=key,
        title=title,
        reporter=reporter,
        assignee=assignee,
        status=status,
        kind="bug",
        priority="high",
        created_at="2026-05-01T00:00:00Z",
        labels=labels or [],
    )


def _ticket_args(**over: Any) -> argparse.Namespace:
    base = dict(ticket_project=["ENG"], tracker="jira", company="")
    base.update(over)
    return argparse.Namespace(**base)


class _FakeTracker:
    def __init__(self, tickets: List[Ticket]) -> None:
        self._tickets = tickets
        self.calls: list = []

    def is_available(self) -> bool:
        return True

    def list_tickets(self, project: str, *, state: str, max_count: int) -> List[Ticket]:
        self.calls.append((project, state, max_count))
        return self._tickets


def test_active_tickets_normalises_rows() -> None:
    tracker = _FakeTracker([_ticket("ENG-1", labels=["a", "b", "c", "d", "e", "f"]), _ticket("ENG-2", status="in_progress")])
    ext = ExtractActiveTickets()
    with mock.patch.object(ext, "_tracker", return_value=tracker):
        section = ext.extract(_ticket_args())

    assert section.title == "Active tickets — 1 project(s)"
    proj = section.subsections[0]
    assert proj.title == "ENG — 2 open ticket(s)"
    rows = proj.data["open_tickets"]
    assert [r["key"] for r in rows] == ["ENG-1", "ENG-2"]
    # labels are capped at 5.
    assert rows[0]["labels"] == ["a", "b", "c", "d", "e"]
    assert rows[1]["status"] == "in_progress"
    # OPEN tickets requested, capped at 25.
    assert tracker.calls == [("ENG", "open", 25)]


def test_active_tickets_caps_title_at_80() -> None:
    tracker = _FakeTracker([_ticket("ENG-1", title="y" * 120)])
    ext = ExtractActiveTickets()
    with mock.patch.object(ext, "_tracker", return_value=tracker):
        section = ext.extract(_ticket_args())
    assert section.subsections[0].data["open_tickets"][0]["title"] == "y" * 80


def test_active_tickets_empty_project_placeholder() -> None:
    tracker = _FakeTracker([])
    ext = ExtractActiveTickets()
    with mock.patch.object(ext, "_tracker", return_value=tracker):
        section = ext.extract(_ticket_args())
    proj = section.subsections[0]
    assert proj.body == "_no open tickets_"
    assert proj.data["open_tickets"] == []


def test_active_tickets_unavailable_without_project() -> None:
    ext = ExtractActiveTickets()
    assert ext.is_available(_ticket_args(ticket_project=[])) is False


class CountingTracker(_FakeTracker):
    """A tracker that gives the most tickets `active-tickets` asks for, and counts the rest."""

    def __init__(self, tickets: List[Ticket], total: int) -> None:
        super().__init__(tickets)
        self.total = total

    def open_count(self, project: str) -> int:
        return self.total


class TestCutTicketList:
    """N17 of spec 008: a real project showed "25 open ticket(s)" with 881 open."""

    LIMIT = 25
    OPEN = 881

    def title(self, total: int) -> str:
        tracker = CountingTracker([_ticket(f"ENG-{number}") for number in range(self.LIMIT)], total)
        ext = ExtractActiveTickets()
        with mock.patch.object(ext, "_tracker", return_value=tracker):
            return ext.extract(_ticket_args()).subsections[0].title

    def test_a_cut_list_says_first_n_of_m(self) -> None:
        assert self.title(self.OPEN) == "ENG — first 25 of 881 open ticket(s)"

    def test_a_tracker_that_cannot_count_says_first_n(self) -> None:
        assert self.title(0) == "ENG — first 25 open ticket(s)"

    def test_a_list_of_exactly_the_limit_is_not_called_cut(self) -> None:
        assert self.title(self.LIMIT) == "ENG — 25 open ticket(s)"


class CountClient:
    """The Jira client: answers the approximate count and keeps the JQL it was sent."""

    def __init__(self, sent: List[str]) -> None:
        self._sent = sent

    def post(self, path: str, body: Dict[str, str]) -> Dict[str, int]:
        self._sent.append(f"{path} {body['jql']}")
        return {"count": TestCutTicketList.OPEN}


class TestJiraOpenCount:
    def test_the_count_asks_jira_for_the_open_tickets_of_the_project(self) -> None:
        sent: List[str] = []

        assert JiraOpenCount(CountClient(sent)).of("ENG") == TestCutTicketList.OPEN
        assert sent == ['rest/api/3/search/approximate-count project = "ENG" AND statusCategory != "Done"']

    def test_a_key_that_is_not_a_jira_key_is_refused_before_any_call(self) -> None:
        sent: List[str] = []

        with pytest.raises(LoginRejected, match="'ENG\" OR 1=1' is not a Jira project key"):
            JiraOpenCount(CountClient(sent)).of('ENG" OR 1=1')
        assert sent == []
