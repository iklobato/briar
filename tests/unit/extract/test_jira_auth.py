"""Jira auth strategies, one per `[auth.jira] method` (spec 005, L9, H5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import pytest

from briar.extract._trackers._jira_auth import JiraAuths, JiraCookieAuth, JiraTokenAuth
from tests.unit.fake_logins import FakeLogins

FAKE_TOKEN = "PLACEHOLDER-not-a-secret"


@dataclass(frozen=True)
class CookieCase:
    """A pasted cookie and a `user_agent` setting, and the headers they must give."""

    cookie: str = "tenant.session.token=fake"
    user_agent: str = ""
    expected: Dict[str, str] = field(default_factory=dict)


class TestPicksTheStrategyOfTheMethod:
    def test_token_is_the_default(self) -> None:
        login = FakeLogins({"jira": {"site": "acme.atlassian.net"}}).logins().jira()

        assert isinstance(JiraAuths.for_login(login), JiraTokenAuth)

    def test_cookie_when_the_method_says_so(self) -> None:
        login = FakeLogins({"jira": {"site": "acme.atlassian.net", "method": "cookie"}}).logins().jira()

        assert isinstance(JiraAuths.for_login(login), JiraCookieAuth)


class TestToken:
    def test_sends_the_email_and_the_api_token_as_basic_auth(self) -> None:
        environ = {"BRIAR_ACME_JIRA_EMAIL": "bot@example.test", "BRIAR_ACME_JIRA_TOKEN": FAKE_TOKEN}
        login = FakeLogins({"jira": {"site": "acme.atlassian.net"}}, environ).logins().jira()

        assert JiraTokenAuth().configure(login) == {"username": "bot@example.test", "password": FAKE_TOKEN}


class TestCookie:
    @pytest.mark.parametrize(
        "case",
        [
            CookieCase(expected={"Cookie": "tenant.session.token=fake", "Origin": "https://acme.atlassian.net", "Referer": "https://acme.atlassian.net/"}),
            CookieCase(user_agent="agent-from-config", expected={"User-Agent": "agent-from-config"}),
            CookieCase(cookie="a=1; atlassian.xsrf.token=fake-xsrf", expected={"X-Atlassian-Token": "no-check"}),
        ],
        ids=["cookie and origin", "user agent from config", "xsrf cookie"],
    )
    def test_sends_the_pasted_cookie_with_the_browser_headers(self, case: CookieCase) -> None:
        auth = {"jira": {"site": "acme.atlassian.net", "method": "cookie", "user_agent": case.user_agent}}
        login = FakeLogins(auth, {"BRIAR_ACME_JIRA_COOKIE": case.cookie}).logins().jira()

        headers = JiraCookieAuth().configure(login)["session"].headers

        assert {name: headers[name] for name in case.expected} == case.expected

    def test_without_user_agent_sends_the_browser_default(self) -> None:
        login = FakeLogins({"jira": {"site": "acme.atlassian.net", "method": "cookie"}}, {"BRIAR_ACME_JIRA_COOKIE": "a=1"}).logins().jira()

        headers = JiraCookieAuth().configure(login)["session"].headers

        assert headers["User-Agent"].startswith("Mozilla/5.0")
        assert "X-Atlassian-Token" not in headers
