"""`briar serve DIR` (spec 003 PR 1): what stops it before the loop, and the loop it starts.
The runbooks are real files in `tmp_path`; the loop itself and the network are replaced."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, List

import pytest

from briar.serve.loop import ServeLoop
from briar.serve.work import ServeCompanies, ServeLock
from tests.unit.fake_logins import Fixes

CLI_ERROR = 1
SERVE_BLOCK = "    serve:\n      model: claude-test\n      max_usd: 5\n      max_loops: 3\n      ci_wait_minutes: 30\n      max_review_rounds: 2\n      repos: [acme/app]\n"


class Runbooks:
    """A folder with one runbook for company `acme`."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def write(self, company_yaml: str) -> Path:
        self.folder.mkdir(exist_ok=True)
        (self.folder / "acme.yaml").write_text(f"version: 1\ncompanies:\n  acme:\n{company_yaml}")
        return self.folder


@pytest.fixture
def runbooks(tmp_path: Path) -> Runbooks:
    return Runbooks(tmp_path / "runbooks")


@pytest.fixture(autouse=True)
def sandbox_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """The host of the test may lack bwrap; `test_no_sandbox_stops_before_the_loop` takes it away."""
    monkeypatch.setattr("briar.serve.work.AgentSandbox.problem", lambda sandbox: "")


class TestBeforeTheLoop:
    def test_not_a_folder_stops(self, cli: Callable[..., SimpleNamespace], tmp_path: Path) -> None:
        run = cli("serve", str(tmp_path / "missing"))

        assert (run.code, run.err.strip().splitlines()[-1]) == (CLI_ERROR, f"error: serve: {tmp_path / 'missing'} is not a directory")

    def test_a_folder_with_nothing_to_serve_stops(self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks) -> None:
        folder = runbooks.write("    extract: []\n")

        run = cli("serve", str(folder))

        assert run.err.strip().splitlines()[-1] == f"error: serve: nothing to serve in {folder}: no company has a `serve:` block or a schedule"

    def test_a_serve_block_without_the_model_stops_the_load(self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks) -> None:
        folder = runbooks.write(SERVE_BLOCK.replace("      model: claude-test\n", ""))

        run = cli("serve", str(folder))

        assert run.code == CLI_ERROR
        assert "companies.acme.serve.model" in run.err

    def test_no_model_login_stops_before_the_loop(self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks) -> None:
        run = cli("serve", str(runbooks.write(SERVE_BLOCK)))

        assert run.err.strip().splitlines()[-1] == "error: no model login: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (in the environment or secrets.env)"

    def test_no_sandbox_stops_before_the_loop(self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "PLACEHOLDER-not-a-secret")
        monkeypatch.setattr("briar.serve.work.AgentSandbox.problem", lambda sandbox: "no agent sandbox: install socat")

        run = cli("serve", str(runbooks.write(SERVE_BLOCK)))

        assert (run.code, run.err.strip().splitlines()[-1]) == (CLI_ERROR, "error: no agent sandbox: install socat")

    def test_a_missing_github_login_stops_before_the_loop(
        self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "PLACEHOLDER-not-a-secret")

        run = cli("serve", str(runbooks.write(SERVE_BLOCK)))

        assert run.code == CLI_ERROR
        assert f"  - github (acme): no login saved (BRIAR_ACME_GITHUB_TOKEN); {Fixes().github()}" in run.err


class TestTheLoop:
    def test_a_passing_check_starts_the_loop(self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks, monkeypatch: pytest.MonkeyPatch) -> None:
        started: List[str] = []
        monkeypatch.setenv("ANTHROPIC_API_KEY", "PLACEHOLDER-not-a-secret")
        monkeypatch.setattr("briar.serve.work.ServeCheck.require", lambda check: None)
        monkeypatch.setattr(ServeLoop, "serve_forever", lambda loop: started.append("serve_forever"))

        run = cli("serve", str(runbooks.write(SERVE_BLOCK)))

        assert (run.code, started) == (0, ["serve_forever"])

    def test_a_second_serve_on_the_same_knowledge_stops(self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks, monkeypatch: pytest.MonkeyPatch) -> None:
        """Real run, 2026-11-13: two `serve` on one folder took the same issue; the second
        collided on `git worktree add` and opened a duplicate PR."""
        started: List[str] = []
        monkeypatch.chdir(runbooks.folder.parent)
        folder = runbooks.write(SERVE_BLOCK)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "PLACEHOLDER-not-a-secret")
        monkeypatch.setattr("briar.serve.work.ServeCheck.require", lambda check: None)
        monkeypatch.setattr(ServeLoop, "serve_forever", lambda loop: started.append("serve_forever"))
        [company] = ServeCompanies(folder, os.environ).load()
        running = ServeLock(company)
        running.take()

        run = cli("serve", str(folder))
        running.release()

        assert (run.code, started) == (CLI_ERROR, [])
        assert "another `briar serve` runs on the company acme" in run.err

    def test_a_folder_with_only_schedules_runs_the_extracts(
        self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """X1: what `briar runbook serve` did; no model login, no sandbox, no pipeline check."""
        started: List[str] = []
        monkeypatch.setattr("briar.iac.runbook.RunbookScheduler.check_logins", lambda scheduler: None)
        monkeypatch.setattr("briar.serve.work.ServeCheck.require", lambda check: started.append("pipeline check"))
        monkeypatch.setattr(ServeLoop, "serve_forever", lambda loop: started.append("serve_forever"))
        schedule = '    schedules:\n      - task: daily\n        every: "day at 03:17"\n        extract:\n          - name: pr-archaeology\n'

        run = cli("serve", str(runbooks.write(schedule)))

        assert (run.code, started) == (0, ["serve_forever"])
