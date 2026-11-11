"""briar doctor health checks (the login lines: spec 005 G14, G15, J10 to J12).

The login lines check `FakeProbe` adapters (`fake_sources`): no network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

import pytest

from briar.doctor import FAIL, OK, WARN, AgentSandboxCheck, Check, RunbookChecks, run_checks, worst_status
from tests.unit.fake_logins import FakeProbe, Fixes

FAKE_SECRET = "PLACEHOLDER-not-a-secret"


class FoundConfig:
    """Stands in for `briar.config.find_config_file`: always finds this file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def find(self, start: Optional[Path] = None) -> Path:
        return self._path


class NoConfig:
    """Stands in for `briar.config.find_config_file`: finds nothing."""

    def find(self, start: Optional[Path] = None) -> Optional[Path]:
        return None


class Project:
    """A `.briar.toml` in `tmp_path`, found by doctor, with a git origin."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._monkeypatch = monkeypatch
        self._tmp_path = tmp_path

    def write(self, text: str) -> Dict[str, Check]:
        config = self._tmp_path / ".briar.toml"
        config.write_text(text)
        self._monkeypatch.setattr("briar.config.find_config_file", FoundConfig(config).find)
        self._monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: ("acme", "app"))
        return {check.name: check for check in run_checks()}


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_sources: Any) -> Project:
    # The host of the test may lack bwrap; TestAgentSandbox checks that line.
    monkeypatch.setattr("briar.serve.work.AgentSandbox.problem", lambda sandbox: "")
    return Project(monkeypatch, tmp_path)


class TestOfflineChecks:
    def test_checks_ok_when_environment_is_set_up(self, project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_ACME_GITHUB_TOKEN", FAKE_SECRET)
        monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_SECRET)

        checks = project.write('company = "acme"\n')

        assert ("llm key" in checks, "store" in checks) == (False, False)
        assert [checks[name].status for name in ("project config", "git remote", "model login", "github login")] == [OK, OK, OK, OK]
        assert checks["model login"].detail == "ANTHROPIC_API_KEY set"
        assert checks["github login"].detail == "github (acme): bot@example.test"
        assert worst_status(list(checks.values())) == OK

    def test_no_config_and_no_company_warn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("briar.config.find_config_file", NoConfig().find)
        monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: None)

        checks = {check.name: check for check in run_checks()}

        assert [checks[name].status for name in ("project config", "git remote", "model login", "logins")] == [WARN, WARN, WARN, WARN]
        assert checks["model login"].detail == (
            "no model login: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (in the environment or secrets.env); only `briar serve` needs it"
        )
        assert checks["logins"].detail == "no company: set `company` in .briar.toml to check its logins"


class TestAgentSandbox:
    """V14: the line names what `briar serve` needs on this system."""

    def test_missing_programs_warn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda program: None)

        (line,) = AgentSandboxCheck().checks()

        expected = "no agent sandbox: install bwrap (package bubblewrap) and socat; briar serve runs the bash of the agent in it; only `briar serve` needs it"
        assert (line.name, line.status, line.detail) == ("agent sandbox", WARN, expected)

    def test_found_programs_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda program: f"/usr/bin/{program}")

        assert [(line.status, line.detail) for line in AgentSandboxCheck().checks()] == [(OK, "bwrap and socat found")]


class Folders:
    """A folder of runbooks for `doctor --runbooks` with one company `acme`."""

    SERVE = "    serve:\n      model: claude-test\n      max_usd: 5\n      max_loops: 3\n      ci_wait_minutes: 30\n      max_review_rounds: 2\n      repos: [acme/app]\n"

    def __init__(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path

    def write(self, company_yaml: str) -> Path:
        folder = self._tmp_path / "runbooks"
        folder.mkdir(exist_ok=True)
        (folder / "acme.yaml").write_text(f"version: 1\ncompanies:\n  acme:\n{company_yaml}")
        return folder


class TestRunbookChecks:
    """X2: `doctor --runbooks DIR` runs the checks `serve DIR` runs, without serving."""

    def test_the_serve_logins_of_each_company_are_lines(self, tmp_path: Path) -> None:
        folder = Folders(tmp_path).write(Folders.SERVE)

        lines = [(check.name, check.status, check.detail) for check in RunbookChecks(folder).checks()]

        assert lines == [
            ("runbooks", OK, f"{folder}: 1 companies with a `serve:` block"),
            ("github login", FAIL, f"github (acme): no login saved (BRIAR_ACME_GITHUB_TOKEN); {Fixes().github()}"),
        ]

    def test_a_login_both_checks_need_is_one_line(self, tmp_path: Path) -> None:
        """A scheduled GitHub extract and the pipeline check the same GitHub login."""
        schedule = '    schedules:\n      - task: daily\n        every: "day at 03:17"\n        extract:\n          - name: pr-archaeology\n            args: {pr_repo: [acme/app]}\n'
        folder = Folders(tmp_path).write(schedule + Folders.SERVE)

        names = [check.name for check in RunbookChecks(folder).checks()]

        assert names == ["runbooks", "github login"]

    def test_a_runbook_that_does_not_load_fails_with_the_key(self, tmp_path: Path) -> None:
        folder = Folders(tmp_path).write(Folders.SERVE.replace("      model: claude-test\n", ""))

        (line,) = RunbookChecks(folder).checks()

        assert (line.name, line.status, "companies.acme.serve.model" in line.detail) == ("runbooks", FAIL, True)

    def test_the_option_reaches_doctor(self, cli: Callable[..., SimpleNamespace], tmp_path: Path) -> None:
        run = cli("--format", "json", "doctor", "--runbooks", str(tmp_path / "missing"))

        checks = {check["check"]: check for check in json.loads(run.out)["checks"]}
        assert (run.code, checks["runbooks"]["status"], checks["runbooks"]["detail"]) == (1, FAIL, f"{tmp_path / 'missing'} is not a directory")


class TestLoginLines:
    """J10: every configured source; J12: no secret is `fail`; G15: network is `warn`."""

    def test_a_block_with_no_secret_fails_with_the_login_to_run(self, project: Project) -> None:
        checks = project.write('company = "acme"\n[auth.jira]\nsite = "acme.atlassian.net"\n')

        jira = checks["jira login"]
        assert (jira.status, jira.detail) == (
            FAIL,
            f"jira (acme): no login saved (BRIAR_ACME_JIRA_EMAIL, BRIAR_ACME_JIRA_TOKEN); {Fixes().jira()}",
        )

    def test_a_1x_github_token_fails_with_the_new_place(self, project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
        """G25: briar no longer reads $GITHUB_TOKEN; doctor says where the token goes now."""
        monkeypatch.setenv("GITHUB_TOKEN", "fake-1x-token")

        github = project.write('company = "acme"\n')["github login"]

        assert (github.status, github.detail) == (
            FAIL,
            'github (acme): `GITHUB_TOKEN` is a 1.x name: use `BRIAR_ACME_GITHUB_TOKEN` with `method = "token"`',
        )

    def test_a_network_error_warns(self, project: Project, monkeypatch: pytest.MonkeyPatch, fake_sources: Any) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)
        fake_sources.probe(FakeProbe("fireflies", unreachable="no connection"))

        fireflies = project.write('company = "acme"\n')["fireflies login"]

        assert (fireflies.status, fireflies.detail) == (WARN, "fireflies (acme): could not reach it (no connection)")

    def test_a_refused_login_fails(self, project: Project, monkeypatch: pytest.MonkeyPatch, fake_sources: Any) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)
        fake_sources.probe(FakeProbe("fireflies", refused="HTTP 401: Unauthorized"))

        checks = project.write('company = "acme"\n')

        assert (checks["fireflies login"].status, worst_status(list(checks.values()))) == (FAIL, FAIL)

    def test_nothing_configured_warns(self, project: Project) -> None:
        logins = project.write('company = "acme"\n')["logins"]

        assert (logins.status, logins.detail) == (
            WARN,
            "no login configured for acme: set its BRIAR_* secrets in the environment or in secrets.env (the README lists them)",
        )

    def test_a_bad_extract_config_fails(self, project: Project) -> None:
        logins = project.write('company = "acme"\n[extract]\nnope = 1\n')["logins"]

        assert logins.status == FAIL
        assert logins.detail.startswith("`extract.nope` in ")
