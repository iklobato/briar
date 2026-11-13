"""Fakes for the `briar serve` tests (spec 003 PR 1): the adapters the finder and the
checks read through, a company with a `serve:` block, and the knowledge store."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Sequence, Set, Tuple

from briar.auth.check import LoginRejected
from briar.extract._provider import PullState, ReviewComment
from briar.extract._tracker import Ticket
from briar.iac.runbook.models import ServeSettings
from briar.serve.git import GitIdentity, Remote
from briar.serve.state import WorkState
from briar.serve.work import ServeCompany
from briar.storage import make_store
from tests.unit.fake_logins import FakeLogins, FakeProbe


class Tickets:
    """Tickets as the trackers give them."""

    def jira(self, key: str, priority: str, created_at: str) -> Ticket:
        return Ticket(key=key, title=f"title of {key}", reporter="", assignee="bot", status="To Do", kind="Task", priority=priority, created_at=created_at)

    def issue(self, number: int, labels: Sequence[str], created_at: str) -> Ticket:
        return Ticket(
            key=f"#{number}",
            title=f"issue {number}",
            reporter="",
            assignee="octo",
            status="open",
            kind="",
            priority="",
            created_at=created_at,
            labels=list(labels),
        )


@dataclass(frozen=True)
class FakeJira:
    """`JiraTracker.assigned` and `can_write`: the tickets of each project, the permissions it lacks."""

    by_project: Mapping[str, List[Ticket]] = field(default_factory=dict)
    missing: Tuple[str, ...] = ()
    calls: List[str] = field(default_factory=list)
    login_source: ClassVar[str] = "jira"

    def who_am_i(self) -> str:
        return "bot@example.test"

    def read(self, resource: str) -> None:
        self.calls.append(f"read {resource}")

    def assigned(self, project: str, max_count: int) -> List[Ticket]:
        self.calls.append(f"assigned {project}")
        return list(self.by_project.get(project, []))[:max_count]

    def can_write(self, project: str, permissions: Sequence[str]) -> None:
        self.calls.append(f"can_write {project} {','.join(permissions)}")
        lacking = [name for name in permissions if name in self.missing]
        if lacking:
            raise LoginRejected(f"missing the Jira permission {', '.join(lacking)}")


@dataclass(frozen=True)
class FakeIssues:
    """`GithubIssuesTracker.assigned`: the open issues of each repo, by assignee."""

    by_repo: Mapping[str, List[Ticket]] = field(default_factory=dict)
    calls: List[str] = field(default_factory=list)

    def assigned(self, repo: str, login: str) -> List[Ticket]:
        self.calls.append(f"assigned {repo} {login}")
        return list(self.by_repo.get(repo, []))


@dataclass(frozen=True)
class FakeGithub:
    """`GithubProvider`: the token owner, the PRs with their comments and resolved threads,
    and the repos it cannot push to. A PR it does not know is open, with no conflict."""

    login: str = "octo"
    comments: Mapping[Tuple[str, int], List[ReviewComment]] = field(default_factory=dict)
    read_only: Tuple[str, ...] = ()
    pulls: Mapping[Tuple[str, int], PullState] = field(default_factory=dict)
    resolved: Mapping[Tuple[str, int], Set[str]] = field(default_factory=dict)
    # The repos `active_repos` gives (every pushable repo with recent activity), and its calls.
    active: Tuple[str, ...] = ()
    active_calls: List[int] = field(default_factory=list)
    login_source: ClassVar[str] = "github"

    def read(self, resource: str) -> None:
        return

    def active_repos(self, days: int) -> List[str]:
        self.active_calls.append(days)
        return list(self.active)

    def who_am_i(self) -> str:
        return self.login

    def pull_state(self, repo: str, number: int) -> PullState:
        return self.pulls.get((repo, number), PullState(True, False, False, "main"))

    def review_comments(self, repo: str, number: int) -> List[ReviewComment]:
        return list(self.comments.get((repo, number), []))

    def resolved_comment_ids(self, repo: str, number: int) -> Set[str]:
        return set(self.resolved.get((repo, number), set()))

    def resolve_token(self) -> str:
        return "PLACEHOLDER-not-a-secret"

    def can_write(self, repo: str) -> None:
        if repo in self.read_only:
            raise LoginRejected("the token cannot push: give it Contents and Pull requests read and write")


@dataclass(frozen=True)
class FakeStyle:
    """`HostStyle` of GitHub whose remotes live under `base` (a local `file://` origin in tests)."""

    base: str
    host: Any

    def pull_url(self, repo: str, number: int) -> str:
        return f"https://github.com/{repo}/pull/{number}"

    def remote(self, repo: str) -> Remote:
        return Remote(repo, self.base)

    def identity(self) -> GitIdentity:
        return GitIdentity(self.host.who_am_i())

    def noun(self) -> str:
        return "pull request"


@dataclass(frozen=True)
class FakeSources:
    github_adapter: FakeGithub = field(default_factory=FakeGithub)
    issues_adapter: FakeIssues = field(default_factory=FakeIssues)
    jira_adapter: FakeJira = field(default_factory=FakeJira)
    slack_adapter: FakeProbe = field(default_factory=lambda: FakeProbe("slack"))

    def slack(self) -> FakeProbe:
        return self.slack_adapter

    def code_host(self) -> FakeGithub:
        return self.github_adapter

    def style(self) -> "FakeStyle":
        return FakeStyle("https://github.com", self.github_adapter)

    def issues(self) -> FakeIssues:
        return self.issues_adapter

    def jira(self) -> FakeJira:
        return self.jira_adapter


class Companies:
    """A company `acme` with a `serve:` block and a knowledge store in `root`."""

    REQUIRED: Mapping[str, Any] = {"model": "claude-test", "max_usd": 5.0, "max_loops": 3, "ci_wait_minutes": 30, "max_review_rounds": 2}

    def __init__(self, root: Path) -> None:
        self._root = root

    def settings(self, extra: Dict[str, Any]) -> ServeSettings:
        return ServeSettings.model_validate({**self.REQUIRED, **extra})

    def company(self, extra: Dict[str, Any]) -> ServeCompany:
        state = WorkState(make_store("file", file_root=self._root), "acme", asyncio.Lock())
        return ServeCompany("acme", self.settings(extra), FakeLogins().logins(), state, self._root)
