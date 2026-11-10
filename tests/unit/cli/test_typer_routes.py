"""The commands on Typer (spec 002 PR 2) get their choices from the registries and
their defaults from the project settings, as the 1.x argparse parser did."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, Optional, Tuple

import pytest

from briar.commands._enums import KnowledgeStoreChoice, OutputFormat
from briar.config import load_project_config
from briar.formatting import FORMATTERS
from briar.storage import KNOWLEDGE_STORE_NAMES


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
