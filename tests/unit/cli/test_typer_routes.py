"""The commands on Typer (spec 002 PR 2) get their choices from the registries and
their defaults from the project settings, as the 1.x argparse parser did."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, Optional, Tuple

import pytest

from briar.commands._enums import BootstrapKindChoice, CredentialStoreChoice, JournalStoreChoice, KnowledgeStoreChoice, OutputFormat
from briar.config import load_project_config
from briar.credentials import CredentialStoreRegistry
from briar.credentials._bootstraps import CredentialBootstrapRegistry
from briar.formatting import FORMATTERS
from briar.journal import JOURNAL_STORE_NAMES
from briar.storage import KNOWLEDGE_STORE_NAMES

USAGE_ERROR = 2


class FakeGitRemote:
    """Stands in for `git remote get-url origin`, already parsed to (owner, repo)."""

    def __init__(self, slug: Optional[Tuple[str, str]]) -> None:
        self._slug = slug

    def slug(self, cwd: Optional[str] = None) -> Optional[Tuple[str, str]]:
        return self._slug


@dataclass(frozen=True)
class Project:
    """A temp project directory the CLI runs in, with the real config reader."""

    root: Path
    monkeypatch: pytest.MonkeyPatch

    def write(self, text: str) -> None:
        (self.root / ".briar.toml").write_text(text)

    def git_origin(self, owner: str, repo: str) -> None:
        self.monkeypatch.setattr("briar.infer.git_remote_slug", FakeGitRemote((owner, repo)).slug)


@pytest.fixture
def project(cli: Callable[..., SimpleNamespace], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    """Runs after `cli`, which stubs config discovery; puts the real reader back."""
    monkeypatch.setattr("briar.config.load_project_config", load_project_config)
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path, monkeypatch)


class TestChoicesMatchTheRegistries:
    def test_output_formats_are_the_formatters(self) -> None:
        assert [choice.value for choice in OutputFormat] == list(FORMATTERS)

    def test_store_choices_are_the_knowledge_stores(self) -> None:
        assert [choice.value for choice in KnowledgeStoreChoice] == list(KNOWLEDGE_STORE_NAMES)

    def test_journal_store_choices_are_the_journal_stores(self) -> None:
        assert [choice.value for choice in JournalStoreChoice] == list(JOURNAL_STORE_NAMES)

    def test_cred_store_choices_are_the_credential_stores(self) -> None:
        assert [choice.value for choice in CredentialStoreChoice] == list(CredentialStoreRegistry.kinds())

    def test_bootstrap_kinds_are_the_bootstraps(self) -> None:
        assert [choice.value for choice in BootstrapKindChoice] == list(CredentialBootstrapRegistry.kinds())


class TestSettingsFillTyperOptions:
    def test_config_file_fills_init(self, cli: Callable[..., SimpleNamespace], project: Project, recorded_run: Dict[str, argparse.Namespace]) -> None:
        project.write('company = "acme"\nstore = "postgres"\n[repo]\nowner = "acme-co"\nrepo = "app"\n')

        run = cli("init")

        assert run.code == 0, run.err
        args = recorded_run["args"]
        assert (args.company, args.store, args.owner, args.repo) == ("acme", "postgres", "acme-co", "app")

    def test_flag_beats_the_config_file(self, cli: Callable[..., SimpleNamespace], project: Project, recorded_run: Dict[str, argparse.Namespace]) -> None:
        project.write('company = "from-file"\n')

        run = cli("init", "--company", "from-flag")

        assert run.code == 0, run.err
        assert recorded_run["args"].company == "from-flag"

    def test_env_beats_the_config_file(self, cli: Callable[..., SimpleNamespace], project: Project, recorded_run: Dict[str, argparse.Namespace]) -> None:
        project.write('company = "from-file"\n')

        run = cli("init", env={"BRIAR_COMPANY": "from-env"})

        assert run.code == 0, run.err
        assert recorded_run["args"].company == "from-env"

    def test_git_origin_fills_owner_and_repo(self, cli: Callable[..., SimpleNamespace], project: Project, recorded_run: Dict[str, argparse.Namespace]) -> None:
        project.git_origin("git-owner", "git-repo")

        run = cli("init")

        assert run.code == 0, run.err
        assert (recorded_run["args"].owner, recorded_run["args"].repo) == ("git-owner", "git-repo")


class TestGroupOptions:
    """`--store` / `--root` of `briar context` work before the operation, as in 1.x."""

    def test_group_flag_reaches_the_operation(self, cli: Callable[..., SimpleNamespace], recorded_run: Dict[str, argparse.Namespace]) -> None:
        run = cli("context", "--store", "postgres", "--root", "r", "get", "x")

        assert run.code == 0, run.err
        assert (recorded_run["args"].store, recorded_run["args"].root) == ("postgres", "r")

    def test_operation_flag_beats_the_group_flag(self, cli: Callable[..., SimpleNamespace], recorded_run: Dict[str, argparse.Namespace]) -> None:
        run = cli("context", "--store", "file", "get", "x", "--store", "postgres")

        assert run.code == 0, run.err
        assert recorded_run["args"].store == "postgres"

    def test_group_flag_beats_the_setting(self, cli: Callable[..., SimpleNamespace], project: Project, recorded_run: Dict[str, argparse.Namespace]) -> None:
        project.write('store = "file"\n')

        run = cli("context", "--store", "postgres", "list")

        assert run.code == 0, run.err
        assert recorded_run["args"].store == "postgres"

    def test_setting_fills_the_operation(self, cli: Callable[..., SimpleNamespace], project: Project, recorded_run: Dict[str, argparse.Namespace]) -> None:
        project.write('store = "postgres"\nroot = "kb"\n')

        run = cli("context", "list")

        assert run.code == 0, run.err
        assert (recorded_run["args"].store, recorded_run["args"].root) == ("postgres", "kb")


class TestRemovedAliases:
    def test_secrets_store_alias_is_an_unknown_option(self, cli: Callable[..., SimpleNamespace], recorded_run: Dict[str, argparse.Namespace]) -> None:
        """The 1.x alias of `--cred-store` (D29a); the other aliases go in spec 002 PR 6."""
        run = cli("secrets", "doctor", "--store", "envfile")

        assert run.code == USAGE_ERROR
        assert "No such option: --store" in run.err
        assert "args" not in recorded_run
