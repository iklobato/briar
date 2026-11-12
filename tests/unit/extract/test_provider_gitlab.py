"""`GitlabProvider` (spec 008 PR 3a, G2, G4, G17, G18). No network: `FakeGitlabApi` answers
each REST path with a body shaped like the GitLab v4 docs.

Doc URLs modelled:
- Merge requests: https://docs.gitlab.com/api/merge_requests/
- Discussions (MR notes, resolved threads): https://docs.gitlab.com/api/discussions/
- Pipelines and jobs: https://docs.gitlab.com/api/pipelines/ and https://docs.gitlab.com/api/jobs/
- Commits: https://docs.gitlab.com/api/commits/
- Repository files and tree: https://docs.gitlab.com/api/repository_files/ and https://docs.gitlab.com/api/repositories/
- Search: https://docs.gitlab.com/api/search/
- Releases, protected branches, approval rules, environments, deployments: the pages of the same names under https://docs.gitlab.com/api/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pytest
import requests
from gitlab.exceptions import GitlabAuthenticationError, GitlabGetError

from briar.auth.check import LoginRejected, SourceUnreachable
from briar.extract._provider import BranchProtection, CiFailure, CiRun, CodeSearchHit, Commit, CommentKind, Deployment, Environment, PullRequest, Release, ReviewComment, TreeEntry
from briar.extract._providers import make_provider
from briar.extract._providers.gitlab import GitlabProvider
from tests.unit.fake_logins import FakeLogins

FAKE_TOKEN = "PLACEHOLDER-not-a-secret"
REPO = "acme/platform/tools/app"
PROJECT = "/projects/acme%2Fplatform%2Ftools%2Fapp"
IID = 4
MR = f"{PROJECT}/merge_requests/{IID}"
NOTE_COUNT = 3
LIMIT = 10
CHANGED_FILES = 12
DIFF_CAP = 1000
UNAUTHORIZED, FORBIDDEN, NOT_FOUND = 401, 403, 404
FIRST_NOTE, REPLY_NOTE, GENERAL_NOTE, SYSTEM_NOTE = 11, 12, 13, 14
LINE = 9
PIPELINE, OLDER_PIPELINE, JOB = 9, 8, 31
TAIL = 80
EXTRA_LINES = 5
SINCE_DAYS, MAX_COMMITS = 7, 5
TWO = 2
DEPLOYMENT = 3


@dataclass(frozen=True)
class FakeGitlabApi:
    """The `GitlabApi` surface: each path gives its answer (an exception is raised);
    `calls` records the path and query of every call, in order."""

    answers: Mapping[str, Any]
    calls: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)

    def get(self, path: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        found: Dict[str, Any] = self._answer(path, query)
        return found

    def pages(self, path: str, query: Optional[Dict[str, Any]] = None, cap: int = 0) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = self._answer(path, query)
        return found[:cap] if cap else found

    def text(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        found: str = self._answer(path, query)
        return found

    def _answer(self, path: str, query: Optional[Dict[str, Any]]) -> Any:
        self.calls.append((path, dict(query or {})))
        found = self.answers[path]
        if isinstance(found, BaseException):
            raise found
        return found


class Provider:
    """A `GitlabProvider` of company acme on a self-hosted GitLab, over a fake API."""

    def __init__(self, api: FakeGitlabApi) -> None:
        self._api = api

    @classmethod
    def of(cls, answers: Mapping[str, Any]) -> "Provider":
        return cls(FakeGitlabApi(answers))

    def provider(self) -> GitlabProvider:
        logins = FakeLogins({"gitlab": {"url": "https://gitlab.acme.test"}}, {"BRIAR_ACME_GITLAB_TOKEN": FAKE_TOKEN}).logins()
        return GitlabProvider(logins=logins, api=self._api)

    def calls(self) -> List[Tuple[str, Dict[str, Any]]]:
        return self._api.calls


class Bodies:
    """Answers of the GitLab API, as its docs show them."""

    def merge_request(self, extra: Mapping[str, Any]) -> Dict[str, Any]:
        body = {
            "iid": IID,
            "title": "MR four",
            "author": {"username": "ana"},
            "draft": False,
            "source_branch": "feat",
            "target_branch": "main",
            "user_notes_count": NOTE_COUNT,
            "created_at": "2026-11-01T10:00:00Z",
            "merged_at": None,
            "reviewers": [{"username": "bo"}],
            "assignees": [{"username": "ana"}],
            "description": "why",
        }
        return {**body, **extra}

    def note(self, number: int, body: str, extra: Mapping[str, Any]) -> Dict[str, Any]:
        note = {"id": number, "author": {"username": "bo"}, "body": body, "system": False, "created_at": f"c{number}", "updated_at": f"u{number}"}
        return {**note, **extra}


class TestLogin:
    def test_who_am_i_is_the_username(self) -> None:
        assert Provider.of({"/user": {"username": "octo"}}).provider().who_am_i() == "octo"

    def test_read_gets_the_project_by_its_full_path(self) -> None:
        fake = Provider.of({PROJECT: {"id": 1}})

        fake.provider().read(REPO)

        assert fake.calls() == [(PROJECT, {})]

    def test_a_refused_token_says_the_status_and_the_gitlab_message(self) -> None:
        refused = GitlabAuthenticationError(error_message="401 Unauthorized", response_code=UNAUTHORIZED)

        with pytest.raises(LoginRejected) as caught:
            Provider.of({"/user": refused}).provider().who_am_i()

        assert str(caught.value) == "HTTP 401: 401 Unauthorized"

    def test_no_connection_is_unreachable(self) -> None:
        with pytest.raises(SourceUnreachable) as caught:
            Provider.of({PROJECT: requests.ConnectionError("no route to host")}).provider().read(REPO)

        assert str(caught.value) == "no route to host"

    def test_the_registry_knows_gitlab(self) -> None:
        assert isinstance(make_provider("gitlab"), GitlabProvider)

    def test_clone_urls_use_the_login_url_and_oauth2(self) -> None:
        provider = Provider.of({}).provider()

        assert provider.clone_url("acme/platform", "app") == "https://gitlab.acme.test/acme/platform/app.git"
        assert provider.authed_clone_url("acme/platform", "app", FAKE_TOKEN) == f"https://oauth2:{FAKE_TOKEN}@gitlab.acme.test/acme/platform/app.git"


class TestMergeRequests:
    def test_open_and_merged_map_to_the_gitlab_states(self) -> None:
        fake = Provider.of({f"{PROJECT}/merge_requests": [Bodies().merge_request({"draft": True})]})

        pulls = fake.provider().list_pulls(REPO, state="open", max_count=LIMIT)
        fake.provider().list_pulls(REPO, state="merged", max_count=LIMIT)

        assert pulls == [PullRequest(IID, "MR four", "ana", True, "feat", "main", NOTE_COUNT, "2026-11-01T10:00:00Z", "", ["bo"], ["ana"], "why")]
        assert [query["state"] for _, query in fake.calls()] == ["opened", "merged"]

    def test_one_merge_request_counts_its_changed_files(self) -> None:
        answers = {MR: Bodies().merge_request({"changes_count": str(CHANGED_FILES), "merged_at": "2026-11-02T10:00:00Z"})}

        pull = Provider.of(answers).provider().get_pull(REPO, IID)

        assert (pull.changed_files, pull.merged_at) == (CHANGED_FILES, "2026-11-02T10:00:00Z")

    def test_a_capped_change_count_keeps_its_number(self) -> None:
        """GitLab answers "1000+" past its diff limit."""
        pull = Provider.of({MR: Bodies().merge_request({"changes_count": "1000+"})}).provider().get_pull(REPO, IID)

        assert pull.changed_files == DIFF_CAP

    def test_notes_inline_general_and_system(self) -> None:
        bodies = Bodies()
        position = {"resolved": True, "position": {"new_path": "a.py", "new_line": LINE}}
        inline = [bodies.note(FIRST_NOTE, "rename", position), bodies.note(REPLY_NOTE, "done", position)]
        discussions = [{"id": "d1", "notes": inline}, {"id": "d2", "notes": [bodies.note(GENERAL_NOTE, "lgtm", {})]}, {"id": "d3", "notes": [bodies.note(SYSTEM_NOTE, "added 1 commit", {"system": True})]}]

        comments = Provider.of({f"{MR}/discussions": discussions}).provider().list_pr_comments(REPO, IID)

        assert comments == [
            ReviewComment("11", "bo", "rename", "a.py", LINE, True, "c11", CommentKind.INLINE, updated_at="u11", thread="d1"),
            ReviewComment("12", "bo", "done", "a.py", LINE, True, "c12", CommentKind.INLINE, updated_at="u12", thread="d1"),
            ReviewComment("13", "bo", "lgtm", "", 0, False, "c13", CommentKind.CONVERSATION, updated_at="u13", thread="d2"),
        ]


class TestPipelines:
    @pytest.mark.parametrize(
        "status, expected",
        [
            ("success", ("completed", "success")),
            ("failed", ("completed", "failure")),
            ("canceled", ("completed", "cancelled")),
            ("skipped", ("completed", "skipped")),
            ("manual", ("completed", "action_required")),
            ("running", ("in_progress", "")),
            ("pending", ("queued", "")),
            ("created", ("queued", "")),
        ],
    )
    def test_the_status_in_the_words_ci_health_compares(self, status: str, expected: Tuple[str, str]) -> None:
        pipeline = {"id": PIPELINE, "name": None, "source": "push", "status": status, "ref": "main", "created_at": "c", "updated_at": "u"}

        runs = Provider.of({f"{PROJECT}/pipelines": [pipeline]}).provider().list_ci_runs(REPO, limit=LIMIT)

        assert runs == [CiRun("push", expected[0], expected[1], "main", "c", "u")]

    def test_failed_jobs_of_the_latest_merge_request_pipeline_with_a_log_tail(self) -> None:
        trace = "\n".join(f"line {number}" for number in range(TAIL + EXTRA_LINES))
        answers = {
            f"{MR}/pipelines": [{"id": PIPELINE}, {"id": OLDER_PIPELINE}],
            f"{PROJECT}/pipelines/{PIPELINE}/jobs": [{"id": JOB, "name": "pytest", "stage": "test", "web_url": "https://gitlab.acme.test/j/31"}],
            f"{PROJECT}/jobs/{JOB}/trace": trace,
        }
        fake = Provider.of(answers)

        failures = fake.provider().list_ci_failures(REPO, IID)

        assert failures == [CiFailure("test", "pytest", "test", "\n".join(trace.splitlines()[-TAIL:]), "https://gitlab.acme.test/j/31")]
        assert (f"{PROJECT}/pipelines/{PIPELINE}/jobs", {"scope[]": "failed"}) in fake.calls()

    def test_a_merge_request_with_no_pipeline_has_no_failure(self) -> None:
        assert Provider.of({f"{MR}/pipelines": []}).provider().list_ci_failures(REPO, IID) == []


class TestRepository:
    def test_commits_with_the_files_they_change(self) -> None:
        answers = {
            f"{PROJECT}/repository/commits": [{"id": "abc", "author_name": "Ana", "title": "Fix it", "created_at": "c"}],
            f"{PROJECT}/repository/commits/abc/diff": [{"new_path": "a.py"}, {"new_path": "b.py"}],
        }

        commits = Provider.of(answers).provider().list_recent_commits(REPO, since_days=SINCE_DAYS, max_count=MAX_COMMITS)

        assert commits == [Commit("abc", "Ana", "Fix it", "c", ["a.py", "b.py"])]

    def test_read_file_on_the_default_branch(self) -> None:
        raw = f"{PROJECT}/repository/files/docs%2FREADME.md/raw"
        fake = Provider.of({PROJECT: {"default_branch": "trunk"}, raw: "hello"})

        assert fake.provider().read_file(REPO, "docs/README.md") == "hello"
        assert fake.calls()[-1] == (raw, {"ref": "trunk"})

    def test_a_missing_file_reads_as_empty(self) -> None:
        missing = GitlabGetError(error_message="404 File Not Found", response_code=NOT_FOUND)

        assert Provider.of({PROJECT: {"default_branch": "main"}, f"{PROJECT}/repository/files/x.py/raw": missing}).provider().read_file(REPO, "x.py") == ""

    def test_tree_marks_files_and_folders(self) -> None:
        answers = {f"{PROJECT}/repository/tree": [{"path": "src", "type": "tree"}, {"path": "src/a.py", "type": "blob"}]}

        assert Provider.of(answers).provider().list_tree(REPO) == [TreeEntry("src", False), TreeEntry("src/a.py", True)]

    def test_search_counts_the_hits_per_file(self) -> None:
        answers = {f"{PROJECT}/search": [{"path": "a.py"}, {"path": "b.py"}, {"path": "a.py"}]}

        assert Provider.of(answers).provider().search_code(REPO, "TODO") == [CodeSearchHit("a.py", TWO), CodeSearchHit("b.py", 1)]

    def test_releases(self) -> None:
        answers = {f"{PROJECT}/releases": [{"tag_name": "v2", "name": "Two", "released_at": "r", "created_at": "c", "upcoming_release": True}]}

        assert Provider.of(answers).provider().list_releases(REPO) == [Release("v2", "Two", "r", True)]

    def test_default_branch(self) -> None:
        assert Provider.of({PROJECT: {"default_branch": "trunk"}}).provider().default_branch(REPO) == "trunk"


class TestGovernance:
    def test_a_protected_branch_with_approvals_and_a_pipeline_rule(self) -> None:
        answers = {
            PROJECT: {"default_branch": "main", "only_allow_merge_if_pipeline_succeeds": True},
            f"{PROJECT}/protected_branches/main": {"name": "main", "code_owner_approval_required": True},
            f"{PROJECT}/approval_rules": [{"approvals_required": 1}, {"approvals_required": TWO}],
        }

        assert Provider.of(answers).provider().get_branch_protection(REPO) == BranchProtection("main", True, TWO, True, False, True)

    def test_an_unprotected_branch(self) -> None:
        answers = {PROJECT: {"default_branch": "main"}, f"{PROJECT}/protected_branches/main": GitlabGetError(error_message="404 Not found", response_code=NOT_FOUND)}

        assert Provider.of(answers).provider().get_branch_protection(REPO) == BranchProtection("main", False)

    def test_approval_rules_of_a_free_plan_count_as_none(self) -> None:
        answers = {
            PROJECT: {"default_branch": "main"},
            f"{PROJECT}/protected_branches/main": {"name": "main"},
            f"{PROJECT}/approval_rules": GitlabGetError(error_message="403 Forbidden", response_code=FORBIDDEN),
        }

        assert Provider.of(answers).provider().get_branch_protection(REPO) == BranchProtection("main", True)

    def test_environments_and_deployments(self) -> None:
        answers = {
            f"{PROJECT}/environments": [{"name": "prod", "external_url": "https://app.acme.test"}],
            f"{PROJECT}/deployments": [{"id": DEPLOYMENT, "environment": {"name": "prod"}, "sha": "abc", "user": {"username": "ana"}, "created_at": "c"}],
        }
        provider = Provider.of(answers).provider()

        assert provider.list_environments(REPO) == [Environment("prod", 0, "https://app.acme.test")]
        assert provider.list_deployments(REPO, limit=LIMIT) == [Deployment("3", "prod", "abc", "ana", "c")]
