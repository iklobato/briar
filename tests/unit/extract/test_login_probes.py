"""The "who am I" and the resource read of each adapter (spec 005 PR 3: J1, J4, J5,
J15), each faked at the edge of its client: the HTTP answer, the vendor client
class, or moto for AWS."""

from __future__ import annotations

import io
import json
import sys
import types
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping
from unittest import mock

import pytest
import requests
from github import GithubException
from moto import mock_aws

from briar.auth.check import LoginCheck, LoginRejected, LoginTarget, SourceUnreachable
from briar.auth.logins import CompanyLogins
from briar.extract._clouds import make_cloud
from briar.extract._meetings import make_meeting
from briar.extract._providers import make_provider
from briar.extract._trackers import make_tracker
from briar.extract.login_probes import SourceProbes
from tests.unit.fake_logins import FakeLogins

FAKE = "PLACEHOLDER-not-a-secret"
NOT_FOUND = 404
UNAUTHORIZED = 401


class Logins:
    """The logins of company `acme` for one source, with placeholder secrets."""

    def github(self) -> CompanyLogins:
        return FakeLogins(environ={"BRIAR_ACME_GITHUB_TOKEN": FAKE}).logins()

    def jira(self) -> CompanyLogins:
        environ = {"BRIAR_ACME_JIRA_EMAIL": "bot@example.test", "BRIAR_ACME_JIRA_TOKEN": FAKE}
        return FakeLogins({"jira": {"site": "acme.atlassian.net"}}, environ).logins()

    def bitbucket(self, method: str) -> CompanyLogins:
        environ = {"BRIAR_ACME_BITBUCKET_EMAIL": "bot@example.test", "BRIAR_ACME_BITBUCKET_TOKEN": FAKE, "BRIAR_ACME_BITBUCKET_ACCESS_TOKEN": FAKE}
        return FakeLogins({"bitbucket": {"workspace": "acme", "method": method}}, environ).logins()

    def keyed(self, source: str) -> CompanyLogins:
        return FakeLogins(environ={f"BRIAR_ACME_{source.upper()}_API_KEY": FAKE}).logins()


@dataclass(frozen=True)
class GithubAnswers:
    """`GithubApi.get_json`: the paths asked, a body per path, a PyGithub error for the rest."""

    bodies: Mapping[str, Any]
    asked: List[str] = field(default_factory=list)

    def get_json(self, path: str) -> Any:
        self.asked.append(path)
        if path not in self.bodies:
            raise GithubException(NOT_FOUND, {"message": "Not Found"})
        return self.bodies[path]


@dataclass(frozen=True)
class GraphqlAnswers:
    """`urlopen_with_retry` of the Linear and Fireflies GraphQL calls: one JSON answer per call."""

    answers: List[Dict[str, Any]]
    queries: List[str] = field(default_factory=list)

    def urlopen(self, request: Any, timeout: float) -> io.BytesIO:
        self.queries.append(json.loads(request.data)["query"])
        return io.BytesIO(json.dumps(self.answers.pop(0)).encode())


class TestAvailableWithoutALogin:
    """J15: the login no longer decides `is_available`; the check before the run does."""

    @pytest.mark.parametrize("kind", ["github", "bitbucket", "gitlab"])
    def test_repo_hosts(self, kind: str) -> None:
        assert make_provider(kind, FakeLogins().logins()).is_available() is True

    @pytest.mark.parametrize("kind", ["jira", "linear", "github-issues", "bitbucket-issues"])
    def test_trackers(self, kind: str) -> None:
        assert make_tracker(kind, FakeLogins().logins()).is_available() is True

    def test_meetings_and_aws(self) -> None:
        assert (make_meeting("fireflies", FakeLogins().logins()).is_available(), make_cloud("aws", FakeLogins().logins()).is_available()) == (True, True)


class TestGithub:
    @pytest.mark.parametrize("make", ["provider", "issues"])
    def test_who_am_i_and_the_repo_read(self, make: str) -> None:
        answers = GithubAnswers({"/user": {"login": "octo"}, "/repos/acme/app": {"full_name": "acme/app"}})
        adapter = make_provider("github", Logins().github()) if make == "provider" else make_tracker("github-issues", Logins().github())

        with mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=answers.get_json):
            assert adapter.who_am_i() == "octo"
            adapter.read("acme/app")

        assert answers.asked == ["/user", "/repos/acme/app"]

    def test_a_repo_it_cannot_see_is_refused_with_the_status(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=GithubAnswers({}).get_json):
            with pytest.raises(LoginRejected, match=r'^HTTP 404: 404 \{"message": "Not Found"\}$'):
                make_provider("github", Logins().github()).read("acme/secret")

    def test_a_bad_repo_name_is_refused_before_any_call(self) -> None:
        answers = GithubAnswers({})

        with mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=answers.get_json):
            with pytest.raises(LoginRejected, match=r"^'acme/app/\.\./x' is not an owner/repo name$"):
                make_provider("github", Logins().github()).read("acme/app/../x")

        assert answers.asked == []


@dataclass(frozen=True)
class FakeJira:
    """The atlassian `Jira` client: `myself` and `get_project`."""

    projects: List[str] = field(default_factory=list)
    error: Any = None

    def myself(self) -> Dict[str, str]:
        if self.error is not None:
            raise self.error
        return {"emailAddress": "bot@example.test", "accountId": "abc"}

    def get_project(self, key: str) -> Dict[str, str]:
        self.projects.append(key)
        return {"key": key}


class TestJira:
    def test_who_am_i_and_the_project_read(self) -> None:
        jira = FakeJira()
        tracker = make_tracker("jira", Logins().jira())

        with mock.patch("atlassian.Jira", return_value=jira):
            assert tracker.who_am_i() == "bot@example.test"
            tracker.read("ENG")

        assert jira.projects == ["ENG"]

    def test_a_refused_login_and_an_unreachable_site(self) -> None:
        response = requests.Response()
        response.status_code = UNAUTHORIZED
        refused = FakeJira(error=requests.HTTPError("Unauthorized (401)", response=response))

        with mock.patch("atlassian.Jira", return_value=refused):
            with pytest.raises(LoginRejected, match=r"^HTTP 401: Unauthorized \(401\)$"):
                make_tracker("jira", Logins().jira()).who_am_i()
        with mock.patch("atlassian.Jira", return_value=FakeJira(error=requests.ConnectionError("Failed to resolve acme.atlassian.net"))):
            with pytest.raises(SourceUnreachable, match=r"^Failed to resolve acme\.atlassian\.net$"):
                make_tracker("jira", Logins().jira()).who_am_i()

    def test_a_bad_project_key_is_refused(self) -> None:
        with pytest.raises(LoginRejected, match=r"^'eng' is not a Jira project key$"):
            make_tracker("jira", Logins().jira()).read("eng")


@dataclass(frozen=True)
class BitbucketLookup:
    """`workspaces` or `repositories` of the atlassian Bitbucket `Cloud` client: `get(slug)`."""

    kind: str
    calls: List[str]

    def get(self, slug: str) -> "BitbucketWorkspace":
        self.calls.append(f"{self.kind} {slug}")
        return BitbucketWorkspace(self.calls)


@dataclass(frozen=True)
class BitbucketWorkspace:
    calls: List[str]

    @property
    def repositories(self) -> BitbucketLookup:
        return BitbucketLookup("repository", self.calls)


@dataclass(frozen=True)
class FakeBitbucket:
    """The atlassian Bitbucket `Cloud` client: `get("user")` and
    `workspaces.get(ws).repositories.get(slug)`."""

    calls: List[str] = field(default_factory=list)

    def get(self, path: str) -> Dict[str, str]:
        self.calls.append(f"get {path}")
        return {"username": "bot", "display_name": "Bot"}

    @property
    def workspaces(self) -> BitbucketLookup:
        return BitbucketLookup("workspace", self.calls)


class TestBitbucket:
    @pytest.mark.parametrize("make", ["provider", "issues"])
    def test_who_am_i_then_the_workspace_and_the_repo(self, make: str) -> None:
        cloud = FakeBitbucket()
        logins = Logins().bitbucket("token")
        adapter = make_provider("bitbucket", logins) if make == "provider" else make_tracker("bitbucket-issues", logins)

        with mock.patch("atlassian.bitbucket.cloud.Cloud", return_value=cloud):
            assert adapter.who_am_i() == "bot"
            adapter.read("app")
            adapter.read("other/api")

        assert cloud.calls == ["get user", "workspace acme", "repository app", "workspace other", "repository api"]

    def test_an_access_token_has_no_user_to_ask(self) -> None:
        cloud = FakeBitbucket()

        with mock.patch("atlassian.bitbucket.cloud.Cloud", return_value=cloud):
            assert make_provider("bitbucket", Logins().bitbucket("access-token")).who_am_i() == "an access token of acme"

        assert cloud.calls == []


class TestGraphql:
    def test_linear_who_am_i_and_the_team_read(self) -> None:
        answers = GraphqlAnswers([{"data": {"viewer": {"email": "bot@example.test"}}}, {"data": {"teams": {"nodes": [{"id": "t1"}]}}}])
        tracker = make_tracker("linear", Logins().keyed("linear"))

        with mock.patch("briar.extract._trackers.linear.urlopen_with_retry", side_effect=answers.urlopen):
            assert tracker.who_am_i() == "bot@example.test"
            tracker.read("ENG")

        assert ["viewer" in answers.queries[0], "teams" in answers.queries[1]] == [True, True]

    def test_linear_a_team_it_cannot_see_is_refused(self) -> None:
        answers = GraphqlAnswers([{"data": {"teams": {"nodes": []}}}])

        with mock.patch("briar.extract._trackers.linear.urlopen_with_retry", side_effect=answers.urlopen):
            with pytest.raises(LoginRejected, match=r"^no Linear team with key 'ENG'$"):
                make_tracker("linear", Logins().keyed("linear")).read("ENG")

    def test_fireflies_who_am_i(self) -> None:
        answers = GraphqlAnswers([{"data": {"user": {"email": "ops@example.test", "name": "Ops"}}}])

        with mock.patch("briar.extract._meetings.fireflies.urlopen_with_retry", side_effect=answers.urlopen):
            assert make_meeting("fireflies", Logins().keyed("fireflies")).who_am_i() == "ops@example.test"


class TestAws:
    @pytest.fixture
    def moto(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        for name, value in (("AWS_ACCESS_KEY_ID", "testing"), ("AWS_SECRET_ACCESS_KEY", "testing"), ("AWS_DEFAULT_REGION", "us-east-1")):
            monkeypatch.setenv(name, value)
        with mock_aws():
            yield

    def test_who_am_i_is_the_caller_arn(self, moto: None) -> None:
        assert make_cloud("aws", FakeLogins().logins()).who_am_i() == "arn:aws:sts::123456789012:user/moto"

    def test_a_profile_that_is_not_there_is_refused(self, moto: None, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))

        with pytest.raises(LoginRejected, match=r"^The config profile \(acme-ro\) could not be found$"):
            make_cloud("aws", FakeLogins({"aws": {"profile": "acme-ro"}}).logins()).who_am_i()


class TestSourceProbes:
    """J9, J10: the default adapter of a source, and the run targets first."""

    def test_the_check_of_one_source_goes_through_its_adapter(self) -> None:
        logins = Logins().github()
        answers = GithubAnswers({"/user": {"login": "octo"}})

        with mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=answers.get_json):
            lines = LoginCheck(logins).check([SourceProbes(logins).target("github")])

        assert [line.line() for line in lines] == ["github (acme): octo"]

    def test_a_source_the_run_reads_keeps_its_resources(self) -> None:
        logins = Logins().github()
        from_run = LoginTarget(make_provider("github", logins), ("acme/app",))

        targets = SourceProbes(logins).for_sources(["github", "fireflies"], [from_run])

        assert [(target.probe.login_source, target.resources) for target in targets] == [("github", ("acme/app",)), ("fireflies", ())]

    def test_every_login_source_has_an_adapter(self) -> None:
        """A real run, 2026-11-13: `briar doctor` in a project with `[auth.slack]` raised
        `KeyError: 'slack'`, since the Slack login had no default adapter."""
        logins = FakeLogins({"slack": {"method": "session", "workspace": "acme"}}).logins()

        assert {source: SourceProbes(logins).target(source).probe.login_source for source in CompanyLogins.SOURCES} == {
            source: source for source in CompanyLogins.SOURCES
        }

    def test_a_cloud_target_reads_its_scope(self) -> None:
        logins = FakeLogins({"gcp": {"project": "acme-prod"}, "azure": {"subscription": "s1"}}).logins()

        assert [SourceProbes(logins).target(source).resources for source in ("aws", "gcp", "azure")] == [(), ("acme-prod",), ("s1",)]


@dataclass(frozen=True)
class Modules:
    """Stands in for the `_try_import` of an optional SDK: a stub per module name."""

    stubs: Mapping[str, Any]

    def find(self, name: str) -> Any:
        return self.stubs.get(name)


class TestGcpAndAzure:
    """The SDKs are optional extras: their modules are stubbed at import."""

    def test_gcp_refreshes_the_adc_and_reads_the_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        asked: List[str] = []
        credentials = types.SimpleNamespace(service_account_email="", account="bot@example.test", refresh=asked.append)
        manager = mock.MagicMock()
        monkeypatch.setitem(sys.modules, "google.auth.transport.requests", types.SimpleNamespace(Request=str))
        auth = types.SimpleNamespace(default=lambda scopes: (credentials, "acme-prod"))
        modules = Modules({"google.auth": auth, "googleapiclient.discovery": types.SimpleNamespace(build=manager)})
        cloud = make_cloud("gcp", FakeLogins({"gcp": {"project": "acme-prod"}}).logins())

        with mock.patch("briar.extract._clouds.gcp._try_import", side_effect=modules.find):
            assert cloud.who_am_i() == "bot@example.test"
            cloud.read("acme-prod")

        manager.return_value.projects.return_value.get.assert_called_once_with(projectId="acme-prod")

    @pytest.mark.parametrize(
        "source, line",
        [
            ("gcp", "gcp (acme): could not check it: google-auth not installed: run `pip install 'briar-cli[gcp]'`"),
            ("azure", "azure (acme): could not check it: azure-identity not installed: run `pip install 'briar-cli[azure]'`"),
        ],
        ids=["gcp", "azure"],
    )
    def test_a_missing_sdk_says_what_to_install(self, source: str, line: str) -> None:
        logins = FakeLogins({"gcp": {"project": "acme-prod"}, "azure": {"subscription": "s1"}}).logins()

        with mock.patch(f"briar.extract._clouds.{source}._try_import", return_value=None):
            lines = LoginCheck(logins).check([SourceProbes(logins).target(source)])

        assert [checked.line() for checked in lines] == [line]

    def test_azure_gets_a_token_for_the_tenant_and_reads_the_subscription(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tokens: List[str] = []
        identity = types.SimpleNamespace(AzureCliCredential=lambda tenant_id: types.SimpleNamespace(get_token=tokens.append, tenant=tenant_id))
        subscriptions = mock.MagicMock()
        modules = Modules({"azure.identity": identity, "azure.mgmt.subscription": types.SimpleNamespace(SubscriptionClient=subscriptions)})
        cloud = make_cloud("azure", FakeLogins({"azure": {"tenant": "t1", "subscription": "s1"}}).logins())

        with mock.patch("briar.extract._clouds.azure._try_import", side_effect=modules.find):
            assert cloud.who_am_i() == "tenant t1"
            cloud.read("s1")

        assert tokens == ["https://management.azure.com/.default"]
        subscriptions.return_value.subscriptions.get.assert_called_once_with("s1")
