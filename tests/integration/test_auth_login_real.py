"""End-to-end: `briar auth login <source>` (spec 005) through the REAL command, the
REAL config reader and the REAL env-file store on disk. Only the terminal is
replaced: the hidden-input reader and `input` return placeholder answers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable, List

import pytest

from briar.config import load_project_config

pytestmark = pytest.mark.integration

FAKE_KEY = "API-KEY-PLACEHOLDER-not-a-secret"


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
def test_auth_login_saves_the_pasted_key_to_the_envfile(cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, project_in_tmp: Path) -> None:
    monkeypatch.setattr("briar.auth._prompt._read_secret_no_max_canon", TerminalAnswers([FAKE_KEY]).answer)

    run = cli("auth", "login", "fireflies", "--company", "acme")

    assert run.code == 0, run.err
    assert f"BRIAR_ACME_FIREFLIES_API_KEY={FAKE_KEY}" in project_in_tmp.read_text()
    assert FAKE_KEY not in run.out


@pytest.mark.timeout(20)
def test_auth_login_reads_the_site_from_briar_toml(cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, project_in_tmp: Path) -> None:
    Path(".briar.toml").write_text('company = "acme"\n[auth.jira]\nsite = "acme.atlassian.net"\n')
    answers = TerminalAnswers(["bot@example.test", FAKE_KEY])
    monkeypatch.setattr("builtins.input", answers.answer)
    monkeypatch.setattr("briar.auth._prompt._read_secret_no_max_canon", answers.answer)

    run = cli("auth", "login", "jira", "--company", "acme")

    assert run.code == 0, run.err
    saved = project_in_tmp.read_text()
    assert "BRIAR_ACME_JIRA_EMAIL=bot@example.test" in saved
    assert f"BRIAR_ACME_JIRA_TOKEN={FAKE_KEY}" in saved


@pytest.mark.timeout(20)
def test_auth_login_without_the_site_saves_nothing(cli: Callable[..., SimpleNamespace], project_in_tmp: Path) -> None:
    """H1: a missing `site` stops the login before it asks, and nothing is written."""
    run = cli("auth", "login", "jira", "--company", "acme")

    assert run.code == 1
    assert "set `site` under `auth.jira`" in run.out
    assert not project_in_tmp.exists()
