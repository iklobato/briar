"""`briar auth`: dispatch and `status` (decision C15; spec 005 G9, G14, J11, J13, J14).

`status` with a source checks that login live; with a company and no source it
checks every configured login; with neither it lists the names held. The
sources are `FakeProbe` adapters (`fake_sources`): no network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from tests.unit.fake_logins import FakeProbe

USAGE_ERROR = 2
FAKE_SECRET = "PLACEHOLDER-not-a-secret"


class TestUnknownAction:
    def test_unknown_action_is_a_usage_error(self, cli: Callable[..., SimpleNamespace]) -> None:
        assert cli("auth", "make-up").code == USAGE_ERROR


class TestStatusWithoutCompany:
    """No source and no company: every credential name held, as 1.x `auth list` did."""

    def test_names_only_never_values(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)

        run = cli("auth", "status")

        assert run.code == 0
        assert "BRIAR_ACME_FIREFLIES_API_KEY" in run.out
        assert FAKE_SECRET not in run.out


@pytest.mark.usefixtures("fake_sources")
class TestStatusOfASource:
    def test_a_login_that_passes_shows_the_account(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)

        run = cli("auth", "status", "fireflies", "--company", "acme")

        assert (run.code, run.out.splitlines()[-1]) == (0, "  ok           fireflies (acme): bot@example.test")

    def test_a_missing_login_fails_with_its_fix(self, cli: Callable[..., SimpleNamespace]) -> None:
        run = cli("auth", "status", "fireflies", "--company", "acme")

        assert run.code == 1
        assert (
            run.out.splitlines()[-1]
            == "  failed       fireflies (acme): no login saved (BRIAR_ACME_FIREFLIES_API_KEY); run `briar auth login fireflies --company acme`"
        )

    def test_a_refused_login_exits_1(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, fake_sources: Any) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)
        fake_sources.probe(FakeProbe("fireflies", refused="HTTP 401: Unauthorized"))

        run = cli("auth", "status", "fireflies", "--company", "acme")

        assert (run.code, "the login was refused (HTTP 401: Unauthorized)" in run.out) == (1, True)

    def test_a_network_error_is_shown_and_exits_0(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, fake_sources: Any) -> None:
        """J14: as in `doctor`, so `auth status` still works offline."""
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)
        fake_sources.probe(FakeProbe("fireflies", unreachable="no connection"))

        run = cli("auth", "status", "fireflies", "--company", "acme")

        assert (run.code, run.out.splitlines()[-1]) == (0, "  unreachable  fireflies (acme): could not reach it (no connection)")

    def test_a_cloud_with_no_value_uses_its_default_chain(self, cli: Callable[..., SimpleNamespace], fake_sources: Any) -> None:
        fake_sources.probe(FakeProbe("aws", account="arn:aws:sts::000000000000:assumed-role/ro"))

        run = cli("auth", "status", "aws", "--company", "acme")

        assert (run.code, run.out.splitlines()[-1]) == (0, "  ok           aws (acme): arn:aws:sts::000000000000:assumed-role/ro")


@pytest.mark.usefixtures("fake_sources")
class TestStatusOfACompany:
    """J13: no source and a company: every configured login, live."""

    def test_each_configured_source_gets_a_line(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)
        monkeypatch.setenv("BRIAR_ACME_LINEAR_API_KEY", FAKE_SECRET)
        monkeypatch.setenv("BRIAR_OTHER_JIRA_TOKEN", FAKE_SECRET)

        run = cli("auth", "status", "--company", "acme")

        assert run.code == 0
        assert run.out.splitlines() == [
            "auth-status: company=acme sources=linear, fireflies",
            "  ok           linear (acme): bot@example.test",
            "  ok           fireflies (acme): bot@example.test",
        ]

    def test_a_company_with_nothing_configured_says_so(self, cli: Callable[..., SimpleNamespace]) -> None:
        run = cli("auth", "status", "--company", "nobody")

        assert (run.code, run.out.splitlines()) == (0, ["auth-status: company=nobody sources=(none configured)"])
