"""N6 as refined by M1 to M4 (`docs/decisions/2026-11-12-briar-2-open-mr-rule.md`): a ticket
with an open PR/MR of anyone but briar is skipped, with a log line. No network."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from briar.extract._provider import PullRequest
from briar.serve.open_work import OpenWork
from briar.serve.state import WorkSource
from briar.serve.work import WorkItem, WorkKind
from tests.unit.serve.fakes import Companies, FakeStyle

REPO = "acme/app"
OTHER = "acme/api"
MR, OLD = 34, 12


@dataclass(frozen=True)
class PullText:
    branch: str
    title: str
    body: str = ""

    def open(self, number: int) -> PullRequest:
        return PullRequest(number, self.title, "ana", False, self.branch, "main", 0, "2026-11-01", body=self.body)


@dataclass(frozen=True)
class FakeHost:
    """The open PRs of each repo; `asked` records the repos read."""

    pulls: Dict[str, List[PullRequest]]
    asked: List[str] = field(default_factory=list)

    def who_am_i(self) -> str:
        return "octo"

    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]:
        self.asked.append(f"{repo}:{state}")
        return list(self.pulls.get(repo, []))


@dataclass(frozen=True)
class FakeTracker:
    links: Dict[str, List[str]] = field(default_factory=dict)

    def remote_links(self, key: str) -> List[str]:
        return list(self.links.get(key, []))


@dataclass(frozen=True)
class Sources:
    host: FakeHost
    tracker: FakeTracker = field(default_factory=FakeTracker)

    def code_host(self) -> FakeHost:
        return self.host

    def style(self) -> FakeStyle:
        return FakeStyle("https://github.com", self.host)

    def jira_links(self) -> FakeTracker:
        return self.tracker


class Open:
    def __init__(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path

    def free(self, sources: Sources, keys: Tuple[str, ...]) -> List[str]:
        settings = Companies(self._tmp_path).company({"jira": {"ENG": {"repos": [REPO, OTHER]}}}).settings
        items = [WorkItem(WorkKind.TICKET, key, WorkSource.JIRA, "", f"title of {key}", repos=(REPO, OTHER)) for key in keys]
        return [item.key for item in OpenWork(sources, settings).free(items)]


class TestOpenWork:
    @pytest.mark.parametrize(
        "text",
        [PullText("ENG-1/device-name", "fix the device"), PullText("fix-device", "fix(ENG-1): device"), PullText("fix-device", "fix the device", "Closes ENG-1")],
        ids=["branch", "title", "description"],
    )
    def test_the_key_in_an_open_pr_skips_the_ticket(self, tmp_path: Path, text: PullText) -> None:
        sources = Sources(FakeHost({REPO: [text.open(MR)]}))

        assert Open(tmp_path).free(sources, ("ENG-1", "ENG-2")) == ["ENG-2"]

    def test_a_longer_key_does_not_match(self, tmp_path: Path) -> None:
        sources = Sources(FakeHost({REPO: [PullText("ENG-10/other", "ENG-10: other").open(MR)]}))

        assert Open(tmp_path).free(sources, ("ENG-1",)) == ["ENG-1"]

    def test_the_pr_on_the_branch_of_briar_does_not_count(self, tmp_path: Path) -> None:
        """M2: the PR briar opened for the ticket goes through the review flow."""
        sources = Sources(FakeHost({REPO: [PullText("briar/ENG-1", "ENG-1: title").open(MR)]}))

        assert Open(tmp_path).free(sources, ("ENG-1",)) == ["ENG-1"]

    def test_a_remote_link_to_an_open_pr_skips_the_ticket(self, tmp_path: Path) -> None:
        sources = Sources(FakeHost({OTHER: [PullText("device-name", "read the device").open(MR)]}), FakeTracker({"ENG-1": [f"https://github.com/{OTHER}/pull/{MR}"]}))

        assert Open(tmp_path).free(sources, ("ENG-1",)) == []

    def test_a_remote_link_to_a_closed_pr_does_not_count(self, tmp_path: Path) -> None:
        sources = Sources(FakeHost({REPO: []}), FakeTracker({"ENG-1": [f"https://github.com/{REPO}/pull/{OLD}"]}))

        assert Open(tmp_path).free(sources, ("ENG-1",)) == ["ENG-1"]

    def test_the_skip_is_logged_with_the_link(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """M3: nothing is written on the ticket; the log says which PR."""
        sources = Sources(FakeHost({REPO: [PullText("ENG-1/fix", "fix").open(MR)]}))

        with caplog.at_level(logging.INFO, logger="briar.serve.open_work"):
            Open(tmp_path).free(sources, ("ENG-1",))

        assert caplog.messages == [f"serve: ENG-1 skipped: https://github.com/{REPO}/pull/{MR} is open for it"]

    def test_each_repo_is_read_once_per_poll(self, tmp_path: Path) -> None:
        host = FakeHost({})

        Open(tmp_path).free(Sources(host), ("ENG-1", "ENG-2"))

        assert sorted(host.asked) == [f"{OTHER}:open", f"{REPO}:open"]
