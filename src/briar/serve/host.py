"""The code host `briar serve` works on (spec 008 PR 3b, G1, G6, G9): the verbs it calls,
and what differs per host besides the API (the PR or MR link, the clone remote, the commit
identity, the word for a pull request)."""

from __future__ import annotations

from typing import List, Optional, Protocol, Set

from briar.extract._provider import BranchPull, CiFailure, CiState, PullRequest, PullRequestDraft, PullState, ReviewComment, ThreadReply
from briar.extract._providers.gitlab_host import GitlabHost
from briar.serve.git import GitIdentity, Remote


class CodeHost(Protocol):
    """G6: the verbs of `briar serve`; `GithubProvider` and `GitlabHost` have them."""

    login_source: str

    def who_am_i(self) -> str: ...

    def read(self, resource: str) -> None: ...

    def resolve_token(self) -> str: ...

    def can_write(self, repo: str) -> None: ...

    def default_branch(self, repo: str) -> str: ...

    def open_pull(self, repo: str, draft: PullRequestDraft) -> int: ...

    def comment(self, repo: str, number: int, text: str) -> None: ...

    def make_draft(self, repo: str, number: int) -> None: ...

    def make_ready(self, repo: str, number: int) -> None: ...

    def ci_state(self, repo: str, number: int) -> CiState: ...

    def list_ci_failures(self, repo: str, number: int) -> List[CiFailure]: ...

    def pull_state(self, repo: str, number: int) -> PullState: ...

    def branch_head(self, repo: str, branch: str) -> str: ...

    def active_repos(self, days: int) -> List[str]: ...

    def usual_reviewers(self, repo: str, count: int) -> List[str]: ...

    def request_review(self, repo: str, number: int, reviewers: List[str]) -> None: ...

    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]: ...

    def branch_pull(self, repo: str, branch: str) -> Optional[BranchPull]: ...

    def review_comments(self, repo: str, number: int) -> List[ReviewComment]: ...

    def resolved_comment_ids(self, repo: str, number: int) -> Set[str]: ...

    def reply_in_thread(self, repo: str, reply: ThreadReply) -> None: ...


class HostStyle(Protocol):
    """What differs per host beyond the API."""

    def pull_url(self, repo: str, number: int) -> str: ...

    def remote(self, repo: str) -> Remote: ...

    def identity(self) -> GitIdentity: ...

    def noun(self) -> str: ...


class GithubStyle:
    SITE = "https://github.com"

    def __init__(self, host: CodeHost) -> None:
        self._host = host

    def pull_url(self, repo: str, number: int) -> str:
        return f"{self.SITE}/{repo}/pull/{number}"

    def remote(self, repo: str) -> Remote:
        return Remote(repo, self.SITE, "x-access-token")

    def identity(self) -> GitIdentity:
        return GitIdentity(self._host.who_am_i())

    def noun(self) -> str:
        return "pull request"


class GitlabStyle:
    """G2: `url` is gitlab.com or the origin of a self-hosted GitLab; a repo is a project path."""

    def __init__(self, url: str, host: GitlabHost) -> None:
        self._url = url
        self._host = host

    def pull_url(self, repo: str, number: int) -> str:
        return f"{self._url}/{repo}/-/merge_requests/{number}"

    def remote(self, repo: str) -> Remote:
        return Remote(repo, self._url, "oauth2")

    def identity(self) -> GitIdentity:
        """G9: the commit email of the GitLab profile."""
        return GitIdentity(self._host.who_am_i(), self._host.commit_email())

    def noun(self) -> str:
        return "merge request"


__all__ = ["CodeHost", "GithubStyle", "GitlabStyle", "HostStyle"]
