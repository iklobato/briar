"""What `briar serve` works on, and the checks before it starts (spec 003 PR 1).

The finder asks GitHub and Jira each cycle (S10): new comments on the PRs briar opened
come first (S14, S22), then the open tickets assigned to the account of the token (S9),
highest priority first, then the oldest. Before the first cycle `serve` checks the model
login (S3), the read logins (#99) and the write access (S29) of every company."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from functools import partial
from pathlib import Path
from typing import Any, Callable, ClassVar, List, Mapping, Sequence, Tuple

from briar.auth.check import CheckResult, CheckState, LoginCheck, LoginCheckFailed, LoginTarget, SourceUnreachable
from briar.auth.logins import CompanyLogins, LoginScope
from briar.errors import CliError
from briar.extract._tracker import Ticket
from briar.iac.runbook.models import ServeSettings
from briar.serve.state import WorkBook, WorkEntry, WorkSource, WorkState, WorkStatus
from briar.storage import make_store


class WorkKind(Enum):
    REVIEW = "review"
    TICKET = "ticket"


@dataclass(frozen=True)
class WorkItem:
    """One task: a ticket to build, or a briar PR with new review comments."""

    kind: WorkKind
    key: str
    source: WorkSource
    repo: str
    title: str
    rank: int = 0
    created_at: str = ""
    pr: int = 0
    comments: Tuple[str, ...] = ()

    def order(self) -> Tuple[int, int, str]:
        """Reviews first (S14), then the rank of the priority, then the oldest."""
        return (0 if self.kind is WorkKind.REVIEW else 1, self.rank, self.created_at)

    def entry(self) -> WorkEntry:
        return WorkEntry(key=self.key, source=self.source, repo=self.repo, title=self.title, status=WorkStatus.FOUND)

    def number(self) -> str:
        """The number of a GitHub issue (`acme/app#7` gives `7`); "" for Jira."""
        return self.key.rsplit("#", 1)[-1] if self.source is WorkSource.GITHUB else ""

    def ref(self) -> str:
        """The ticket in a branch or a title: the Jira key, or `issue-<number>`."""
        return f"issue-{self.number()}" if self.source is WorkSource.GITHUB else self.key


class PriorityRank:
    """A rank where 0 is the most urgent. Jira: its standard priority names; GitHub: the
    place of the first label of `priority_labels` the issue has (S14)."""

    JIRA: ClassVar[Tuple[str, ...]] = ("Highest", "High", "Medium", "Low", "Lowest")
    # A ticket with no known priority comes after every known one.
    UNKNOWN = 99

    def jira(self, name: str) -> int:
        return self.JIRA.index(name) if name in self.JIRA else self.UNKNOWN

    def github(self, labels: Sequence[str], priority_labels: Sequence[str]) -> int:
        found = [priority_labels.index(label) for label in labels if label in priority_labels]
        return min(found) if found else self.UNKNOWN


@dataclass(frozen=True)
class ServeCompany:
    """One company of a runbook with a `serve:` block."""

    name: str
    settings: ServeSettings
    logins: CompanyLogins
    state: WorkState
    # The knowledge store folder: the blob `knowledge:<name>` and the state `work:<name>`.
    root: Path = Path("knowledge")

    def knowledge_file(self) -> Path:
        return self.root / "knowledge" / f"{self.name}.md"

    def state_file(self) -> Path:
        return self.root / "work" / f"{self.name}.md"


class WorkSources:
    """The adapters `serve` reads through, built with the company logins."""

    def __init__(self, logins: CompanyLogins) -> None:
        self._logins = logins

    def github(self) -> Any:
        from briar.extract._providers import make_provider

        return make_provider("github", self._logins)

    def issues(self) -> Any:
        from briar.extract._trackers import make_tracker

        return make_tracker("github-issues", self._logins)

    def jira(self) -> Any:
        from briar.extract._trackers import make_tracker

        return make_tracker("jira", self._logins)


class WorkFinder:
    """The work of one company in one cycle, in the order `serve` takes it."""

    # The most tickets one Jira project gives per cycle.
    MAX_TICKETS = 50

    def __init__(self, company: ServeCompany, sources: WorkSources) -> None:
        self._company = company
        self._sources = sources

    def find(self) -> List[WorkItem]:
        book = self._company.state.book()
        login = self._sources.github().who_am_i() if self._company.settings.all_repos() else ""
        tickets = [item for item in [*self._jira(), *self._issues(login)] if not book.taken(item.key)]
        return sorted([*self._reviews(book, login), *tickets], key=WorkItem.order)

    def _jira(self) -> List[WorkItem]:
        if not self._company.settings.jira:
            return []
        tracker = self._sources.jira()
        return [
            self._jira_item(ticket, repo) for project, repo in self._company.settings.jira.items() for ticket in tracker.assigned(project, self.MAX_TICKETS)
        ]

    def _jira_item(self, ticket: Ticket, repo: str) -> WorkItem:
        rank = PriorityRank().jira(ticket.priority)
        return WorkItem(WorkKind.TICKET, ticket.key, WorkSource.JIRA, repo, ticket.title, rank, ticket.created_at)

    def _issues(self, login: str) -> List[WorkItem]:
        if not self._company.settings.repos:
            return []
        tracker = self._sources.issues()
        return [self._issue_item(ticket, repo) for repo in self._company.settings.repos for ticket in tracker.assigned(repo, login)]

    def _issue_item(self, ticket: Ticket, repo: str) -> WorkItem:
        rank = PriorityRank().github(ticket.labels, self._company.settings.priority_labels)
        return WorkItem(WorkKind.TICKET, f"{repo}{ticket.key}", WorkSource.GITHUB, repo, ticket.title, rank, ticket.created_at)

    def _reviews(self, book: WorkBook, login: str) -> List[WorkItem]:
        found = [self._review(entry, login) for entry in book.open_prs()]
        return [item for item in found if item.comments]

    def _review(self, entry: WorkEntry, login: str) -> WorkItem:
        comments = self._sources.github().list_pr_comments(entry.repo, entry.pr)
        new = tuple(comment.id for comment in comments if comment.author != login and comment.id not in entry.handled_comments)
        return WorkItem(WorkKind.REVIEW, entry.key, entry.source, entry.repo, entry.title, pr=entry.pr, comments=new)


class ModelLogin:
    """The login of the model (S3): the Anthropic API key or a Claude subscription token."""

    NAMES: ClassVar[Tuple[str, ...]] = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def problem(self) -> str:
        if any(self._environ.get(name, "").strip() for name in self.NAMES):
            return ""
        return "no model login: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (in the environment or secrets.env)"


class WriteCheck:
    """S29: the logins can write what `serve` writes, checked with no write: GitHub
    `permissions.push` of each repo, Jira `mypermissions` of each project."""

    FIX = "give this login write access"

    def __init__(self, company: ServeCompany, sources: WorkSources) -> None:
        self._company = company
        self._sources = sources

    def results(self) -> List[CheckResult]:
        github = self._sources.github()
        lines = [self._line("github", repo, partial(github.can_write, repo)) for repo in self._company.settings.all_repos()]
        if self._company.settings.jira:
            jira = self._sources.jira()
            lines += [self._line("jira", project, partial(jira.can_write, project, self._jira_permissions())) for project in self._company.settings.jira]
        return lines

    def _jira_permissions(self) -> List[str]:
        status = self._company.settings.status
        return ["ADD_COMMENTS", *(["TRANSITION_ISSUES"] if status.started or status.review else [])]

    def _line(self, source: str, resource: str, action: Callable[[], None]) -> CheckResult:
        try:
            action()
        except SourceUnreachable as error:
            return CheckResult(source, self._company.name, CheckState.UNREACHABLE, f"could not reach it to check writing {resource} ({error})")
        except CliError as error:
            return CheckResult(source, self._company.name, CheckState.FAILED, f"cannot write {resource} ({error})", self.FIX)
        return CheckResult(source, self._company.name, CheckState.OK, f"can write {resource}")


class ServeCheck:
    """Before the first cycle: the model login, then the read and write checks of every
    company; any failure stops `serve` with every failed line (G13, J8)."""

    def __init__(self, companies: Sequence[ServeCompany], environ: Mapping[str, str]) -> None:
        self._companies = companies
        self._environ = environ

    def require(self) -> None:
        problem = ModelLogin(self._environ).problem()
        if problem:
            raise CliError(problem)
        lines = [line for company in self._companies for line in self._company_lines(company)]
        if not all(line.passed() for line in lines):
            raise LoginCheckFailed.from_results(lines)

    def _company_lines(self, company: ServeCompany) -> List[CheckResult]:
        sources = WorkSources(company.logins)
        targets = [LoginTarget(sources.github(), tuple(company.settings.all_repos()))]
        if company.settings.jira:
            targets.append(LoginTarget(sources.jira(), tuple(company.settings.jira)))
        read = LoginCheck(company.logins).check(targets)
        return read if not all(line.passed() for line in read) else [*read, *WriteCheck(company, sources).results()]


class ServeCompanies:
    """The companies with a `serve:` block in the runbooks of a folder."""

    def __init__(self, directory: Path, environ: Mapping[str, str]) -> None:
        self._directory = directory
        self._environ = environ

    def load(self) -> List[ServeCompany]:
        from briar.iac.runbook import load_runbook_file

        found: List[ServeCompany] = []
        for path in sorted(self._directory.glob("*.yaml")):
            runbook = load_runbook_file(path)
            found += [self._company(path, name, entry) for name, entry in runbook.companies.items() if entry.serve is not None]
        return found

    def _company(self, path: Path, name: str, entry: Any) -> ServeCompany:
        scope = LoginScope(name, str(path), f"companies.{name}.auth", runbook=str(path))
        root = Path(entry.knowledge.root or "./knowledge")
        state = WorkState(make_store("file", file_root=root), name, asyncio.Lock())
        return ServeCompany(name, entry.serve, CompanyLogins(scope, entry.auth, self._environ), state, root)


__all__ = ["ModelLogin", "PriorityRank", "ServeCheck", "ServeCompanies", "ServeCompany", "WorkFinder", "WorkItem", "WorkKind", "WorkSources", "WriteCheck"]
