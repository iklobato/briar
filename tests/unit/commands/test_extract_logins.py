"""`briar extract` and the company logins of `[auth]` (spec 005: G2, G8, G12, G13, G25, H6, J2, J3).

The GitHub API answers come from a fake `GithubApi.get_json`: no network."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, List, Mapping
from unittest import mock

import pytest
import requests
from github import GithubException

from briar.config import load_project_config
from tests.unit.fake_logins import Fixes

CLI_ERROR = 1
NOT_FOUND = 404
GITHUB_LOGIN = "BRIAR_ACME_GITHUB_TOKEN"


@dataclass(frozen=True)
class GithubAnswers:
    """`GithubApi.get_json`: a body per path; the rest is a 404, or `error` when set."""

    bodies: Mapping[str, Any]
    error: Any = None
    asked: List[str] = field(default_factory=list)

    def get_json(self, path: str) -> Any:
        self.asked.append(path)
        if self.error is not None:
            raise self.error
        if path not in self.bodies:
            raise GithubException(NOT_FOUND, {"message": "Not Found"})
        return self.bodies[path]


class TestExtractLogins:
    @pytest.fixture(autouse=True)
    def project_in_tmp(self, cli: Callable[..., SimpleNamespace], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Runs after `cli`, which stubs the project config reader: puts the real one back
        and runs in `tmp_path`, so the `.briar.toml` written there is read."""
        monkeypatch.setattr("briar.config.load_project_config", load_project_config)
        monkeypatch.chdir(tmp_path)

    def test_a_bad_auth_key_stops_before_any_extractor(self, cli: Callable[..., SimpleNamespace]) -> None:
        """H6: the whole `[auth]` block is checked, even a source the run does not use."""
        Path(".briar.toml").write_text('[auth.azure]\ntennant = "x"\n')

        run = cli("extract", "--company", "acme", "--include", "pr-archaeology", "--repo", "acme/app")

        assert run.code == CLI_ERROR
        assert "`auth.azure.tennant` in" in run.err
        assert "unknown key; allowed: tenant" in run.err

    def test_a_1x_name_stops_with_the_new_place(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        """G8: the 1.x Jira token with no 2.0 login stops the run and names the fix."""
        Path(".briar.toml").write_text('[auth.jira]\nsite = "acme.atlassian.net"\n[extract.active-tickets]\nprojects = ["ENG"]\n')
        monkeypatch.setenv("JIRA_ACME_TOKEN", "fake-1x-token")
        monkeypatch.delenv("BRIAR_ACME_JIRA_TOKEN", raising=False)

        run = cli("extract", "--company", "acme", "--include", "active-tickets")

        assert run.code == CLI_ERROR
        assert "`JIRA_ACME_TOKEN` is a 1.x name: use `BRIAR_ACME_JIRA_TOKEN`\n" in run.err

    def test_github_token_alone_stops_with_the_new_place(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        """G25: briar no longer reads `GITHUB_TOKEN`."""
        monkeypatch.setenv("GITHUB_TOKEN", "fake-1x-token")
        monkeypatch.delenv("BRIAR_ACME_GITHUB_TOKEN", raising=False)

        run = cli("extract", "--company", "acme", "--include", "pr-archaeology", "--repo", "acme/app")

        assert run.code == CLI_ERROR
        assert '`GITHUB_TOKEN` is a 1.x name: use `BRIAR_ACME_GITHUB_TOKEN` with `method = "token"`' in run.err


class TestTheCheckBeforeTheRun:
    """G12, G13, J2, J3: the check runs before any extractor, and any failure stops the run."""

    @pytest.fixture(autouse=True)
    def project_in_tmp(self, cli: Callable[..., SimpleNamespace], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("briar.config.load_project_config", load_project_config)
        monkeypatch.chdir(tmp_path)

    def test_no_login_stops_before_any_extractor_runs(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(GITHUB_LOGIN, raising=False)

        run = cli("extract", "--company", "acme", "--include", "pr-archaeology", "--repo", "acme/app")

        assert run.code == CLI_ERROR
        assert run.err.strip() == (
            "error: the login check failed; fix these, then run again:\n" f"  - github (acme): no login saved (BRIAR_ACME_GITHUB_TOKEN); {Fixes().github()}"
        )
        assert "running" not in run.err

    def test_a_repo_the_login_cannot_read_stops_the_run(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(GITHUB_LOGIN, "PLACEHOLDER-not-a-secret")
        answers = GithubAnswers({"/user": {"login": "octo"}})

        with mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=answers.get_json):
            run = cli("extract", "--company", "acme", "--include", "pr-archaeology", "--repo", "acme/secret")

        assert run.code == CLI_ERROR
        assert (
            '  - github (acme): octo cannot read acme/secret (HTTP 404: 404 {"message": "Not Found"}); check the name and the access of this login' in run.err
        )
        assert answers.asked == ["/user", "/repos/acme/secret"]

    def test_a_network_error_stops_the_run(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        """J3: the run stops on a network error too, listed with the others."""
        monkeypatch.setenv(GITHUB_LOGIN, "PLACEHOLDER-not-a-secret")
        answers = GithubAnswers({}, error=requests.ConnectionError("Max retries exceeded with url: /user"))

        with mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=answers.get_json):
            run = cli("extract", "--company", "acme", "--include", "pr-archaeology", "--repo", "acme/app")

        assert (run.code, "  - github (acme): could not reach it (Max retries exceeded with url: /user)" in run.err) == (CLI_ERROR, True)

    def test_a_passing_check_shows_the_account_then_runs(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(GITHUB_LOGIN, "PLACEHOLDER-not-a-secret")
        answers = GithubAnswers({"/user": {"login": "octo"}, "/repos/acme/app": {"full_name": "acme/app"}})

        with mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=answers.get_json):
            with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=[]):
                run = cli("extract", "--company", "acme", "--include", "pr-archaeology", "--repo", "acme/app")

        assert "  login github (acme): octo\n  running pr-archaeology ..." in run.err

    def test_a_skipped_extractor_needs_no_login(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        """J2: no Jira project, so `active-tickets` is skipped and Jira is not checked."""
        monkeypatch.delenv("BRIAR_ACME_JIRA_TOKEN", raising=False)

        run = cli("extract", "--company", "acme", "--include", "active-tickets")

        assert run.code == CLI_ERROR
        assert "skipped active-tickets" in run.err
        assert "login check" not in run.err
