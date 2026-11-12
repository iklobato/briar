"""What `briar serve` writes and reads on GitLab (spec 008 PR 3b, G6 to G9 in
`docs/decisions/2026-11-12-briar-2-work-selection-pr3.md`): draft merge requests, notes,
replies in discussions, the head pipeline, the merge state, the review notes with the access
level of each author, and the write check. Every call raises `LoginRejected` or
`SourceUnreachable` (through `GitlabFailures`), so a failed write stops the task with a report."""

from __future__ import annotations

import re
from dataclasses import replace
from functools import cached_property, partial
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Set
from urllib.parse import quote

from briar.auth.check import LoginRejected
from briar.auth.logins import GitlabLogin
from briar.extract._login_failures import GitlabFailures
from briar.extract._provider import BranchPull, CiFailure, CiState, PullRequest, PullRequestDraft, PullState, ReviewComment, ThreadReply
from briar.extract._providers.gitlab import BranchRules, GitlabApi, GitlabProvider, GitlabRest, MergeRequestOf, Names, NoteOf


class DraftTitle:
    """GitLab keeps "draft" in the title: `Draft:` (also read: `[Draft]`, `(Draft)`, `WIP:`)."""

    PREFIX = "Draft: "
    MARKS: ClassVar["re.Pattern[str]"] = re.compile(r"^\s*(draft:|\[draft\]|\(draft\)|wip:)\s*", re.IGNORECASE)

    def __init__(self, title: str) -> None:
        self._title = title

    def ready(self) -> str:
        return self.MARKS.sub("", self._title)

    def draft(self) -> str:
        return f"{self.PREFIX}{self.ready()}"


class PipelineState:
    """G8: the head pipeline of an MR as one CI state."""

    PASSED: ClassVar[FrozenSet[str]] = frozenset({"success", "skipped", "manual"})
    FAILED: ClassVar[FrozenSet[str]] = frozenset({"failed", "canceled"})

    def __init__(self, pipeline: Optional[Mapping[str, Any]]) -> None:
        self._pipeline = pipeline

    def state(self) -> CiState:
        if not self._pipeline:
            return CiState.NONE
        status = str(self._pipeline.get("status") or "")
        if status in self.PASSED:
            return CiState.PASSED
        return CiState.FAILED if status in self.FAILED else CiState.PENDING


class Authors:
    """G7, G11: the access level of each note author (one call per person) and the bot users."""

    DEVELOPER = 30
    # The users GitLab makes for project and group access tokens.
    BOT: ClassVar["re.Pattern[str]"] = re.compile(r"^(project|group)_\d+_bot(_[0-9a-f]+)?$")

    def __init__(self, rest: GitlabRest, project: str) -> None:
        self._rest = rest
        self._project = project
        self._levels: Dict[int, int] = {}

    def comment(self, note: Mapping[str, Any], discussion: str) -> ReviewComment:
        found = NoteOf(note, discussion).comment()
        author = note.get("author") or {}
        writes = self._level(int(author.get("id") or 0)) >= self.DEVELOPER
        bot = bool(self.BOT.match(Names().one(author)))
        return replace(found, association="MEMBER" if writes else "", bot=bot)

    def _level(self, user: int) -> int:
        if user not in self._levels:
            self._levels[user] = self._read(user)
        return self._levels[user]

    def _read(self, user: int) -> int:
        from gitlab.exceptions import GitlabHttpError

        try:
            return int(self._rest.get(f"{self._project}/members/all/{user}").get("access_level") or 0)
        except GitlabHttpError as error:
            if error.response_code == BranchRules.NOT_FOUND:
                return 0
            raise


class GitlabHost:
    """The `CodeHost` of `briar serve` on GitLab; a repo is the full project path (G2)."""

    login_source = "gitlab"
    # `api` writes through the API and pushes over Git HTTP; `write_repository` alone cannot open an MR.
    WRITE_SCOPE = "api"

    def __init__(self, login: GitlabLogin, api: Optional[GitlabRest] = None) -> None:
        self._login = login
        self._api = api

    # ---- login and checks --------------------------------------------------

    def who_am_i(self) -> str:
        return self._reader.who_am_i()

    def read(self, resource: str) -> None:
        self._reader.read(resource)

    def resolve_token(self) -> str:
        return self._login.token()

    def commit_email(self) -> str:
        """G9: the commit email of the profile, else the main email."""
        user = GitlabFailures().answer(partial(self._rest.get, "/user"))
        return str(user.get("commit_email") or user.get("email") or "")

    def can_write(self, repo: str) -> None:
        """S29 on GitLab: the login is a Developer or above, and the token may write."""
        GitlabFailures().read(partial(self._check_write, repo))

    def default_branch(self, repo: str) -> str:
        return str(GitlabFailures().answer(partial(self._rest.get, self._project(repo))).get("default_branch") or "")

    # ---- merge requests ----------------------------------------------------

    def open_pull(self, repo: str, draft: PullRequestDraft) -> int:
        title = DraftTitle(draft.title).draft() if draft.draft else draft.title
        body = {"source_branch": draft.head, "target_branch": draft.base, "title": title, "description": draft.body}
        return int(GitlabFailures().answer(partial(self._rest.post, f"{self._project(repo)}/merge_requests", body))["iid"])

    def comment(self, repo: str, number: int, text: str) -> None:
        GitlabFailures().read(partial(self._rest.post, f"{self._mr(repo, number)}/notes", {"body": text}))

    def reply_in_thread(self, repo: str, reply: ThreadReply) -> None:
        path = f"{self._mr(repo, reply.number)}/discussions/{reply.thread}/notes"
        GitlabFailures().read(partial(self._rest.post, path, {"body": reply.text}))

    def make_draft(self, repo: str, number: int) -> None:
        GitlabFailures().read(partial(self._retitle, repo, number, DraftTitle.draft))

    def make_ready(self, repo: str, number: int) -> None:
        GitlabFailures().read(partial(self._retitle, repo, number, DraftTitle.ready))

    # ---- state -------------------------------------------------------------

    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]:
        """Like the extract verb, but an error raises: the open MR rule must not read a failure as "no MR"."""
        query = {"state": GitlabProvider.STATES.get(state, state), **GitlabProvider.NEWEST}
        rows = GitlabFailures().answer(partial(self._rest.pages, f"{self._project(repo)}/merge_requests", query, max_count))
        return [MergeRequestOf(row).pull() for row in rows]

    def ci_state(self, repo: str, number: int) -> CiState:
        return PipelineState(self._merge_request(repo, number).get("head_pipeline")).state()

    def list_ci_failures(self, repo: str, number: int) -> List[CiFailure]:
        return self._reader.list_ci_failures(repo, number)

    def pull_state(self, repo: str, number: int) -> PullState:
        found = self._merge_request(repo, number)
        state = str(found.get("state") or "")
        start = str((found.get("diff_refs") or {}).get("start_sha") or "")
        return PullState(state == "opened", state == "merged", bool(found.get("has_conflicts")), str(found.get("target_branch") or ""), bool(found.get("draft")), start)

    def branch_head(self, repo: str, branch: str) -> str:
        found = GitlabFailures().answer(partial(self._rest.get, f"{self._project(repo)}/repository/branches/{quote(branch, safe='')}"))
        return str((found.get("commit") or {}).get("id") or "")

    def branch_pull(self, repo: str, branch: str) -> Optional[BranchPull]:
        """The newest MR, open or not, from `branch` (a lost state never opens a second MR)."""
        query = {"source_branch": branch, "state": "all", "order_by": "created_at", "sort": "desc"}
        found = GitlabFailures().answer(partial(self._rest.pages, f"{self._project(repo)}/merge_requests", query, 1))
        if not found:
            return None
        newest = found[0]
        return BranchPull(int(newest["iid"]), newest.get("state") == "opened", newest.get("state") == "merged", bool(newest.get("draft")))

    def review_comments(self, repo: str, number: int) -> List[ReviewComment]:
        authors = Authors(self._rest, self._project(repo))
        return [authors.comment(note, str(discussion.get("id") or "")) for discussion in self._discussions(repo, number) for note in discussion.get("notes") or [] if not note.get("system")]

    def resolved_comment_ids(self, repo: str, number: int) -> Set[str]:
        return {str(note.get("id")) for discussion in self._discussions(repo, number) for note in discussion.get("notes") or [] if note.get("resolvable") and note.get("resolved")}

    # ---- steps -------------------------------------------------------------

    @cached_property
    def _rest(self) -> GitlabRest:
        return self._api or GitlabApi(self._login)

    @cached_property
    def _reader(self) -> GitlabProvider:
        # Its reads go through the same REST surface; it never builds a client of its own.
        return GitlabProvider(logins=None, api=self._rest)

    def _project(self, repo: str) -> str:
        return f"/projects/{quote(repo, safe='')}"

    def _mr(self, repo: str, number: int) -> str:
        return f"{self._project(repo)}/merge_requests/{number}"

    def _merge_request(self, repo: str, number: int) -> Dict[str, Any]:
        found: Dict[str, Any] = GitlabFailures().answer(partial(self._rest.get, self._mr(repo, number)))
        return found

    def _discussions(self, repo: str, number: int) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = GitlabFailures().answer(partial(self._rest.pages, f"{self._mr(repo, number)}/discussions"))
        return found

    def _retitle(self, repo: str, number: int, change: Any) -> None:
        title = str(self._rest.get(self._mr(repo, number)).get("title") or "")
        self._rest.put(self._mr(repo, number), {"title": change(DraftTitle(title))})

    def _check_write(self, repo: str) -> None:
        access = self._rest.get(self._project(repo)).get("permissions") or {}
        levels = [int((access.get(kind) or {}).get("access_level") or 0) for kind in ("project_access", "group_access")]
        if max(levels) < Authors.DEVELOPER:
            raise LoginRejected("the login is not a Developer of the project: give it the Developer role or above")
        scopes = set(self._rest.get("/personal_access_tokens/self").get("scopes") or [])
        if self.WRITE_SCOPE not in scopes:
            raise LoginRejected("the token cannot write: give it the api scope")


__all__ = ["GitlabHost"]
