"""One login per source per company (decisions G5, G7, G8, G25, H1, H2, H7).

The environment is a plain dict: the logins read it the way they read
`os.environ` after the startup bootstrap loaded `secrets.env`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

from briar.auth import MockPromptIO
from briar.auth.config import AuthTable
from briar.auth.logins import CompanyLogins, LoginScope
from briar.errors import CliError

FAKE_TOKEN = "PLACEHOLDER-not-a-secret"


@dataclass(frozen=True)
class LegacyCase:
    """A 1.x env var set while the 2.0 login is missing, and the start of the stop message."""

    source: str = ""
    environ: Dict[str, str] = field(default_factory=dict)
    message: str = ""


@dataclass(frozen=True)
class ClientCase:
    """A Bitbucket method, its secrets, and the client keyword names it gives."""

    method: str = ""
    environ: Dict[str, str] = field(default_factory=dict)
    keys: List[str] = field(default_factory=list)


class LoginsFor:
    """Builds the logins of company `acme-co` from an `[auth]` table and an environment."""

    def __init__(self, auth: Dict[str, Any], environ: Dict[str, str]) -> None:
        self._auth = auth
        self._environ = environ

    def logins(self) -> CompanyLogins:
        block = AuthTable({"auth": self._auth}, ".briar.toml").block()
        return CompanyLogins(LoginScope("acme-co"), block, self._environ)


class TestNames:
    def test_secret_names_carry_the_company_and_the_source(self) -> None:
        logins = LoginsFor({}, {}).logins()

        assert logins.jira().names() == ["BRIAR_ACME_CO_JIRA_EMAIL", "BRIAR_ACME_CO_JIRA_TOKEN"]
        assert logins.github().names() == ["BRIAR_ACME_CO_GITHUB_TOKEN"]
        assert logins.linear().names() == ["BRIAR_ACME_CO_LINEAR_API_KEY"]
        assert logins.fireflies().names() == ["BRIAR_ACME_CO_FIREFLIES_API_KEY"]

    def test_the_method_picks_the_names(self) -> None:
        logins = LoginsFor({"jira": {"method": "cookie"}, "bitbucket": {"method": "access-token"}}, {}).logins()

        assert logins.jira().names() == ["BRIAR_ACME_CO_JIRA_COOKIE"]
        assert logins.bitbucket().names() == ["BRIAR_ACME_CO_BITBUCKET_ACCESS_TOKEN"]

    def test_no_company_stops(self) -> None:
        logins = CompanyLogins(LoginScope(""), AuthTable({}, ".briar.toml").block(), {})

        with pytest.raises(CliError) as caught:
            logins.jira().names()

        assert str(caught.value) == "no company: pass --company or set `company` in .briar.toml"


class TestReady:
    def test_nothing_set_is_not_ready(self) -> None:
        assert LoginsFor({"jira": {"site": "acme.atlassian.net"}}, {}).logins().jira().ready() is False

    def test_secrets_and_site_are_ready(self) -> None:
        environ = {"BRIAR_ACME_CO_JIRA_EMAIL": "bot@example.test", "BRIAR_ACME_CO_JIRA_TOKEN": FAKE_TOKEN}

        login = LoginsFor({"jira": {"site": "https://acme.atlassian.net/"}}, environ).logins().jira()

        assert login.ready() is True
        assert login.site_url() == "https://acme.atlassian.net"

    def test_secrets_without_the_site_stop(self) -> None:
        """H1: briar never writes the config; it says what to add where."""
        environ = {"BRIAR_ACME_CO_JIRA_EMAIL": "bot@example.test", "BRIAR_ACME_CO_JIRA_TOKEN": FAKE_TOKEN}

        with pytest.raises(CliError) as caught:
            LoginsFor({}, environ).logins().jira().ready()

        assert str(caught.value) == "set `site` under `auth.jira` in .briar.toml (briar jira login for acme-co)"


class TestLegacyNames:
    @pytest.mark.parametrize(
        "case",
        [
            LegacyCase("jira", {"JIRA_ACME_CO_TOKEN": FAKE_TOKEN}, "`JIRA_ACME_CO_TOKEN` is a 1.x name: use `BRIAR_ACME_CO_JIRA_TOKEN`"),
            LegacyCase("jira", {"JIRA_ACME_CO_URL": "https://acme.atlassian.net"}, "`JIRA_ACME_CO_URL` is a 1.x name: use `site` under `[auth.jira]`"),
            LegacyCase(
                "linear",
                {"LINEAR_ACME_CO_TOKEN": FAKE_TOKEN},
                '`LINEAR_ACME_CO_TOKEN` is a 1.x name: use `BRIAR_ACME_CO_LINEAR_API_KEY` with `method = "api-key"`',
            ),
            LegacyCase(
                "fireflies", {"FIREFLIES_ACME_CO_API_KEY": FAKE_TOKEN}, "`FIREFLIES_ACME_CO_API_KEY` is a 1.x name: use `BRIAR_ACME_CO_FIREFLIES_API_KEY`"
            ),
            LegacyCase(
                "bitbucket",
                {"BITBUCKET_ACME_CO_APP_PASSWORD": FAKE_TOKEN},
                "`BITBUCKET_ACME_CO_APP_PASSWORD` is a 1.x name: use `BRIAR_ACME_CO_BITBUCKET_TOKEN` (an Atlassian API token)",
            ),
            LegacyCase("github", {"GITHUB_TOKEN": FAKE_TOKEN}, '`GITHUB_TOKEN` is a 1.x name: use `BRIAR_ACME_CO_GITHUB_TOKEN` with `method = "token"`'),
        ],
        ids=["jira token", "jira url", "linear", "fireflies", "bitbucket", "github"],
    )
    def test_a_1x_name_with_no_2x_login_stops_with_the_new_place(self, case: LegacyCase) -> None:
        with pytest.raises(CliError) as caught:
            LoginsFor({}, case.environ).logins().source(case.source).ready()

        assert str(caught.value) == f"{case.message}. Or run `briar auth login {case.source} --company acme-co`"

    def test_a_1x_name_next_to_the_2x_login_is_ignored(self) -> None:
        environ = {"GITHUB_TOKEN": "fake-1x", "BRIAR_ACME_CO_GITHUB_TOKEN": FAKE_TOKEN}

        login = LoginsFor({}, environ).logins().github()

        assert login.ready() is True
        assert login.token() == FAKE_TOKEN


class TestLogin:
    def test_asks_each_field_and_returns_them_by_name(self) -> None:
        prompt = MockPromptIO(answers=["bot@example.test", f"  {FAKE_TOKEN}  "])

        entries = LoginsFor({"jira": {"site": "acme.atlassian.net"}}, {}).logins().jira().login(prompt)

        assert entries == {"BRIAR_ACME_CO_JIRA_EMAIL": "bot@example.test", "BRIAR_ACME_CO_JIRA_TOKEN": FAKE_TOKEN}
        assert [secret for _, secret in prompt.prompts] == [False, True]

    def test_an_empty_answer_stops(self) -> None:
        with pytest.raises(CliError) as caught:
            LoginsFor({}, {}).logins().linear().login(MockPromptIO(answers=[""]))

        assert str(caught.value) == "linear login: nothing entered for BRIAR_ACME_CO_LINEAR_API_KEY"

    def test_a_missing_site_stops_before_asking(self) -> None:
        prompt = MockPromptIO(answers=[])

        with pytest.raises(CliError, match=r"set `site` under `auth.jira`"):
            LoginsFor({}, {}).logins().jira().login(prompt)

        assert prompt.prompts == []


class TestBitbucketClient:
    @pytest.mark.parametrize(
        "case",
        [
            ClientCase("token", {"BRIAR_ACME_CO_BITBUCKET_EMAIL": "bot@example.test", "BRIAR_ACME_CO_BITBUCKET_TOKEN": FAKE_TOKEN}, ["password", "username"]),
            ClientCase("access-token", {"BRIAR_ACME_CO_BITBUCKET_ACCESS_TOKEN": FAKE_TOKEN}, ["token"]),
            ClientCase("cookie", {"BRIAR_ACME_CO_BITBUCKET_COOKIE": "session=fake"}, ["session"]),
        ],
        ids=["token", "access-token", "cookie"],
    )
    def test_each_method_gives_its_client_arguments(self, case: ClientCase) -> None:
        login = LoginsFor({"bitbucket": {"workspace": "acme", "method": case.method}}, case.environ).logins().bitbucket()

        assert sorted(login.client_kwargs()) == case.keys

    def test_the_cookie_method_sends_the_pasted_header(self) -> None:
        login = LoginsFor({"bitbucket": {"workspace": "acme", "method": "cookie"}}, {"BRIAR_ACME_CO_BITBUCKET_COOKIE": "session=fake"}).logins().bitbucket()

        assert login.client_kwargs()["session"].headers["Cookie"] == "session=fake"
