"""The login check before a run (spec 005 PR 3: G12, G13, J1, J3, J5, J6, J17)."""

from __future__ import annotations

from typing import ClassVar, Mapping, Tuple

import pytest

from briar.auth.check import CheckResult, CheckState, LoginCheck, LoginCheckFailed, LoginTarget, TargetGroups
from briar.errors import CliError
from tests.unit.fake_logins import FakeLogins, FakeProbe, Fixes


class SdkMissing:
    """An adapter whose library is not installed."""

    login_source = "github"

    def who_am_i(self) -> str:
        raise CliError("google-auth not installed: run `pip install 'briar-cli[gcp]'`")

    def read(self, resource: str) -> None:
        raise CliError(f"not read: {resource}")


class CheckFor:
    """The check of company `acme`, with a placeholder secret saved for each source in `saved`."""

    NAMES: ClassVar[Mapping[str, str]] = {"github": "BRIAR_ACME_GITHUB_TOKEN", "fireflies": "BRIAR_ACME_FIREFLIES_API_KEY"}

    def __init__(self, saved: Tuple[str, ...]) -> None:
        self._saved = saved

    def check(self) -> LoginCheck:
        environ = {self.NAMES[source]: "PLACEHOLDER-not-a-secret" for source in self._saved}
        return LoginCheck(FakeLogins(environ=environ).logins())


class TestOneSource:
    def test_who_am_i_then_each_resource_once_in_order(self) -> None:
        probe = FakeProbe("github")
        targets = [LoginTarget(probe, ("acme/app", "acme/api")), LoginTarget(FakeProbe("github"), ("acme/app", "acme/web"))]

        lines = CheckFor(("github",)).check().check(targets)

        assert lines == [CheckResult("github", "acme", CheckState.OK, "bot@example.test")]
        assert probe.calls == ["who_am_i", "read acme/app", "read acme/api", "read acme/web"]

    def test_no_login_saved_fails_without_calling_the_source(self) -> None:
        probe = FakeProbe("github")

        lines = CheckFor(()).check().check([LoginTarget(probe, ("acme/app",))])

        assert [line.line() for line in lines] == [f"github (acme): no login saved (BRIAR_ACME_GITHUB_TOKEN); {Fixes().github()}"]
        assert probe.calls == []

    def test_a_refused_login_reads_nothing_and_names_the_login(self) -> None:
        probe = FakeProbe("github", refused="HTTP 401: Bad credentials")

        lines = CheckFor(("github",)).check().check([LoginTarget(probe, ("acme/app",))])

        assert [(line.state, line.line()) for line in lines] == [
            (CheckState.FAILED, f"github (acme): the login was refused (HTTP 401: Bad credentials); {Fixes().github()}")
        ]
        assert probe.calls == ["who_am_i"]

    def test_an_error_that_is_not_the_source_answer_is_not_a_refusal(self) -> None:
        """A missing SDK or setting: its own message, no login to run again."""
        lines = CheckFor(("github",)).check().check([LoginTarget(SdkMissing())])

        assert [line.line() for line in lines] == ["github (acme): could not check it: google-auth not installed: run `pip install 'briar-cli[gcp]'`"]

    def test_an_unreachable_source_is_its_own_state(self) -> None:
        lines = CheckFor(("github",)).check().check([LoginTarget(FakeProbe("github", unreachable="timed out"))])

        assert [(line.state, line.line()) for line in lines] == [(CheckState.UNREACHABLE, "github (acme): could not reach it (timed out)")]

    def test_each_unreadable_resource_is_a_line_and_the_rest_are_read(self) -> None:
        """J5: a 404 is 'not found or no access'; the fix names the access of this login."""
        probe = FakeProbe("github", unreadable=("acme/secret", "acme/gone"))

        lines = CheckFor(("github",)).check().check([LoginTarget(probe, ("acme/secret", "acme/app", "acme/gone"))])

        assert [line.line() for line in lines] == [
            "github (acme): bot@example.test cannot read acme/secret (HTTP 404: Not Found); check the name and the access of this login",
            "github (acme): bot@example.test cannot read acme/gone (HTTP 404: Not Found); check the name and the access of this login",
        ]
        assert probe.calls == ["who_am_i", "read acme/secret", "read acme/app", "read acme/gone"]


class TestRequire:
    """G12, G13, J3: any failure stops the run and every failure is listed."""

    def test_every_failure_is_listed(self) -> None:
        targets = [LoginTarget(FakeProbe("github", refused="HTTP 401: Bad credentials")), LoginTarget(FakeProbe("jira"))]

        with pytest.raises(LoginCheckFailed) as caught:
            CheckFor(("github",)).check().require(targets)

        assert str(caught.value) == (
            "the login check failed; fix these, then run again:\n"
            f"  - github (acme): the login was refused (HTTP 401: Bad credentials); {Fixes().github()}\n"
            f"  - jira (acme): no login saved (BRIAR_ACME_JIRA_EMAIL, BRIAR_ACME_JIRA_TOKEN); {Fixes().jira()}"
        )

    def test_a_network_error_stops_the_run_too(self) -> None:
        targets = [LoginTarget(FakeProbe("github")), LoginTarget(FakeProbe("fireflies", unreachable="no connection"))]

        with pytest.raises(LoginCheckFailed, match=r"  - fireflies \(acme\): could not reach it \(no connection\)$"):
            CheckFor(("github", "fireflies")).check().require(targets)

    def test_a_passing_check_gives_the_accounts(self) -> None:
        targets = [LoginTarget(FakeProbe("github", account="octo")), LoginTarget(FakeProbe("fireflies", account="ops@example.test"))]

        lines = CheckFor(("github", "fireflies")).check().require(targets)

        assert [line.line() for line in lines] == ["github (acme): octo", "fireflies (acme): ops@example.test"]

    def test_no_target_passes(self) -> None:
        assert CheckFor(()).check().require([]) == []


class TestTargetGroups:
    def test_the_first_adapter_of_a_source_answers(self) -> None:
        first, second = FakeProbe("github"), FakeProbe("github")

        groups = TargetGroups([LoginTarget(first), LoginTarget(FakeProbe("jira")), LoginTarget(second)])

        assert list(groups.probes()) == ["github", "jira"]
        assert groups.probes()["github"] is first
