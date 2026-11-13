"""Which meetings of a Fireflies key belong to the company (decisions F1 to F12 in
`docs/decisions/2026-11-12-fireflies-company-filter.md`). A real personal account showed one
participant email per meeting (the owner), so title words, email domains and member speakers
are the signals; any one of them keeps a meeting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence
from urllib.parse import parse_qs

import httpx
import pytest
from gitlab.exceptions import GitlabHttpError

from briar.auth.check import LoginRejected
from briar.auth.config import AuthBlock, SlackAuth, SlackMethod
from briar.auth.logins import CompanyLogins, LoginScope, SlackLogin
from briar.extract._meeting import Meeting
from briar.extract._meetings.company_filter import CompanyMembers, MeetingFilter, MembersUnreadable, PersonName
from briar.extract._providers.gitlab import GitlabMembers
from briar.extract._trackers.jira import JiraMembers
from briar.serve.slack import SlackApi, SlackMembers
from tests.unit.extract.test_provider_gitlab import FakeGitlabApi

HTTP_OK = 200
FORBIDDEN = 403
# Two GitLab group ids of the token.
GROUP = 7
SUBGROUP = 9


DURATION_SEC = 60


class Meetings:
    def one(self, title: str = "abc-defg-hij", attendees: Sequence[str] = (), speakers: Sequence[str] = ()) -> Meeting:
        return Meeting("m1", title, "2026-11-12T10:00:00+00:00", DURATION_SEC, "owner@gmail.com", list(attendees), speakers=list(speakers))


@dataclass(frozen=True)
class FakeMembers:
    """A member source: its names, or the error its client gave."""

    source: str
    found: List[str] = field(default_factory=list)
    error: str = ""

    def names(self) -> List[str]:
        if self.error:
            raise LoginRejected(self.error)
        return self.found


class TestMeetingFilter:
    @pytest.mark.parametrize(
        "meeting",
        [
            Meetings().one(title="AI Infra Daily Sync"),
            Meetings().one(attendees=["Ana@Acme.io"]),
            Meetings().one(speakers=["Jose  Muller"]),
        ],
        ids=["title word", "email domain", "member speaker"],
    )
    def test_any_one_signal_keeps_the_meeting(self, meeting: Meeting) -> None:
        rules = MeetingFilter(("ai infra",), ("@acme.io",), frozenset({PersonName("José Müller").key()}))

        assert rules.keeps(meeting) is True

    def test_a_meeting_with_no_signal_is_dropped_when_a_filter_is_set(self) -> None:
        """A meet-code title, the owner only, an unknown speaker."""
        rules = MeetingFilter(("ai infra",), ("@acme.io",), frozenset({"bo wang"}))

        assert rules.keeps(Meetings().one(attendees=["owner@gmail.com"], speakers=["Someone Else"])) is False

    def test_an_exact_email_matches_only_that_person(self) -> None:
        rules = MeetingFilter((), ("ana@acme.io",), frozenset())

        assert (rules.keeps(Meetings().one(attendees=["ANA@acme.io"])), rules.keeps(Meetings().one(attendees=["bob@acme.io"]))) == (True, False)

    def test_the_organizer_email_counts_for_a_domain(self) -> None:
        assert MeetingFilter((), ("@gmail.com",), frozenset()).keeps(Meetings().one()) is True

    def test_no_filter_keeps_every_meeting(self) -> None:
        """F10b: no title words, no allowed emails, no member names."""
        assert MeetingFilter((), (), frozenset()).keeps(Meetings().one()) is True


class TestPersonName:
    def test_case_accents_and_spaces_do_not_count(self) -> None:
        assert PersonName("  Astrid  Ariza Gutiérrez ").key() == PersonName("astrid ariza gutierrez").key() == "astrid ariza gutierrez"


@dataclass(frozen=True)
class FakeJiraUsers:
    """The `users_get_all` call of the atlassian client: pages of `users/search`, then []."""

    pages: List[List[Dict[str, Any]]]
    starts: List[int] = field(default_factory=list)

    def users_get_all(self, start: int, limit: int) -> List[Dict[str, Any]]:
        self.starts.append(start)
        return self.pages[len(self.starts) - 1] if len(self.starts) <= len(self.pages) else []

    def get_all_assignable_users_for_project(self, project_key: str, start: int, limit: int) -> List[Dict[str, Any]]:
        """`user/assignable/search`: what a plain account can read (a real run: `users/search`
        answered 403 `error.no-permission`)."""
        return self.users_get_all(start, limit) if project_key == "ENG" else []


class SlackUsers:
    """The fake Web API of `users.list`: two pages joined by a cursor."""

    def __init__(self, pages: List[Dict[str, Any]]) -> None:
        self.forms: List[Dict[str, List[str]]] = []
        self._pages = pages

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.forms.append(parse_qs(request.content.decode()))
        return httpx.Response(HTTP_OK, json={"ok": True, **self._pages[len(self.forms) - 1]})


class TestMemberSources:
    def test_jira_gives_the_active_people_of_the_site_page_by_page(self) -> None:
        person, app, gone = {"accountType": "atlassian", "active": True}, {"accountType": "app", "active": True}, {"accountType": "atlassian", "active": False}
        pages = [[{**person, "displayName": "Bo Wang"}, {**app, "displayName": "Automation"}], [{**gone, "displayName": "Old One"}]]
        client = FakeJiraUsers(pages)

        assert (JiraMembers(client, ()).names(), client.starts) == (["Bo Wang"], [0, len(pages[0]), len(pages[0]) + len(pages[1])])

    def test_jira_with_projects_gives_the_people_who_can_work_on_them(self) -> None:
        person = {"accountType": "atlassian", "active": True}
        client = FakeJiraUsers([[{**person, "displayName": "Bo Wang"}]])

        assert JiraMembers(client, ("ENG", "OPS")).names() == ["Bo Wang"]

    def test_gitlab_gives_the_active_members_of_every_group_of_the_token(self) -> None:
        api = FakeGitlabApi(
            {
                "/groups": [{"id": GROUP}, {"id": SUBGROUP}],
                f"/groups/{GROUP}/members/all": [{"name": "Bo Wang", "state": "active"}, {"name": "Blocked", "state": "blocked"}],
                f"/groups/{SUBGROUP}/members/all": [{"name": "Ana Lima", "state": "active"}],
            }
        )

        assert GitlabMembers(api).names() == ["Bo Wang", "Ana Lima"]
        assert api.calls[0] == ("/groups", {"min_access_level": GitlabMembers.GUEST})

    def test_a_gitlab_error_is_a_login_error_with_its_status(self) -> None:
        api = FakeGitlabApi({"/groups": GitlabHttpError("403 Forbidden", response_code=FORBIDDEN)})

        with pytest.raises(LoginRejected, match=r"^HTTP 403"):
            GitlabMembers(api).names()

    def test_slack_gives_the_people_of_the_workspace_page_by_page(self) -> None:
        bot, gone = {"is_bot": True, "real_name": "Deploy Bot"}, {"deleted": True, "real_name": "Old One"}
        first = {"members": [{"real_name": "Bo Wang"}, bot], "response_metadata": {"next_cursor": "c2"}}
        fake = SlackUsers([first, {"members": [gone, {"id": "USLACKBOT", "real_name": "Slackbot"}, {"profile": {"real_name": "Ana Lima"}}]}])
        api = SlackApi(SlackLogins().login(), httpx.MockTransport(fake.answer), lambda seconds: None)

        assert SlackMembers(api).names() == ["Bo Wang", "Ana Lima"]
        assert [form.get("cursor") for form in fake.forms] == [None, ["c2"]]


class SlackLogins:
    def login(self) -> SlackLogin:
        block = AuthBlock(slack=SlackAuth(method=SlackMethod.SESSION, workspace="acme"))
        environ = {"BRIAR_ACME_SLACK_TOKEN": "xoxc-PLACEHOLDER-not-a-secret", "BRIAR_ACME_SLACK_COOKIE": "PLACEHOLDER-cookie"}
        return CompanyLogins(LoginScope("acme"), block, environ).slack()


class TestCompanyMembers:
    def test_the_names_of_every_source_join(self) -> None:
        members = CompanyMembers([FakeMembers("jira", ["Bo Wang"]), FakeMembers("slack", ["José Müller", ""])])

        assert members.keys() == frozenset({"bo wang", "jose muller"})

    def test_a_source_that_fails_names_itself_and_the_reason(self) -> None:
        """F6: the meetings step stops for that run."""
        members = CompanyMembers([FakeMembers("jira", ["Bo Wang"]), FakeMembers("gitlab", error="HTTP 403: Forbidden")])

        with pytest.raises(MembersUnreadable, match=r"^the members of gitlab could not be read: HTTP 403: Forbidden$"):
            members.keys()
