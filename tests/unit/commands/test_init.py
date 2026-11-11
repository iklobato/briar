"""`briar init` generates a .briar.toml, inferring the repo from git."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import briar.commands.init as init_mod
from briar.commands.init import CommandInit
from briar.errors import CliError


def _args(**over):
    base = dict(company="", owner="", repo="", path=".briar.toml", force=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_init_writes_config_inferring_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(init_mod, "_TEMPLATE", "briar.init_templates")
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: ("acme-co", "widgets"))
    code = CommandInit().run(_args(company="acme"))
    assert code == 0
    written = (tmp_path / ".briar.toml").read_text()
    assert 'company = "acme"' in written
    assert "store" not in written  # 2.0 has one knowledge store; the key is an error now
    assert 'owner = "acme-co"' in written
    assert 'repo  = "widgets"' in written


def test_init_explicit_owner_repo_beats_git(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: ("git-owner", "git-repo"))
    CommandInit().run(_args(owner="flag-owner", repo="flag-repo"))
    written = (tmp_path / ".briar.toml").read_text()
    assert 'owner = "flag-owner"' in written
    assert 'repo  = "flag-repo"' in written


def test_init_refuses_existing_without_force(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".briar.toml").write_text("existing")
    with pytest.raises(CliError, match="already exists"):
        CommandInit().run(_args())
    assert (tmp_path / ".briar.toml").read_text() == "existing"  # untouched


def test_init_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: None)
    (tmp_path / ".briar.toml").write_text("old")
    CommandInit().run(_args(company="acme", force=True))
    assert 'company = "acme"' in (tmp_path / ".briar.toml").read_text()


class TestExampleRunbook:
    """X3: `init` also writes `runbooks/<company>.yaml` for `briar serve`."""

    REQUIRED = ("model", "max_usd", "max_loops", "ci_wait_minutes", "max_review_rounds")

    def test_the_runbook_stops_until_the_model_and_limits_are_chosen(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from briar.iac.runbook import load_runbook_file

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: ("acme-co", "widgets"))
        CommandInit().run(_args(company="acme-labs"))
        runbook = tmp_path / "runbooks" / "acme-labs.yaml"

        with pytest.raises(CliError, match=r"companies\.acme-labs\.serve\.model\n  Field required"):
            load_runbook_file(runbook)
        assert "BRIAR_ACME_LABS_GITHUB_TOKEN" in runbook.read_text()

    def test_the_runbook_loads_once_the_required_lines_are_uncommented(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from briar.iac.runbook import load_runbook_file

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: ("acme-co", "widgets"))
        CommandInit().run(_args(company="acme"))
        runbook = tmp_path / "runbooks" / "acme.yaml"
        text = runbook.read_text()
        for key in self.REQUIRED:
            text = text.replace(f"      # {key}:", f"      {key}:")
        runbook.write_text(text)

        company = load_runbook_file(runbook).companies["acme"]

        assert (company.serve.repos, company.serve.model, company.schedules[0].extract[0].args) == (
            ["acme-co/widgets"],
            "claude-sonnet-4-5",
            {"pr_repo": ["acme-co/widgets"]},
        )

    def test_no_company_and_no_git_give_the_example_names(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: None)

        CommandInit().run(_args())

        text = (tmp_path / "runbooks" / "example.yaml").read_text()
        assert ("  example:\n" in text, "repos: [your-org/your-repo]" in text) == (True, True)

    def test_an_existing_runbook_stops_init_without_force(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "runbooks").mkdir()
        (tmp_path / "runbooks" / "acme.yaml").write_text("mine")

        with pytest.raises(CliError, match=r"^runbooks/acme\.yaml already exists: pass --force to overwrite$"):
            CommandInit().run(_args(company="acme"))

        assert ((tmp_path / "runbooks" / "acme.yaml").read_text(), (tmp_path / ".briar.toml").exists()) == ("mine", False)
