"""`GitlabHost`: what `briar serve` writes and reads on GitLab (spec 008 PR 3b, G6 to G9).
No network: `FakeGitlabApi` answers each REST path like the GitLab v4 docs.

Doc URLs modelled:
- Merge requests (create, update, single, list by source branch): https://docs.gitlab.com/api/merge_requests/
- Notes and discussions: https://docs.gitlab.com/api/notes/ and https://docs.gitlab.com/api/discussions/
- Project members (inherited): https://docs.gitlab.com/api/members/#get-a-member-of-a-group-or-project-including-inherited-and-invited-members
- Branches: https://docs.gitlab.com/api/branches/
- Token of the request: https://docs.gitlab.com/api/personal_access_tokens/#get-single-personal-access-token
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

import pytest
from gitlab.exceptions import GitlabHttpError

from briar.auth.check import LoginRejected
from briar.extract._provider import BranchPull, CiState, CommentKind, PullRequestDraft, PullState, ThreadReply
from briar.extract._providers.gitlab_host import GitlabHost
from tests.unit.extract.gitlab_fakes import FakeGitlabApi
from tests.unit.fake_logins import FakeLogins

FAKE_TOKEN = "PLACEHOLDER-not-a-secret"
REPO = "acme/platform/app"
PROJECT = "/projects/acme%2Fplatform%2Fapp"
IID = 7
MR = f"{PROJECT}/merge_requests/{IID}"
DEVELOPER, REPORTER = 30, 20
BOB, CY, BOT = 11, 12, 13
NOTE, REPLY, BOT_NOTE, SYSTEM = 101, 102, 103, 104
LINE = 4
ACTIVE_DAYS = 90
FORBIDDEN, NOT_FOUND = 403, 404


class Host:
    """A `GitlabHost` of company acme over a fake API."""

    def __init__(self, api: FakeGitlabApi) -> None:
        self._api = api

    @classmethod
    def of(cls, answers: Mapping[str, Any]) -> "Host":
        return cls(FakeGitlabApi(answers))

    def host(self) -> GitlabHost:
        logins = FakeLogins({}, {"BRIAR_ACME_GITLAB_TOKEN": FAKE_TOKEN}).logins()
        return GitlabHost(logins.gitlab(), self._api)

    def calls(self) -> List[Any]:
        return self._api.calls


class Notes:
    def review(self) -> Dict[str, Any]:
        """An inline note of a developer, a reply of a reporter, a bot note and a system note."""
        bob, cy, bot = {"id": BOB, "username": "bob"}, {"id": CY, "username": "cy"}, {"id": BOT, "username": "project_9_bot_ab12"}
        inline = self.note(NOTE, bob, {"position": {"new_path": "a.py", "new_line": LINE}})
        discussions = [
            {"id": "d1", "notes": [inline, self.note(REPLY, cy, {})]},
            {"id": "d2", "notes": [self.note(BOT_NOTE, bot, {}), self.note(SYSTEM, bob, {"system": True})]},
        ]
        return {
            f"{MR}/discussions": discussions,
            f"{PROJECT}/members/all/{BOB}": {"access_level": DEVELOPER},
            f"{PROJECT}/members/all/{CY}": {"access_level": REPORTER},
            f"{PROJECT}/members/all/{BOT}": GitlabHttpError(error_message="404 Not found", response_code=NOT_FOUND),
        }

    def note(self, number: int, author: Mapping[str, Any], extra: Mapping[str, Any]) -> Dict[str, Any]:
        note = {"id": number, "author": dict(author), "body": f"note {number}", "system": False, "created_at": "c", "updated_at": "u"}
        return {**note, **extra}


class TestMergeRequestWrites:
    def test_a_draft_merge_request_is_opened_and_gives_its_number(self) -> None:
        fake = Host.of({f"POST {PROJECT}/merge_requests": {"iid": IID}})

        number = fake.host().open_pull(REPO, PullRequestDraft("briar/ENG-1", "main", "ENG-1: fix it", "the plan", draft=True))
        fake.host().open_pull(REPO, PullRequestDraft("briar/ENG-2", "main", "ENG-2: fix it", "the plan"))

        assert number == IID
        assert fake.calls() == [
            (f"POST {PROJECT}/merge_requests", {"source_branch": "briar/ENG-1", "target_branch": "main", "title": "Draft: ENG-1: fix it", "description": "the plan"}),
            (f"POST {PROJECT}/merge_requests", {"source_branch": "briar/ENG-2", "target_branch": "main", "title": "ENG-2: fix it", "description": "the plan"}),
        ]

    def test_a_comment_is_a_note_on_the_merge_request(self) -> None:
        fake = Host.of({f"POST {MR}/notes": {"id": 1}})

        fake.host().comment(REPO, IID, "hello")

        assert fake.calls() == [(f"POST {MR}/notes", {"body": "hello"})]

    @pytest.mark.parametrize("title, ready, draft", [("Draft: fix it", "fix it", "Draft: fix it"), ("fix it", "fix it", "Draft: fix it"), ("[Draft] fix it", "fix it", "Draft: fix it")])
    def test_ready_and_draft_change_the_title_prefix(self, title: str, ready: str, draft: str) -> None:
        fake = Host.of({MR: {"title": title}, f"PUT {MR}": {}})

        fake.host().make_ready(REPO, IID)
        fake.host().make_draft(REPO, IID)

        assert [body for path, body in fake.calls() if path.startswith("PUT")] == [{"title": ready}, {"title": draft}]

    def test_a_reply_goes_into_its_discussion(self) -> None:
        fake = Host.of({f"POST {MR}/discussions/d1/notes": {"id": 1}})

        fake.host().reply_in_thread(REPO, ThreadReply(IID, "d1", "done"))

        assert fake.calls() == [(f"POST {MR}/discussions/d1/notes", {"body": "done"})]

    def test_a_refused_write_says_the_status(self) -> None:
        refused = GitlabHttpError(error_message="403 Forbidden", response_code=FORBIDDEN)

        with pytest.raises(LoginRejected) as caught:
            Host.of({f"POST {MR}/notes": refused}).host().comment(REPO, IID, "hello")

        assert str(caught.value) == "HTTP 403: 403 Forbidden"


class TestActiveRepos:
    def test_the_projects_the_token_can_push_to_with_recent_activity(self) -> None:
        """Decision C2 of the autonomous serve: Developer or more, not archived, active lately."""
        on = {"merge_requests_access_level": "enabled", "repository_access_level": "enabled"}
        rows = [
            {"path_with_namespace": "acme/platform/app", **on},
            {"path_with_namespace": "acme/infra", **on},
            # A real project had merge requests turned off: listing its MRs answered 403 and
            # stopped every ticket choice (2026-11-13).
            {"path_with_namespace": "acme/vendored", **on, "merge_requests_access_level": "disabled"},
            {"path_with_namespace": "acme/wiki-only", **on, "repository_access_level": "disabled"},
        ]
        host = Host.of({"/projects": rows})

        found = host.host().active_repos(ACTIVE_DAYS)

        path, query = host.calls()[-1]
        assert (found, path) == (["acme/platform/app", "acme/infra"], "/projects")
        assert {key: query[key] for key in ("membership", "min_access_level", "archived")} == {"membership": True, "min_access_level": DEVELOPER, "archived": False}
        assert query["last_activity_after"].endswith("T00:00:00Z")


class TestMergeRequestState:
    @pytest.mark.parametrize(
        "status, expected",
        [
            ("success", CiState.PASSED),
            ("skipped", CiState.PASSED),
            ("manual", CiState.PASSED),
            ("failed", CiState.FAILED),
            ("canceled", CiState.FAILED),
            ("running", CiState.PENDING),
            ("created", CiState.PENDING),
        ],
    )
    def test_the_head_pipeline_gives_the_ci_state(self, status: str, expected: CiState) -> None:
        """G8: canceled needs a look; skipped or stopped at a manual job is no code error."""
        answers = {MR: {"sha": "new", "head_pipeline": {"status": status, "sha": "new"}}}

        assert Host.of(answers).host().ci_state(REPO, IID) is expected

    def test_the_pipeline_of_an_older_commit_is_pending(self) -> None:
        """Real review round, 2026-11-12: right after the push the MR still showed the passed
        pipeline of the old commit, and serve called the round done before the new CI ran."""
        answers = {MR: {"sha": "new", "head_pipeline": {"status": "success", "sha": "old"}}}

        assert Host.of(answers).host().ci_state(REPO, IID) is CiState.PENDING

    def test_the_pipeline_of_an_older_commit_is_pending_while_the_mr_lags(self) -> None:
        """Real review round 2, 2026-11-12: right after the push the MR sha itself was still
        the old commit (GitLab refreshes an MR after a push), so the old pipeline matched it.
        The source branch moves at once: its head is the commit to wait for."""
        answers = {
            MR: {"sha": "old", "source_branch": "briar/ENG-1", "head_pipeline": {"status": "success", "sha": "old"}},
            f"{PROJECT}/repository/branches/briar%2FENG-1": {"commit": {"id": "new"}},
        }

        assert Host.of(answers).host().ci_state(REPO, IID) is CiState.PENDING

    def test_a_merged_results_pipeline_of_the_head_gives_the_ci_state(self) -> None:
        """Real run, 2026-11-13: a project with merged results pipelines runs CI on a merge
        commit of the head and the target (`refs/merge-requests/<iid>/merge`); its sha is never
        the head, so serve read a failed pipeline as pending until `ci_wait_minutes`."""
        merged = {"status": "failed", "sha": "merge", "ref": f"refs/merge-requests/{IID}/merge"}
        answers = {MR: {"sha": "new", "head_pipeline": merged}, f"{PROJECT}/repository/commits/merge": {"parent_ids": ["main", "new"]}}

        assert Host.of(answers).host().ci_state(REPO, IID) is CiState.FAILED

    def test_a_merged_results_pipeline_of_an_older_head_is_pending(self) -> None:
        merged = {"status": "success", "sha": "merge", "ref": f"refs/merge-requests/{IID}/merge"}
        answers = {MR: {"sha": "new", "head_pipeline": merged}, f"{PROJECT}/repository/commits/merge": {"parent_ids": ["main", "old"]}}

        assert Host.of(answers).host().ci_state(REPO, IID) is CiState.PENDING

    def test_no_pipeline_yet_in_a_repo_with_ci_is_pending(self) -> None:
        answers = {MR: {"sha": "new", "head_pipeline": None}, PROJECT: {"ci_config_path": ""}, f"{PROJECT}/repository/files/.gitlab-ci.yml": {"file_name": ".gitlab-ci.yml"}}

        assert Host.of(answers).host().ci_state(REPO, IID) is CiState.PENDING

    def test_no_pipeline_in_a_repo_without_ci_is_none(self) -> None:
        missing = GitlabHttpError(error_message="404 File Not Found", response_code=NOT_FOUND)
        answers = {MR: {"sha": "new", "head_pipeline": None}, PROJECT: {"ci_config_path": "ci/main.yml"}, f"{PROJECT}/repository/files/ci%2Fmain.yml": missing}

        assert Host.of(answers).host().ci_state(REPO, IID) is CiState.NONE

    def test_pull_state(self) -> None:
        answers = {MR: {"state": "opened", "has_conflicts": True, "target_branch": "main", "draft": True, "diff_refs": {"start_sha": "abc"}}}

        assert Host.of(answers).host().pull_state(REPO, IID) == PullState(True, False, True, "main", True, "abc")

    def test_a_merged_merge_request(self) -> None:
        state = Host.of({MR: {"state": "merged", "has_conflicts": False, "target_branch": "main", "draft": False}}).host().pull_state(REPO, IID)

        assert (state.open, state.merged, state.base_sha) == (False, True, "")

    def test_branch_head(self) -> None:
        assert Host.of({f"{PROJECT}/repository/branches/briar%2FENG-1": {"commit": {"id": "abc"}}}).host().branch_head(REPO, "briar/ENG-1") == "abc"

    def test_the_newest_merge_request_of_a_branch(self) -> None:
        fake = Host.of({f"{PROJECT}/merge_requests": [{"iid": IID, "state": "merged", "draft": False}]})

        found = fake.host().branch_pull(REPO, "briar/ENG-1")

        assert found == BranchPull(IID, False, True, False)
        assert fake.calls()[0][1]["source_branch"] == "briar/ENG-1"

    def test_no_merge_request_for_a_branch(self) -> None:
        assert Host.of({f"{PROJECT}/merge_requests": []}).host().branch_pull(REPO, "briar/ENG-1") is None


class TestReviewComments:
    def test_members_with_push_get_the_write_association_and_bots_are_marked(self) -> None:
        """G7: Developer or above, as "can push" on GitHub; G11: a project or group bot user."""
        comments = Host.of(Notes().review()).host().review_comments(REPO, IID)

        assert [(comment.id, comment.kind, comment.association, comment.bot, comment.thread, comment.file_path) for comment in comments] == [
            ("101", CommentKind.INLINE, "MEMBER", False, "d1", "a.py"),
            ("102", CommentKind.CONVERSATION, "", False, "d1", ""),
            ("103", CommentKind.CONVERSATION, "", True, "d2", ""),
        ]

    def test_the_notes_of_resolved_discussions(self) -> None:
        notes = Notes()
        discussions = [{"id": "d1", "notes": [notes.note(NOTE, {"id": BOB}, {"resolvable": True, "resolved": True})]}, {"id": "d2", "notes": [notes.note(REPLY, {"id": BOB}, {"resolvable": True, "resolved": False})]}]

        assert Host.of({f"{MR}/discussions": discussions}).host().resolved_comment_ids(REPO, IID) == {"101"}


class TestWriteAccess:
    def test_a_developer_with_an_api_token_can_write(self) -> None:
        answers = {PROJECT: {"permissions": {"project_access": None, "group_access": {"access_level": DEVELOPER}}}, "/personal_access_tokens/self": {"scopes": ["api", "write_repository"]}}

        Host.of(answers).host().can_write(REPO)

    def test_the_api_scope_alone_can_write(self) -> None:
        """Real check, 2026-11-12: a token with `api` and no `write_repository` was refused.
        `api` covers the repository over Git HTTP too (https://docs.gitlab.com/user/profile/personal_access_tokens/#personal-access-token-scopes)."""
        answers = {PROJECT: {"permissions": {"project_access": {"access_level": DEVELOPER}}}, "/personal_access_tokens/self": {"scopes": ["read_api", "api"]}}

        Host.of(answers).host().can_write(REPO)

    def test_a_reporter_cannot_write(self) -> None:
        answers = {PROJECT: {"permissions": {"project_access": {"access_level": REPORTER}, "group_access": None}}, "/personal_access_tokens/self": {"scopes": ["api"]}}

        with pytest.raises(LoginRejected) as caught:
            Host.of(answers).host().can_write(REPO)

        assert str(caught.value) == "the login is not a Developer of the project: give it the Developer role or above"

    def test_a_read_api_token_cannot_write(self) -> None:
        answers = {PROJECT: {"permissions": {"project_access": {"access_level": DEVELOPER}}}, "/personal_access_tokens/self": {"scopes": ["read_api"]}}

        with pytest.raises(LoginRejected) as caught:
            Host.of(answers).host().can_write(REPO)

        assert str(caught.value) == "the token cannot write: give it the api scope"


class TestIdentity:
    def test_the_commit_email_of_the_profile(self) -> None:
        assert Host.of({"/user": {"username": "octo", "commit_email": "octo@acme.test", "email": "o@acme.test"}}).host().commit_email() == "octo@acme.test"

    def test_the_main_email_when_no_commit_email(self) -> None:
        assert Host.of({"/user": {"username": "octo", "commit_email": None, "email": "o@acme.test"}}).host().commit_email() == "o@acme.test"
