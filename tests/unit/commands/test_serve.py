"""`briar serve DIR` (spec 003 PR 1): what stops it before the loop, and the loop it starts.
The runbooks are real files in `tmp_path`; the loop itself and the network are replaced."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable, List

import pytest

from briar.serve.loop import ServeLoop

CLI_ERROR = 1
SERVE_BLOCK = "    serve:\n      model: claude-test\n      max_usd: 5\n      max_loops: 3\n      ci_wait_minutes: 30\n      repos: [acme/app]\n"


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


class TestBeforeTheLoop:
    def test_not_a_folder_stops(self, cli: Callable[..., SimpleNamespace], tmp_path: Path) -> None:
        run = cli("serve", str(tmp_path / "missing"))

        assert (run.code, run.err.strip().splitlines()[-1]) == (CLI_ERROR, f"error: serve: {tmp_path / 'missing'} is not a directory")

    def test_no_company_with_a_serve_block_stops(self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks) -> None:
        folder = runbooks.write("    extract: []\n")

        run = cli("serve", str(folder))

        assert run.err.strip().splitlines()[-1] == f"error: serve: no runbook in {folder} has a company with a `serve:` block"

    def test_a_serve_block_without_the_model_stops_the_load(self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks) -> None:
        folder = runbooks.write(SERVE_BLOCK.replace("      model: claude-test\n", ""))

        run = cli("serve", str(folder))

        assert run.code == CLI_ERROR
        assert "companies.acme.serve.model" in run.err

    def test_no_model_login_stops_before_the_loop(self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks) -> None:
        run = cli("serve", str(runbooks.write(SERVE_BLOCK)))

        assert run.err.strip().splitlines()[-1] == "error: no model login: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (in the environment or secrets.env)"

    def test_a_missing_github_login_stops_before_the_loop(
        self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "PLACEHOLDER-not-a-secret")

        run = cli("serve", str(runbooks.write(SERVE_BLOCK)))

        assert run.code == CLI_ERROR
        assert "  - github (acme): no login saved (BRIAR_ACME_GITHUB_TOKEN); run `briar auth login github --company acme --runbook " in run.err


class TestTheLoop:
    def test_a_passing_check_starts_the_loop(self, cli: Callable[..., SimpleNamespace], runbooks: Runbooks, monkeypatch: pytest.MonkeyPatch) -> None:
        started: List[str] = []
        monkeypatch.setenv("ANTHROPIC_API_KEY", "PLACEHOLDER-not-a-secret")
        monkeypatch.setattr("briar.serve.work.ServeCheck.require", lambda check: None)
        monkeypatch.setattr(ServeLoop, "serve_forever", lambda loop: started.append("serve_forever"))

        run = cli("serve", str(runbooks.write(SERVE_BLOCK)))

        assert (run.code, started) == (0, ["serve_forever"])
