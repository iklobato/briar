"""End-to-end: `briar auth login <source>` (spec 005) through the REAL command, the
REAL config reader and the REAL env-file store on disk. Only the terminal is
replaced: the hidden-input reader and `input` return placeholder answers. The
login check before saving (J9) talks to the wire fake of `conftest.py`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, List

import pytest

from briar.config import load_project_config

pytestmark = pytest.mark.integration

FAKE_KEY = "API-KEY-PLACEHOLDER-not-a-secret"
UNAUTHORIZED = 401


class TerminalAnswers:
    """Answers the prompts in order, in place of the hidden-input reader and `input`."""

    def __init__(self, answers: List[str]) -> None:
        self._answers = answers

    def answer(self, message: str) -> str:
        return self._answers.pop(0)


@pytest.fixture(autouse=True)
def project_in_tmp(cli: Callable[..., SimpleNamespace], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Runs after `cli`, which stubs the project config reader: puts the real one back,
    runs in `tmp_path`, and saves secrets to `tmp_path/secrets.env`."""
    monkeypatch.setattr("briar.config.load_project_config", load_project_config)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BRIAR_SECRETS_FILE", str(tmp_path / "secrets.env"))
    return tmp_path / "secrets.env"


@pytest.mark.timeout(20)
@dataclass(frozen=True)
class Wire:
    """The terminal answers and the wire fake a login talks to."""

    monkeypatch: pytest.MonkeyPatch
    api: Any

    def answers(self, answers: List[str]) -> None:
        reader = TerminalAnswers(answers)
        self.monkeypatch.setattr("builtins.input", reader.answer)
        self.monkeypatch.setattr("briar.auth._prompt._read_secret_no_max_canon", reader.answer)


@pytest.fixture
def graphql_wire(monkeypatch: pytest.MonkeyPatch, graphql_at: Any) -> Wire:
    return Wire(monkeypatch, graphql_at)


@pytest.fixture
def jira_wire(monkeypatch: pytest.MonkeyPatch, jira_at: Any) -> Wire:
    return Wire(monkeypatch, jira_at)


def test_auth_login_saves_the_pasted_key_to_the_envfile(cli: Callable[..., SimpleNamespace], project_in_tmp: Path, graphql_wire: Wire) -> None:
    graphql_wire.answers([FAKE_KEY])
    graphql_at = graphql_wire.api
    graphql_at.add("POST", "/graphql", {"data": {"user": {"email": "ops@example.test"}}})

    run = cli("auth", "login", "fireflies", "--company", "acme")

    assert run.code == 0, run.err
    assert f"BRIAR_ACME_FIREFLIES_API_KEY={FAKE_KEY}" in project_in_tmp.read_text()
    assert "auth: fireflies (acme): ops@example.test" in run.out
    assert FAKE_KEY not in run.out
    assert graphql_at.received[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"


@pytest.mark.timeout(20)
def test_auth_login_refused_by_the_source_saves_nothing(cli: Callable[..., SimpleNamespace], project_in_tmp: Path, graphql_wire: Wire) -> None:
    """J9: a key the source refuses never reaches secrets.env."""
    graphql_wire.answers([FAKE_KEY])
    graphql_wire.api.add("POST", "/graphql", {"errors": [{"message": "Invalid API key"}]}, status=UNAUTHORIZED)

    run = cli("auth", "login", "fireflies", "--company", "acme")

    assert run.code == 1
    assert "  - fireflies (acme): the login was refused (HTTP 401: HTTP Error 401: Unauthorized)" in run.out
    assert not project_in_tmp.exists()


@pytest.mark.timeout(20)
def test_auth_login_reads_the_site_from_briar_toml(cli: Callable[..., SimpleNamespace], project_in_tmp: Path, jira_wire: Wire) -> None:
    jira_at = jira_wire.api
    Path(".briar.toml").write_text(f'company = "acme"\n{jira_at.jira_site_toml}')
    jira_wire.answers(["bot@example.test", FAKE_KEY])

    run = cli("auth", "login", "jira", "--company", "acme")

    assert run.code == 0, run.err
    saved = project_in_tmp.read_text()
    assert "BRIAR_ACME_JIRA_EMAIL=bot@example.test" in saved
    assert f"BRIAR_ACME_JIRA_TOKEN={FAKE_KEY}" in saved
    assert [request["path"] for request in jira_at.received] == ["/rest/api/2/myself"]


@pytest.mark.timeout(20)
def test_auth_login_without_the_site_saves_nothing(cli: Callable[..., SimpleNamespace], project_in_tmp: Path) -> None:
    """H1: a missing `site` stops the login before it asks, and nothing is written."""
    run = cli("auth", "login", "jira", "--company", "acme")

    assert run.code == 1
    assert "set `site` under `auth.jira`" in run.out
    assert not project_in_tmp.exists()
