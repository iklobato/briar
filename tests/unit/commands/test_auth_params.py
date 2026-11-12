"""`briar auth`: the effect of every option of `login` and `status` (C15, spec 005
G6, G9, H1, J9, J13).

  login    target (a source name) · --company · --runbook   (checks before it saves)
  status   [target] · --company · --runbook   (no target: every configured login; no company: the names held)

The store, the terminal prompt and the sources (`fake_sources`) are fakes at the
command seams; values are never echoed."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest
from pytest_mock import MockerFixture

from briar.auth import MockPromptIO
from tests.unit.fake_logins import FakeProbe

FAKE_SECRET = "ACCESS-TOKEN-PLACEHOLDER-not-a-secret"
USAGE_ERROR = 2
SOURCES = ("github", "jira", "bitbucket", "linear", "fireflies")


class FakeStore:
    def __init__(self, present: Dict[str, str]) -> None:
        self.values = present
        self.writes: List[tuple] = []

    def read(self, name: str) -> Optional[str]:
        return self.values.get(name)

    def write(self, name: str, value: str) -> None:
        self.writes.append((name, value))
        self.values[name] = value

    def list(self) -> List[str]:
        return sorted(self.values)


@dataclass(frozen=True)
class Seam:
    """What the command writes to, and what the prompt answers."""

    present: Dict[str, str] = field(default_factory=dict)
    answers: List[str] = field(default_factory=list)

    def install(self, mocker: MockerFixture) -> FakeStore:
        store = FakeStore(dict(self.present))
        mocker.patch("briar.commands.auth.EnvFileStore", return_value=store)
        mocker.patch("briar.commands.auth.TerminalPromptIO", return_value=MockPromptIO(answers=list(self.answers)))
        return store


@pytest.mark.usefixtures("fake_sources")
class TestCompanyFlag:
    def test_status_without_target_checks_only_that_company(
        self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Seam().install(mocker)
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)
        monkeypatch.setenv("BRIAR_GLOBEX_LINEAR_API_KEY", FAKE_SECRET)

        run = cli("auth", "status", "--company", "acme")

        assert (run.code, run.out.splitlines()[1:]) == (0, ["  ok           fireflies (acme): bot@example.test"])

    def test_status_without_target_or_company_lists_all(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture) -> None:
        Seam({"BRIAR_ACME_JIRA_TOKEN": "x", "BRIAR_GLOBEX_JIRA_TOKEN": "y"}).install(mocker)

        run = cli("auth", "status")

        assert (run.code, "BRIAR_ACME_JIRA_TOKEN" in run.out, "BRIAR_GLOBEX_JIRA_TOKEN" in run.out) == (0, True, True)

    def test_status_checks_the_login_of_the_company(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        Seam().install(mocker)
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)

        run = cli("auth", "status", "fireflies", "--company", "acme")

        assert (run.code, "  ok           fireflies (acme): bot@example.test" in run.out, FAKE_SECRET in run.out) == (0, True, False)

    def test_login_checks_then_writes_under_the_company(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture) -> None:
        store = Seam(answers=[FAKE_SECRET]).install(mocker)

        run = cli("auth", "login", "fireflies", "--company", "globex")

        assert run.code == 0
        assert store.writes == [("BRIAR_GLOBEX_FIREFLIES_API_KEY", FAKE_SECRET)]
        assert "auth: fireflies (globex): bot@example.test" in run.out
        assert FAKE_SECRET not in run.out


@dataclass(frozen=True)
class LoginSeam:
    """The store the login writes to, and the sources it checks."""

    store: FakeStore
    sources: Any


@dataclass(frozen=True)
class FailedLogin:
    probe: FakeProbe
    shown: str


class TestLoginCheck:
    """J9: `briar auth login` runs "who am I" with the values entered, and saves only when it passes."""

    @pytest.fixture
    def seam(self, mocker: MockerFixture, fake_sources: Any) -> LoginSeam:
        return LoginSeam(Seam(answers=[FAKE_SECRET]).install(mocker), fake_sources)

    @pytest.mark.parametrize(
        "failed",
        [
            FailedLogin(FakeProbe("fireflies", refused="HTTP 401: Unauthorized"), "fireflies (globex): the login was refused (HTTP 401: Unauthorized)"),
            FailedLogin(FakeProbe("fireflies", unreachable="no connection"), "fireflies (globex): could not reach it (no connection)"),
        ],
        ids=["refused", "network"],
    )
    def test_a_login_that_fails_its_check_saves_nothing(self, cli: Callable[..., SimpleNamespace], seam: LoginSeam, failed: FailedLogin) -> None:
        seam.sources.probe(failed.probe)

        run = cli("auth", "login", "fireflies", "--company", "globex")

        assert (run.code, seam.store.writes, failed.probe.calls) == (1, [], ["who_am_i"])
        assert f"  - {failed.shown}" in run.out

    def test_the_login_check_reads_no_resource(self, cli: Callable[..., SimpleNamespace], seam: LoginSeam) -> None:
        """J9: the login does not know which repos a run will read."""
        probe = seam.sources.probe(FakeProbe("fireflies"))

        cli("auth", "login", "fireflies", "--company", "globex")

        assert probe.calls == ["who_am_i"]


@pytest.mark.usefixtures("fake_sources")
class TestTarget:
    def test_target_omitted_is_usage_error(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture) -> None:
        Seam().install(mocker)

        run = cli("auth", "login")

        assert run.code == USAGE_ERROR
        assert "Missing argument 'TARGET'" in run.err

    @pytest.mark.parametrize(
        "target", ["not-a-target", "github-pat", "jira-token", "linear-api-key"], ids=["unknown", "github-pat", "jira-token", "linear-api-key"]
    )
    def test_an_unknown_or_1x_target_is_usage_error(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture, target: str) -> None:
        """G9: the 1.x target names get the usual usage error, with the valid list."""
        Seam().install(mocker)

        run = cli("auth", "login", target)

        assert run.code == USAGE_ERROR
        assert f"'{target}' is not one of 'github', 'jira', 'bitbucket', 'linear', 'fireflies'" in run.err

    @pytest.mark.parametrize("target", SOURCES, ids=SOURCES)
    def test_each_source_is_a_target(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture, target: str) -> None:
        Seam().install(mocker)

        run = cli("auth", "status", target, "--company", "acme")

        assert run.code == 1
        assert f"  failed       {target} (acme): no login saved (BRIAR_ACME_{target.upper()}_" in run.out


@pytest.mark.usefixtures("fake_sources")
class TestConfig:
    def test_login_without_the_site_stops_and_asks_nothing(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture) -> None:
        """H1: briar never writes the config; it says what to add where."""
        store = Seam().install(mocker)

        run = cli("auth", "login", "jira", "--company", "acme")

        assert run.code == 1
        assert "error: set `site` under `auth.jira` in .briar.toml (briar jira login for acme)" in run.out
        assert store.writes == []

    def test_login_reads_the_runbook_company(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture, tmp_path: Path) -> None:
        """G6: `--runbook FILE` reads `companies.<company>.auth`, not `.briar.toml`."""
        runbook = tmp_path / "acme.yaml"
        runbook.write_text("version: 1\ncompanies:\n  acme:\n    auth:\n      jira:\n        site: acme.atlassian.net\n")
        store = Seam(answers=["bot@example.test", FAKE_SECRET]).install(mocker)

        run = cli("auth", "login", "jira", "--company", "acme", "--runbook", str(runbook))

        assert run.code == 0
        assert store.writes == [("BRIAR_ACME_JIRA_EMAIL", "bot@example.test"), ("BRIAR_ACME_JIRA_TOKEN", FAKE_SECRET)]

    def test_a_company_missing_from_the_runbook_stops(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture, tmp_path: Path) -> None:
        runbook = tmp_path / "acme.yaml"
        runbook.write_text("version: 1\ncompanies:\n  acme: {}\n")
        Seam().install(mocker)

        run = cli("auth", "status", "jira", "--company", "globex", "--runbook", str(runbook))

        assert run.code == 1
        assert f"error: company 'globex' is not in {runbook} (companies: acme)" in run.out

    def test_status_gives_the_1x_hint(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture) -> None:
        """G8: the status of a login names the 1.x env var that stands in for it."""
        Seam().install(mocker)
        mocker.patch.dict("os.environ", {"JIRA_ACME_TOKEN": FAKE_SECRET})

        run = cli("auth", "status", "jira", "--company", "acme")

        assert run.code == 1
        assert "  failed       jira (acme): `JIRA_ACME_TOKEN` is a 1.x name: use `BRIAR_ACME_JIRA_TOKEN`" in run.out
