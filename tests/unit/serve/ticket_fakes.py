"""Fakes for the ticket routine tests (spec 003 PR 2): a local git origin, an agent that
edits files by script, and GitHub and Jira that record what briar writes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Sequence, Tuple

from briar.auth.check import LoginRejected
from briar.extract._provider import CiFailure, CiState, PullRequestDraft
from briar.extract._tracker import Ticket
from briar.serve.agent import AgentAnswer, AgentRequest
from briar.serve.git import Shell


class LocalOrigin:
    """A bare git repo `acme/app` under `root/remotes`, with one commit on `main`."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def create(self) -> Path:
        shell = Shell()
        bare = self.root / "remotes" / "acme" / "app.git"
        seed = self.root / "seed"
        bare.parent.mkdir(parents=True)
        seed.mkdir()
        for line in (f"git init -q --bare -b main {bare}", "git init -q -b main", "echo app > README.md", "git add README.md"):
            assert shell.line(line, seed if "bare" not in line else self.root).ok(), line
        assert shell.line(f"git -c user.name=t -c user.email=t@example.test commit -q -m seed && git push -q {bare} main", seed).ok()
        return self.root / "remotes"

    def branches(self) -> str:
        return Shell().line("git branch --list", self.root / "remotes" / "acme" / "app.git").output


@dataclass(frozen=True)
class Step:
    """One scripted agent answer: the files it writes, and what it answers."""

    writes: Mapping[str, str] = field(default_factory=dict)
    structured: Mapping[str, Any] = field(default_factory=dict)
    cost: float = 0.1
    failed: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ScriptedAgent:
    """`AgentRunner` that plays `steps` in order and records each request."""

    steps: List[Step]
    requests: List[AgentRequest] = field(default_factory=list)

    async def ask(self, request: AgentRequest) -> AgentAnswer:
        self.requests.append(request)
        step = self.steps.pop(0)
        for name, text in step.writes.items():
            (request.cwd / name).write_text(text)
        return AgentAnswer("done", step.structured, step.cost, step.failed, step.reason)


class Plans:
    def plan(self) -> Step:
        return Step(structured={"summary": "Add the feature file", "steps": ["write feature.txt"], "tests": ["test -f feature.txt"]})

    def approved(self) -> Step:
        return Step(structured={"approved": True, "findings": []})

    def rejected(self, finding: str) -> Step:
        return Step(structured={"approved": False, "findings": [finding]})


@dataclass(frozen=True)
class FakeGithubSide:
    """The GitHub provider of the routine: it records the PRs and comments briar writes."""

    ci: List[CiState] = field(default_factory=lambda: [CiState.PASSED])
    pulls: List[Tuple[str, PullRequestDraft]] = field(default_factory=list)
    comments: List[Tuple[str, int, str]] = field(default_factory=list)
    drafts: List[int] = field(default_factory=list)
    # A PR refusal of GitHub, raised by every `open_pull` while set.
    refusals: List[str] = field(default_factory=list)
    login_source: ClassVar[str] = "github"
    NUMBER: ClassVar[int] = 41

    def who_am_i(self) -> str:
        return "octo"

    def default_branch(self, repo: str) -> str:
        return "main"

    def open_pull(self, repo: str, draft: PullRequestDraft) -> int:
        if self.refusals:
            raise LoginRejected(self.refusals[0])
        self.pulls.append((repo, draft))
        return self.NUMBER

    def comment(self, repo: str, number: int, text: str) -> None:
        self.comments.append((repo, number, text))

    def make_draft(self, repo: str, number: int) -> None:
        self.drafts.append(number)

    def ci_state(self, repo: str, number: int) -> CiState:
        return self.ci.pop(0) if len(self.ci) > 1 else self.ci[0]

    def list_ci_failures(self, repo: str, number: int) -> List[CiFailure]:
        return [CiFailure(workflow="test", job="test", step="pytest", log_tail="1 failed: test_feature", url="https://ci.example.test/1")]


@dataclass(frozen=True)
class FakeTrackerSide:
    """Jira or GitHub issues for the routine: the ticket text, and the comments and moves."""

    comments: List[Tuple[str, str]] = field(default_factory=list)
    moves: List[Tuple[str, str]] = field(default_factory=list)

    def get_ticket(self, project: str, key: str) -> Ticket:
        return Ticket(
            key=key, title="Add the feature", reporter="", assignee="", status="", kind="", priority="", created_at="", description="We need feature.txt"
        )

    def comment(self, key: str, text: str) -> None:
        self.comments.append((key, text))

    def move(self, key: str, status: str) -> None:
        self.moves.append((key, status))


@dataclass(frozen=True)
class RoutineSources:
    github_side: FakeGithubSide = field(default_factory=FakeGithubSide)
    tracker_side: FakeTrackerSide = field(default_factory=FakeTrackerSide)

    def github(self) -> FakeGithubSide:
        return self.github_side

    def issues(self) -> FakeTrackerSide:
        return self.tracker_side

    def jira(self) -> FakeTrackerSide:
        return self.tracker_side


class Settings:
    """The `serve:` block of the routine tests."""

    def block(self, verify: Sequence[str]) -> Dict[str, Any]:
        return {"jira": {"ENG": "acme/app"}, "verify": list(verify), "status": {"started": "In Progress", "review": "In Review"}}
