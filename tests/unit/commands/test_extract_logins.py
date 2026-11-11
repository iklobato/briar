"""`briar extract` and the company logins of `[auth]` (spec 005: G2, G8, G25, H6)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from briar.config import load_project_config

CLI_ERROR = 1


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
        assert "`JIRA_ACME_TOKEN` is a 1.x name: use `BRIAR_ACME_JIRA_TOKEN`. Or run `briar auth login jira --company acme`" in run.err

    def test_github_token_alone_stops_with_the_new_place(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        """G25: briar no longer reads `GITHUB_TOKEN`."""
        monkeypatch.setenv("GITHUB_TOKEN", "fake-1x-token")
        monkeypatch.delenv("BRIAR_ACME_GITHUB_TOKEN", raising=False)

        run = cli("extract", "--company", "acme", "--include", "pr-archaeology", "--repo", "acme/app")

        assert run.code == CLI_ERROR
        assert '`GITHUB_TOKEN` is a 1.x name: use `BRIAR_ACME_GITHUB_TOKEN` with `method = "token"`' in run.err
