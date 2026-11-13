"""What `briar serve` works on, and the checks before it starts (spec 003 PR 1).

The finder asks GitHub and Jira each cycle (S10): the PRs briar opened come first (S14,
S22): the merged or closed ones to record, and the ones with new comments of people with
write access or listed bots (V1 to V5) or in conflict with their base (V6); then the open
tickets assigned to the account of the token (S9), highest priority first, then the
oldest. Before the first cycle `serve` checks the model
login (S3), the read logins (#99) and the write access (S29) of every company."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, replace
from enum import Enum
from functools import partial
from pathlib import Path
from typing import Any, Callable, ClassVar, List, Mapping, Optional, Sequence, Set, Tuple

from briar.auth.check import CheckResult, CheckState, LoginCheck, LoginCheckFailed, LoginTarget, SourceUnreachable
from briar.auth.logins import CompanyLogins, LoginScope
from briar.errors import CliError
from briar.extract._provider import CommentKind, PullState, ReviewComment
from briar.extract._tracker import Ticket
from briar.iac.runbook.models import CodeHostKind, ServeSettings
from briar.serve.git import RepoCheckout, Shell
from briar.serve.host import GithubStyle, GitlabStyle, HostStyle
from briar.serve.state import WorkBook, WorkEntry, WorkSource, WorkState, WorkStatus
from briar.storage import make_store


class WorkKind(Enum):
    REVIEW = "review"
    TICKET = "ticket"
    # A briar PR that was merged or closed: the task only records it (V3).
    MERGED = "merged"
    CLOSED = "closed"
    # A briar PR still a draft in `pr-open`: `serve` stopped during the CI wait; finish it.
    READY = "ready"


class Marker:
    """The hidden mark on every GitHub comment briar posts (V2): briar skips only marked
    comments, so the owner of the token can still steer it from the PR."""

    TEXT = "<!-- briar -->"

    def mark(self, text: str) -> str:
        return f"{text}\n\n{self.TEXT}"

    def absent(self, body: str) -> bool:
        return self.TEXT not in body


@dataclass(frozen=True)
class WorkItem:
    """One task: a ticket to build, a briar PR with new review comments or a conflict, or
    a briar PR that ended."""

    kind: WorkKind
    key: str
    source: WorkSource
    repo: str
    title: str
    rank: int = 0
    created_at: str = ""
    pr: int = 0
    comments: Tuple[ReviewComment, ...] = ()
    conflict: bool = False
    # Spec 008: the repos a ticket may be built in, the ticket with its signals, why the
    # selector picked it and what that choice cost (paid from the task budget, N9).
    repos: Tuple[str, ...] = ()
    ticket: Optional[Ticket] = None
    reason: str = ""
    selection_cost: float = 0.0

    def order(self) -> Tuple[int, int, str]:
        """The PRs first (S14), then the rank of the priority, then the oldest."""
        return (1 if self.kind is WorkKind.TICKET else 0, self.rank, self.created_at)

    def entry(self) -> WorkEntry:
        return WorkEntry(key=self.key, source=self.source, repo=self.repo, title=self.title, status=WorkStatus.FOUND, reason=self.reason)

    def resumed(self, entry: WorkEntry) -> "WorkItem":
        """The ticket again, in the repo briar started it in and with why it was picked."""
        return replace(self, repo=entry.repo, reason=entry.reason)

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


class ServeLock:
    """One `briar serve` per company and knowledge folder. The state lock is in-process
    only; a real run (2026-11-13) with two `serve` on one folder took the same issue twice
    and opened a duplicate PR. The lock lasts as long as the process (the OS drops it)."""

    def __init__(self, company: ServeCompany) -> None:
        self._company = company
        self._held: List[Any] = []

    def take(self) -> None:
        import fcntl

        path = self._company.state_file().with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as busy:
            handle.close()
            raise CliError(f"serve: another `briar serve` runs on the company {self._company.name} ({path}); stop it first") from busy
        self._held.append(handle)

    def release(self) -> None:
        for handle in self._held:
            handle.close()
        self._held.clear()


class BranchName:
    """The branch briar makes for a ticket (S27): `serve.branch` with the key, the title and
    the number, made safe for git."""

    UNSAFE: ClassVar["re.Pattern[str]"] = re.compile(r"[^A-Za-z0-9._/-]+")

    def __init__(self, settings: ServeSettings) -> None:
        self._settings = settings

    def of(self, item: WorkItem) -> str:
        fields = {"key": item.ref(), "title": item.title, "number": item.number()}
        return self.UNSAFE.sub("-", self._settings.branch.format_map(fields)).strip("-")


class WorkSources:
    """The adapters `serve` reads through, built with the company logins; the code host is
    GitHub or GitLab (G1)."""

    def __init__(self, logins: CompanyLogins, kind: CodeHostKind = CodeHostKind.GITHUB) -> None:
        self._logins = logins
        self._kind = kind

    def code_host(self) -> Any:
        """A `CodeHost` (`briar.serve.host`)."""
        from briar.extract._providers import make_provider
        from briar.extract._providers.gitlab_host import GitlabHost

        if self._kind is CodeHostKind.GITLAB:
            return GitlabHost(self._logins.gitlab())
        return make_provider("github", self._logins)

    def style(self) -> HostStyle:
        if self._kind is CodeHostKind.GITLAB:
            return GitlabStyle(self._logins.gitlab().url(), self.code_host())
        return GithubStyle(self.code_host())

    def issues(self) -> Any:
        from briar.extract._trackers import make_tracker

        return make_tracker("github-issues", self._logins)

    def jira(self) -> Any:
        from briar.extract._trackers import make_tracker

        return make_tracker("jira", self._logins)

    def jira_links(self) -> Any:
        from briar.extract._trackers.jira import JiraRemoteLinks

        return JiraRemoteLinks(self._logins.jira())

    def slack(self) -> Any:
        from briar.serve.slack import SlackApi, SlackSource

        login = self._logins.slack()
        return SlackSource(login, SlackApi(login, None))


class WorkFinder:
    """The work of one company in one cycle, in the order `serve` takes it."""

    # The most tickets one Jira project gives per cycle.
    MAX_TICKETS = 50

    def __init__(self, company: ServeCompany, sources: WorkSources, drift: Optional[Any] = None) -> None:
        self._company = company
        self._sources = sources
        # `BaseDrift`: finds a conflict GitHub has not computed yet; None reads GitHub only.
        self._drift = drift

    def find(self) -> List[WorkItem]:
        book = self._company.state.book()
        login = self._sources.code_host().who_am_i() if self._company.settings.repos else ""
        tickets = [item for item in [*self._jira(), *self._issues(login)] if not book.taken(item.key)]
        return sorted([*self._pulls(book), *tickets], key=WorkItem.order)

    def _jira(self) -> List[WorkItem]:
        if not self._company.settings.jira:
            return []
        tracker = self._sources.jira()
        return [
            self._jira_item(ticket, tuple(project.repos))
            for key, project in self._company.settings.jira.items()
            for ticket in tracker.assigned(key, self.MAX_TICKETS)
        ]

    def _jira_item(self, ticket: Ticket, repos: Tuple[str, ...]) -> WorkItem:
        """The repo is the only allowed one, or chosen by the selector among `repos` (N5)."""
        rank = PriorityRank().jira(ticket.priority)
        return WorkItem(WorkKind.TICKET, ticket.key, WorkSource.JIRA, repos[0] if len(repos) == 1 else "", ticket.title, rank, ticket.created_at, repos=repos, ticket=ticket)

    def _issues(self, login: str) -> List[WorkItem]:
        if not self._company.settings.repos:
            return []
        tracker = self._sources.issues()
        return [self._issue_item(ticket, repo) for repo in self._company.settings.repos for ticket in tracker.assigned(repo, login)]

    def _issue_item(self, ticket: Ticket, repo: str) -> WorkItem:
        rank = PriorityRank().github(ticket.labels, self._company.settings.priority_labels)
        return WorkItem(WorkKind.TICKET, f"{repo}{ticket.key}", WorkSource.GITHUB, repo, ticket.title, rank, ticket.created_at, repos=(repo,), ticket=ticket)

    def _pulls(self, book: WorkBook) -> List[WorkItem]:
        found = [self._pull(entry) for entry in book.watched()]
        return [item for item in found if item is not None]

    def _pull(self, entry: WorkEntry) -> Optional[WorkItem]:
        github = self._sources.code_host()
        pull = github.pull_state(entry.repo, entry.pr)
        if not pull.open:
            return WorkItem(WorkKind.MERGED if pull.merged else WorkKind.CLOSED, entry.key, entry.source, entry.repo, entry.title, pr=entry.pr)
        resolved = github.resolved_comment_ids(entry.repo, entry.pr)
        new = ReviewFilter(self._company.settings, entry, resolved).new(github.review_comments(entry.repo, entry.pr))
        conflict = pull.conflict or (self._drift is not None and self._drift.conflicts(entry, pull))
        if new or conflict:
            return WorkItem(WorkKind.REVIEW, entry.key, entry.source, entry.repo, entry.title, pr=entry.pr, comments=tuple(new), conflict=conflict)
        unfinished = pull.draft and entry.status is WorkStatus.PR_OPEN
        return WorkItem(WorkKind.READY, entry.key, entry.source, entry.repo, entry.title, pr=entry.pr) if unfinished else None


class BaseDrift:
    """GitHub recomputes `mergeable_state` when a PR head changes, not when its base moves on:
    the click copy kept PR #5 `CLEAN` for 40 minutes after `main` moved. When the base GitHub
    used is older than the head of the base branch, the merge is tried in the clone instead."""

    def __init__(self, company: ServeCompany, sources: WorkSources, cache: Path) -> None:
        self._company = company
        self._sources = sources
        self._cache = cache

    def conflicts(self, entry: WorkEntry, pull: PullState) -> bool:
        if not pull.base_sha or self._sources.code_host().branch_head(entry.repo, pull.base) == pull.base_sha:
            return False
        checkout = RepoCheckout(Shell(), self._cache, self._sources.style().remote(entry.repo))
        return not checkout.merges_clean(pull.base, entry.branch, self._sources.code_host().resolve_token())


class Replied:
    """V4 when the state is lost: the answers of briar on the PR itself say what it answered.
    A conversation comment is answered when a later briar comment quotes its first line
    (`<author> wrote:\\n> <line>`); an inline one, when briar replied later in its thread."""

    def __init__(self, comments: Sequence[ReviewComment]) -> None:
        self._comments = comments

    def answered(self, comment: ReviewComment) -> bool:
        later = [answer for answer in self._comments if not Marker().absent(answer.body) and answer.created_at >= comment.updated_at]
        if comment.kind is CommentKind.INLINE:
            return any(answer.kind is CommentKind.INLINE and answer.thread == comment.thread for answer in later)
        quote = f"{comment.author} wrote:\n> {(comment.body.splitlines() or [''])[0]}"
        return any(quote in answer.body for answer in later)


class ReviewFilter:
    """The new comments of a briar PR that briar answers: from people with write access or
    a bot of `review_bots` (V1), not its own (V2), never answered or edited since (V4), and
    not in a resolved thread (V5)."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    WRITE: ClassVar[Tuple[str, ...]] = ("OWNER", "MEMBER", "COLLABORATOR")

    def __init__(self, settings: ServeSettings, entry: WorkEntry, resolved: Set[str]) -> None:
        self._settings = settings
        self._entry = entry
        self._resolved = resolved

    def new(self, comments: Sequence[ReviewComment]) -> List[ReviewComment]:
        replied = Replied(comments)
        fresh = [comment for comment in comments if self._fresh(comment) and not replied.answered(comment)]
        strangers = [comment for comment in fresh if not self._trusted(comment)]
        if strangers:
            self.LOG.info("serve: %s#%d: %d comments from accounts with no write access left alone", self._entry.repo, self._entry.pr, len(strangers))
        return [comment for comment in fresh if self._trusted(comment)]

    def _fresh(self, comment: ReviewComment) -> bool:
        resolved = comment.kind is CommentKind.INLINE and comment.id in self._resolved
        answered = self._entry.answered.get(comment.key()) == comment.updated_at
        return Marker().absent(comment.body) and not resolved and not answered

    def _trusted(self, comment: ReviewComment) -> bool:
        return comment.author in self._settings.review_bots or (not comment.bot and comment.association in self.WRITE)


class ModelLogin:
    """The login of the model (S3): the Anthropic API key or a Claude subscription token."""

    NAMES: ClassVar[Tuple[str, ...]] = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def problem(self) -> str:
        if any(self._environ.get(name, "").strip() for name in self.NAMES):
            return ""
        return "no model login: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (in the environment or secrets.env)"


class AgentSandbox:
    """V12, V14: what the sandbox of the SDK needs for the bash of the agent: the macOS
    sandbox, or bubblewrap and socat on Linux. Checked by briar itself, since what the SDK
    does without them is not known; no switch turns the sandbox off."""

    NEEDS: ClassVar[Tuple[Tuple[str, Tuple[str, ...]], ...]] = (("Darwin", ("sandbox-exec",)), ("Linux", ("bwrap", "socat")))
    PACKAGES: ClassVar[Tuple[Tuple[str, str], ...]] = (("bwrap", "bubblewrap"),)

    def __init__(self, system: str, which: Callable[[str], Optional[str]]) -> None:
        self._system = system
        self._which = which

    def problem(self) -> str:
        needs = dict(self.NEEDS).get(self._system)
        if needs is None:
            return f"no agent sandbox: briar serve runs on macOS and Linux only, not on {self._system}"
        missing = [self._package(name) for name in needs if not self._which(name)]
        return f"no agent sandbox: install {' and '.join(missing)}; briar serve runs the bash of the agent in it" if missing else ""

    def found(self) -> str:
        return " and ".join(dict(self.NEEDS).get(self._system, ()))

    def _package(self, program: str) -> str:
        package = dict(self.PACKAGES).get(program, "")
        return f"{program} (package {package})" if package else program


class WriteCheck:
    """S29: the logins can write what `serve` writes, checked with no write: GitHub
    `permissions.push` of each repo, Jira `mypermissions` of each project."""

    FIX = "give this login write access"

    def __init__(self, company: ServeCompany, sources: WorkSources) -> None:
        self._company = company
        self._sources = sources

    def results(self) -> List[CheckResult]:
        host = self._sources.code_host()
        lines = [self._line(host.login_source, repo, partial(host.can_write, repo)) for repo in self._company.settings.all_repos()]
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
    """Before the first cycle: the model login and the agent sandbox, then the read and
    write checks of every company; any failure stops `serve` with every failed line (G13,
    J8, V14)."""

    def __init__(self, companies: Sequence[ServeCompany], environ: Mapping[str, str], sandbox: AgentSandbox) -> None:
        self._companies = companies
        self._environ = environ
        self._sandbox = sandbox

    def require(self) -> None:
        problems = [problem for problem in (ModelLogin(self._environ).problem(), self._sandbox.problem()) if problem]
        if problems:
            raise CliError("\n".join(problems))
        lines = self.lines()
        if not all(line.passed() for line in lines):
            raise LoginCheckFailed.from_results(lines)

    def lines(self) -> List[CheckResult]:
        """The read and write lines of every company (`doctor --runbooks` shows them, X2)."""
        return [line for company in self._companies for line in self._company_lines(company)]

    def _company_lines(self, company: ServeCompany) -> List[CheckResult]:
        sources = WorkSources(company.logins, company.settings.code_host)
        targets = [LoginTarget(sources.code_host(), tuple(company.settings.all_repos()))]
        if company.settings.jira:
            targets.append(LoginTarget(sources.jira(), tuple(company.settings.jira.keys())))
        slack = company.settings.chat.slack
        if slack is not None:
            targets.append(LoginTarget(sources.slack(), tuple(slack.channels)))
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
        scope = LoginScope(name, str(path), f"companies.{name}.auth")
        # Absolute: the agent runs in the worktree, where a path relative to here points nowhere.
        root = Path(entry.knowledge.root or "./knowledge").resolve()
        state = WorkState(make_store("file", file_root=root), name, asyncio.Lock())
        return ServeCompany(name, entry.serve, CompanyLogins(scope, entry.auth, self._environ), state, root)


__all__ = [
    "ServeLock",
    "AgentSandbox",
    "BaseDrift",
    "BranchName",
    "Marker",
    "ModelLogin",
    "PriorityRank",
    "ReviewFilter",
    "ServeCheck",
    "ServeCompanies",
    "ServeCompany",
    "WorkFinder",
    "WorkItem",
    "WorkKind",
    "WorkSources",
    "WriteCheck",
]
