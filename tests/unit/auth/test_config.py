"""The `[auth]` block (decisions G2, G4, G5, G26, H6, H10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from briar.auth.config import AuthBlock, AuthTable
from briar.errors import CliError
from briar.iac.runbook.models import RunbookFile

ORIGIN = "/repo/.briar.toml"


@dataclass(frozen=True)
class BadBlock:
    """An `[auth]` table and the whole error it must give."""

    table: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class SiteCase:
    written: str = ""
    kept: str = ""


class TestAuthTable:
    def test_no_auth_table_gives_every_default(self) -> None:
        block = AuthTable({"company": "acme"}, ORIGIN).block()

        assert block == AuthBlock()
        assert (block.jira.method.value, block.bitbucket.method.value, block.github.host) == ("token", "token", "github.com")

    def test_keeps_the_values_written(self) -> None:
        table = {
            "auth": {
                "jira": {"site": "acme.atlassian.net", "method": "cookie", "user_agent": "agent-x"},
                "bitbucket": {"workspace": "acme", "method": "access-token"},
            }
        }

        block = AuthTable(table, ORIGIN).block()

        assert (block.jira.site, block.jira.method.value, block.jira.user_agent) == ("https://acme.atlassian.net", "cookie", "agent-x")
        assert (block.bitbucket.workspace, block.bitbucket.method.value) == ("acme", "access-token")

    @pytest.mark.parametrize(
        "case",
        [
            BadBlock(
                {"auth": {"jiraa": {}}}, "`auth.jiraa` in /repo/.briar.toml: unknown key; allowed: github, jira, bitbucket, linear, fireflies, aws, gcp, azure"
            ),
            BadBlock({"auth": {"jira": {"metod": "token"}}}, "`auth.jira.metod` in /repo/.briar.toml: unknown key; allowed: method, site, user_agent"),
            BadBlock({"auth": {"jira": {"method": "password"}}}, "`auth.jira.method` in /repo/.briar.toml: Input should be 'token' or 'cookie'"),
            BadBlock({"auth": {"aws": {"region": "eu-west-1"}}}, "`auth.aws.region` in /repo/.briar.toml: unknown key; allowed: profile"),
        ],
        ids=["unknown source", "unknown key", "bad method", "key of another table"],
    )
    def test_a_bad_block_stops_with_the_key_and_the_file(self, case: BadBlock) -> None:
        with pytest.raises(CliError) as caught:
            AuthTable(case.table, ORIGIN).block()

        assert str(caught.value) == case.error

    def test_checks_a_source_the_run_does_not_use(self) -> None:
        """H6: the whole block, every time."""
        with pytest.raises(CliError, match=r"`auth.azure.tennant`"):
            AuthTable({"auth": {"jira": {"site": "acme.atlassian.net"}, "azure": {"tennant": "x"}}}, ORIGIN).block()


class TestJiraSite:
    @pytest.mark.parametrize(
        "case",
        [
            SiteCase("acme.atlassian.net", "https://acme.atlassian.net"),
            SiteCase("https://acme.atlassian.net/", "https://acme.atlassian.net"),
            SiteCase("https://acme.atlassian.net/jira/software/projects", "https://acme.atlassian.net"),
            SiteCase("  acme.atlassian.net  ", "https://acme.atlassian.net"),
            SiteCase("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
            SiteCase("", ""),
        ],
        ids=["host", "url", "url with a path", "spaces", "local http", "unset"],
    )
    def test_keeps_the_scheme_and_the_host(self, case: SiteCase) -> None:
        assert AuthTable({"auth": {"jira": {"site": case.written}}}, ORIGIN).block().jira.site == case.kept


class TestRunbookBlock:
    """G2: `companies.<company>.auth` of a runbook takes the same keys as `[auth]`."""

    def test_a_runbook_company_keeps_its_auth_block(self) -> None:
        runbook = RunbookFile.model_validate({"companies": {"acme": {"auth": {"jira": {"site": "acme.atlassian.net", "method": "cookie"}}}}})

        jira = runbook.companies["acme"].auth.jira
        assert (jira.site, jira.method.value) == ("https://acme.atlassian.net", "cookie")

    def test_a_bad_key_in_the_runbook_auth_block_stops_the_load(self) -> None:
        with pytest.raises(ValidationError, match=r"companies\.acme\.auth\.jira\.metod\n  Extra inputs are not permitted"):
            RunbookFile.model_validate({"companies": {"acme": {"auth": {"jira": {"metod": "token"}}}}})
