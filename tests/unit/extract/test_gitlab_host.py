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


class TestMergeRequestState:
    @pytest.mark.parametrize(
        "pipeline, expected",
        [
            (None, CiState.NONE),
            ({"status": "success"}, CiState.PASSED),
            ({"status": "skipped"}, CiState.PASSED),
            ({"status": "manual"}, CiState.PASSED),
            ({"status": "failed"}, CiState.FAILED),
            ({"status": "canceled"}, CiState.FAILED),
            ({"status": "running"}, CiState.PENDING),
            ({"status": "created"}, CiState.PENDING),
        ],
    )
    def test_the_head_pipeline_gives_the_ci_state(self, pipeline: Any, expected: CiState) -> None:
        """G8: canceled needs a look; skipped or stopped at a manual job is no code error."""
        assert Host.of({MR: {"head_pipeline": pipeline}}).host().ci_state(REPO, IID) is expected

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
