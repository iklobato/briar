"""The `serve:` block of a runbook company (spec 003: S5, S11, S13, S16, S24, S27, V1, V11, V13)."""

from __future__ import annotations

from typing import Any, Dict

import pytest
from pydantic import ValidationError

from briar.iac.runbook.models import RunbookFile, ServeSettings

TWO = 2
DEFAULT_PARALLEL = 3
DEFAULT_POLL_MINUTES = 5


class Required:
    """The five required values of a `serve:` block."""

    MAX_USD = 5.0
    MAX_LOOPS = 3
    CI_WAIT_MINUTES = 30
    MAX_REVIEW_ROUNDS = 4

    def block(self) -> Dict[str, Any]:
        return {
            "model": "claude-test",
            "max_usd": self.MAX_USD,
            "max_loops": self.MAX_LOOPS,
            "ci_wait_minutes": self.CI_WAIT_MINUTES,
            "max_review_rounds": self.MAX_REVIEW_ROUNDS,
        }


class TestRequired:
    @pytest.mark.parametrize("missing", ["model", "max_usd", "max_loops", "ci_wait_minutes", "max_review_rounds"])
    def test_a_missing_required_field_stops_the_load(self, missing: str) -> None:
        """S5, S24: nobody spends on a model or a limit they did not choose."""
        block = {**Required().block(), "repos": ["acme/app"]}
        del block[missing]

        with pytest.raises(ValidationError, match=rf"companies\.acme\.serve\.{missing}\n  Field required"):
            RunbookFile.model_validate({"companies": {"acme": {"serve": block}}})

    def test_nothing_to_watch_stops_the_load(self) -> None:
        with pytest.raises(ValidationError, match=r"set `repos` or `jira` under `serve`: there is nothing to watch"):
            ServeSettings.model_validate(Required().block())

    def test_a_bad_repo_or_project_name_stops_the_load(self) -> None:
        with pytest.raises(ValidationError, match=r"not an owner/repo name or a Jira project key: app, eng"):
            ServeSettings.model_validate({**Required().block(), "repos": ["app"], "jira": {"eng": "acme/app"}})

    def test_a_bad_domain_or_bot_login_stops_the_load(self) -> None:
        block = {**Required().block(), "repos": ["acme/app"], "allowed_domains": ["https://pypi.org", "pypi.org"], "review_bots": ["renovate"]}

        with pytest.raises(ValidationError, match=r"not a domain \(`pypi.org`\) or a bot login \(`renovate\[bot\]`\): https://pypi.org, renovate"):
            ServeSettings.model_validate(block)

    def test_an_unknown_key_stops_the_load(self) -> None:
        with pytest.raises(ValidationError, match=r"serve\.max_paralel\n  Extra inputs are not permitted"):
            RunbookFile.model_validate({"companies": {"acme": {"serve": {**Required().block(), "repos": ["acme/app"], "max_paralel": TWO}}}})


class TestDefaults:
    def test_the_optional_values(self) -> None:
        settings = ServeSettings.model_validate({**Required().block(), "repos": ["acme/app"]})

        assert (settings.max_parallel, settings.poll_minutes) == (DEFAULT_PARALLEL, DEFAULT_POLL_MINUTES)
        assert (settings.branch, settings.title, settings.status.started, settings.status.review) == ("briar/{key}", "{key}: {title}", "", "")
        assert (settings.review_bots, settings.allowed_domains) == ([], [])

    def test_every_repo_once_with_the_repos_of_jira(self) -> None:
        settings = ServeSettings.model_validate({**Required().block(), "repos": ["acme/app", "acme/web"], "jira": {"ENG": "acme/app", "OPS": "acme/ops"}})

        assert settings.all_repos() == ["acme/app", "acme/web", "acme/ops"]

    def test_a_company_with_no_serve_block_is_left_alone(self) -> None:
        assert RunbookFile.model_validate({"companies": {"acme": {}}}).companies["acme"].serve is None
