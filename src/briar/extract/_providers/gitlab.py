"""GitLab `RepositoryProvider` (spec 008 PR 3a, decisions G2 to G5, G17, G18 in
`docs/decisions/2026-11-12-briar-2-work-selection-pr3.md`).

`GitlabApi` is the only class that talks to `python-gitlab`: it calls REST paths and gives
plain dicts; the library holds the token, the pagination and the retries (G17).
`GitlabProvider` turns those answers into the `_provider` dataclasses the extractors read.
A repo is the full project path, of any depth (`group/sub/app`, G2)."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import cached_property, partial
from itertools import islice
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Protocol
from urllib.parse import quote, urlsplit

from briar.auth.logins import CompanyLogins, GitlabLogin
from briar.decorators import swallow_errors
from briar.extract._login_failures import GitlabFailures
from briar.extract._provider import (
    BranchProtection,
    CiFailure,
    CiRun,
    CodeSearchHit,
    Commit,
    CommentKind,
    Deployment,
    Environment,
    PullRequest,
    Release,
    RepositoryProvider,
    ReviewComment,
    TreeEntry,
)


class GitlabRest(Protocol):
    """The calls `GitlabProvider` makes: one object, a list (at most `cap` rows, 0 = all), a text."""

    def get(self, path: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]: ...

    def pages(self, path: str, query: Optional[Dict[str, Any]] = None, cap: int = 0) -> List[Dict[str, Any]]: ...

    def text(self, path: str, query: Optional[Dict[str, Any]] = None) -> str: ...

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]: ...

    def put(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]: ...


class GitlabApi:
    """`GitlabRest` over `python-gitlab`, built on the first call."""

    TIMEOUT = 30
    # G17: a few tries on a 429 (the library waits the `Retry-After`), a 5xx or a timeout.
    ATTEMPTS = 4
    PER_PAGE = 100

    def __init__(self, login: GitlabLogin) -> None:
        self._login = login

    def get(self, path: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        found: Dict[str, Any] = self._client.http_get(path, query_data=query or {}, max_retries=self.ATTEMPTS)
        return found

    def pages(self, path: str, query: Optional[Dict[str, Any]] = None, cap: int = 0) -> List[Dict[str, Any]]:
        rows = self._client.http_list(path, query_data={**(query or {}), "per_page": self.PER_PAGE}, iterator=True, max_retries=self.ATTEMPTS)
        return list(islice(rows, cap or None))

    def text(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        answer = self._client.http_get(path, query_data=query or {}, raw=True, max_retries=self.ATTEMPTS)
        return str(answer.text)

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        found: Dict[str, Any] = self._client.http_post(path, post_data=body, max_retries=self.ATTEMPTS)
        return found

    def put(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        found: Dict[str, Any] = self._client.http_put(path, post_data=body, max_retries=self.ATTEMPTS)
        return found

    @cached_property
    def _client(self) -> Any:
        import gitlab

        return gitlab.Gitlab(self._login.url(), private_token=self._login.token(), timeout=self.TIMEOUT, retry_transient_errors=True)


class GitlabMembers:
    """The people of the GitLab groups of the token, for the company filter of the meetings
    (F3b): every group it is a member of, then the active members of each."""

    source = "gitlab"
    # The lowest access level: every group the token can see as a member.
    GUEST = 10
    ACTIVE = "active"

    def __init__(self, rest: GitlabRest) -> None:
        self._rest = rest

    def names(self) -> List[str]:
        return GitlabFailures().answer(self._people)

    def _people(self) -> List[str]:
        """F5: the user of the token is left out (it speaks in the meetings of every company)."""
        owner = self._rest.get("/user").get("id")
        groups = self._rest.pages("/groups", {"min_access_level": self.GUEST})
        members = [member for group in groups for member in self._rest.pages(f"/groups/{group['id']}/members/all")]
        return [str(member.get("name") or "") for member in members if member.get("state") == self.ACTIVE and member.get("id") != owner]


class Names:
    """Usernames out of GitLab user objects."""

    def one(self, user: Any) -> str:
        return str((user or {}).get("username") or "")

    def many(self, users: Any) -> List[str]:
        return [self.one(user) for user in users or []]


class MergeRequestOf:
    """A GitLab merge request as a `PullRequest`."""

    BODY_CAP = 1500

    def __init__(self, row: Mapping[str, Any]) -> None:
        self._row = row

    def pull(self) -> PullRequest:
        row, names = self._row, Names()
        return PullRequest(
            number=int(row.get("iid") or 0),
            title=str(row.get("title") or ""),
            author=names.one(row.get("author")),
            is_draft=bool(row.get("draft") or row.get("work_in_progress")),
            head_ref=str(row.get("source_branch") or ""),
            base_ref=str(row.get("target_branch") or ""),
            review_comment_count=int(row.get("user_notes_count") or 0),
            created_at=str(row.get("created_at") or ""),
            merged_at=str(row.get("merged_at") or ""),
            requested_reviewers=names.many(row.get("reviewers")),
            assignees=names.many(row.get("assignees")),
            body=str(row.get("description") or "")[: self.BODY_CAP],
            changed_files=self._changed_files(),
        )

    def _changed_files(self) -> int:
        """GitLab gives a string, "1000+" past its diff limit."""
        return int(re.sub(r"\D", "", str(self._row.get("changes_count") or "")) or 0)


class NoteOf:
    """One note of an MR discussion as a `ReviewComment`; a note with a position is inline."""

    BODY_CAP = 1500

    def __init__(self, note: Mapping[str, Any], discussion: str) -> None:
        self._note = note
        self._discussion = discussion

    def comment(self) -> ReviewComment:
        note = self._note
        position = note.get("position") or {}
        return ReviewComment(
            id=str(note.get("id") or ""),
            author=Names().one(note.get("author")),
            body=str(note.get("body") or "")[: self.BODY_CAP],
            file_path=str(position.get("new_path") or position.get("old_path") or ""),
            line=int(position.get("new_line") or position.get("old_line") or 0),
            is_resolved=bool(note.get("resolved")),
            created_at=str(note.get("created_at") or ""),
            kind=CommentKind.INLINE if position else CommentKind.CONVERSATION,
            updated_at=str(note.get("updated_at") or ""),
            thread=self._discussion,
        )


class PipelineOf:
    """A GitLab pipeline as a `CiRun`, in the GitHub Actions words `ci-health` compares."""

    # G8: a pipeline stopped at a manual job is done, as `action_required` is on GitHub.
    CONCLUSION: ClassVar[Mapping[str, str]] = {
        "success": "success",
        "failed": "failure",
        "canceled": "cancelled",
        "skipped": "skipped",
        "manual": "action_required",
    }
    RUNNING: ClassVar[Mapping[str, str]] = {"running": "in_progress"}

    def __init__(self, row: Mapping[str, Any]) -> None:
        self._row = row

    def ci_run(self) -> CiRun:
        row = self._row
        status = str(row.get("status") or "")
        done = status in self.CONCLUSION
        return CiRun(
            name=str(row.get("name") or row.get("source") or ""),
            status="completed" if done else self.RUNNING.get(status, "queued"),
            conclusion=self.CONCLUSION.get(status, ""),
            head_branch=str(row.get("ref") or ""),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
        )


class BranchRules:
    """The protection of one branch and the approval rules of its project."""

    NOT_FOUND = 404
    # Approval rules are a paid feature: a free plan answers one of these.
    NO_APPROVAL_RULES: ClassVar[FrozenSet[int]] = frozenset({403, 404})

    def __init__(self, rest: GitlabRest, project: str) -> None:
        self._rest = rest
        self._project = project

    def protected(self, name: str) -> Optional[Dict[str, Any]]:
        """The protected branch, None when the branch has no rule."""
        from gitlab.exceptions import GitlabHttpError

        try:
            return self._rest.get(f"{self._project}/protected_branches/{quote(name, safe='')}")
        except GitlabHttpError as error:
            if error.response_code == self.NOT_FOUND:
                return None
            raise

    def approvals(self) -> int:
        """The most approvals any rule asks for; 0 on a plan without approval rules."""
        from gitlab.exceptions import GitlabHttpError

        try:
            rules = self._rest.pages(f"{self._project}/approval_rules")
        except GitlabHttpError as error:
            if error.response_code in self.NO_APPROVAL_RULES:
                return 0
            raise
        return max((int(rule.get("approvals_required") or 0) for rule in rules), default=0)


class GitlabProvider(RepositoryProvider):
    kind = "gitlab"
    login_source = "gitlab"

    # The last lines of a failed job log, as GitHub keeps.
    TAIL_LINES = 80
    # Commits whose changed files are read (one call each), as GitHub reads.
    COMMIT_FILES_CAP = 50
    MESSAGE_CAP = 200
    ENVIRONMENTS_CAP = 100
    # The defaults of the `RepositoryProvider` verbs.
    SINCE_DAYS, MAX_COMMITS, MAX_TREE, MAX_HITS, MAX_RELEASES = 30, 200, 5000, 200, 100
    STATES: ClassVar[Mapping[str, str]] = {"open": "opened", "merged": "merged"}
    NEWEST: ClassVar[Mapping[str, str]] = {"order_by": "created_at", "sort": "desc"}

    def __init__(self, *, logins: Optional[CompanyLogins] = None, api: Optional[GitlabRest] = None) -> None:
        self._logins = logins
        self._api = api

    # ---- login, clone ------------------------------------------------------

    def is_available(self) -> bool:
        """The login is the job of the check before the run (spec 005, J15)."""
        return True

    def who_am_i(self) -> str:
        return GitlabFailures().account(self._username)

    def read(self, resource: str) -> None:
        GitlabFailures().read(partial(self._rest.get, self._project(resource)))

    def resolve_token(self) -> str:
        return self._login().token()

    def clone_url(self, owner: str, repo: str) -> str:
        return f"{self._login().url()}/{owner}/{repo}.git"

    def authed_clone_url(self, owner: str, repo: str, token: str) -> str:
        site = urlsplit(self._login().url())
        return f"{site.scheme}://oauth2:{token}@{site.netloc}/{owner}/{repo}.git"

    # ---- merge requests ----------------------------------------------------

    @swallow_errors(default=[], message="gitlab list_pulls")
    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]:
        query = {"state": self.STATES.get(state, state), **self.NEWEST}
        rows = self._rest.pages(f"{self._project(repo)}/merge_requests", query, max_count)
        return [MergeRequestOf(row).pull() for row in rows]

    @swallow_errors(default=None, message="gitlab get_pull")
    def get_pull(self, repo: str, number: int) -> PullRequest:
        return MergeRequestOf(self._rest.get(f"{self._project(repo)}/merge_requests/{number}")).pull()

    @swallow_errors(default=[], message="gitlab list_pr_comments")
    def list_pr_comments(self, repo: str, number: int) -> List[ReviewComment]:
        discussions = self._rest.pages(f"{self._project(repo)}/merge_requests/{number}/discussions")
        return [
            NoteOf(note, str(discussion.get("id") or "")).comment()
            for discussion in discussions
            for note in discussion.get("notes") or []
            if not note.get("system")
        ]

    # ---- pipelines ---------------------------------------------------------

    @swallow_errors(default=[], message="gitlab list_ci_runs")
    def list_ci_runs(self, repo: str, *, limit: int) -> List[CiRun]:
        rows = self._rest.pages(f"{self._project(repo)}/pipelines", {"order_by": "id", "sort": "desc"}, limit)
        return [PipelineOf(row).ci_run() for row in rows]

    @swallow_errors(default=[], message="gitlab list_ci_failures")
    def list_ci_failures(self, repo: str, number: int) -> List[CiFailure]:
        """The failed jobs of the newest pipeline of the MR, each with the end of its log."""
        project = self._project(repo)
        pipelines = self._rest.pages(f"{project}/merge_requests/{number}/pipelines", {}, 1)
        if not pipelines:
            return []
        jobs = self._rest.pages(f"{project}/pipelines/{pipelines[0]['id']}/jobs", {"scope[]": "failed"})
        return [self._failure(project, job) for job in jobs]

    # ---- repository --------------------------------------------------------

    @swallow_errors(default=[], message="gitlab list_recent_commits")
    def list_recent_commits(self, repo: str, *, since_days: int = SINCE_DAYS, max_count: int = MAX_COMMITS) -> List[Commit]:
        project = self._project(repo)
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self._rest.pages(f"{project}/repository/commits", {"since": since}, max_count)
        return [self._commit(row, self._files(project, row) if index < self.COMMIT_FILES_CAP else []) for index, row in enumerate(rows)]

    @swallow_errors(default="", message="gitlab read_file")
    def read_file(self, repo: str, path: str) -> str:
        """"" for a file that is not there: extractors probe common names, as on GitHub."""
        from gitlab.exceptions import GitlabHttpError

        try:
            return self._rest.text(f"{self._project(repo)}/repository/files/{quote(path, safe='')}/raw", {"ref": self.default_branch(repo)})
        except GitlabHttpError as error:
            if error.response_code == BranchRules.NOT_FOUND:
                return ""
            raise

    @swallow_errors(default="", message="gitlab default_branch")
    def default_branch(self, repo: str) -> str:
        return str(self._rest.get(self._project(repo)).get("default_branch") or "")

    @swallow_errors(default=[], message="gitlab list_tree")
    def list_tree(self, repo: str, *, max_count: int = MAX_TREE) -> List[TreeEntry]:
        rows = self._rest.pages(f"{self._project(repo)}/repository/tree", {"recursive": True}, max_count)
        return [TreeEntry(str(row.get("path") or ""), row.get("type") == "blob") for row in rows]

    @swallow_errors(default=[], message="gitlab search_code")
    def search_code(self, repo: str, query: str, *, max_count: int = MAX_HITS) -> List[CodeSearchHit]:
        # GitLab blob search has no OR (a real "TODO OR FIXME OR HACK" found nothing): one search per word.
        # ponytail: max_count caps each word, not the sum.
        words = [word.strip() for word in query.split(" OR ") if word.strip()]
        rows = [row for word in words for row in self._rest.pages(f"{self._project(repo)}/search", {"scope": "blobs", "search": word}, max_count)]
        counts = Counter(str(row.get("path") or "") for row in rows)
        return [CodeSearchHit(path, matches) for path, matches in counts.items()]

    @swallow_errors(default=[], message="gitlab list_releases")
    def list_releases(self, repo: str, *, max_count: int = MAX_RELEASES) -> List[Release]:
        rows = self._rest.pages(f"{self._project(repo)}/releases", {}, max_count)
        return [
            Release(str(row.get("tag_name") or ""), str(row.get("name") or row.get("tag_name") or ""), str(row.get("released_at") or row.get("created_at") or ""), bool(row.get("upcoming_release")))
            for row in rows
        ]

    # ---- governance, deploys -----------------------------------------------

    @swallow_errors(default=BranchProtection(branch="", exists=False), message="gitlab get_branch_protection")
    def get_branch_protection(self, repo: str, branch: str = "") -> BranchProtection:
        project = self._project(repo)
        settings = self._rest.get(project)
        name = branch or str(settings.get("default_branch") or "")
        rules = BranchRules(self._rest, project)
        protected = rules.protected(name)
        if protected is None:
            return BranchProtection(branch=name, exists=False)
        return BranchProtection(
            branch=name,
            exists=True,
            required_reviews=rules.approvals(),
            requires_status_checks=bool(settings.get("only_allow_merge_if_pipeline_succeeds")),
            requires_code_owner_review=bool(protected.get("code_owner_approval_required")),
        )

    @swallow_errors(default=[], message="gitlab list_environments")
    def list_environments(self, repo: str) -> List[Environment]:
        rows = self._rest.pages(f"{self._project(repo)}/environments", {}, self.ENVIRONMENTS_CAP)
        return [Environment(str(row.get("name") or ""), 0, str(row.get("external_url") or "")) for row in rows]

    @swallow_errors(default=[], message="gitlab list_deployments")
    def list_deployments(self, repo: str, *, limit: int) -> List[Deployment]:
        rows = self._rest.pages(f"{self._project(repo)}/deployments", dict(self.NEWEST), limit)
        names = Names()
        return [
            Deployment(str(row.get("id") or ""), str((row.get("environment") or {}).get("name") or ""), str(row.get("sha") or ""), names.one(row.get("user")), str(row.get("created_at") or ""))
            for row in rows
        ]

    # ---- steps -------------------------------------------------------------

    @cached_property
    def _rest(self) -> GitlabRest:
        return self._api or GitlabApi(self._login())

    def _login(self) -> GitlabLogin:
        return (self._logins or CompanyLogins.defaults("")).gitlab()

    def _project(self, repo: str) -> str:
        return f"/projects/{quote(repo, safe='')}"

    def _username(self) -> str:
        return Names().one(self._rest.get("/user"))

    def _failure(self, project: str, job: Mapping[str, Any]) -> CiFailure:
        log = self._rest.text(f"{project}/jobs/{job['id']}/trace")
        stage = str(job.get("stage") or "")
        return CiFailure(stage, str(job.get("name") or ""), stage, "\n".join(log.splitlines()[-self.TAIL_LINES :]), str(job.get("web_url") or ""))

    def _files(self, project: str, row: Mapping[str, Any]) -> List[str]:
        return [str(change.get("new_path") or "") for change in self._rest.pages(f"{project}/repository/commits/{row.get('id')}/diff")]

    def _commit(self, row: Mapping[str, Any], files: List[str]) -> Commit:
        return Commit(str(row.get("id") or ""), str(row.get("author_name") or ""), str(row.get("title") or "")[: self.MESSAGE_CAP], str(row.get("created_at") or ""), files)



__all__ = ["GitlabApi", "GitlabProvider", "GitlabRest"]
