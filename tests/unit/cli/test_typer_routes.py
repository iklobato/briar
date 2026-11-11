"""The commands on Typer (spec 002 PR 2) get their choices from the registries and
their defaults from the project settings, as the 1.x argparse parser did."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, Optional, Tuple

import pytest

from briar.commands._enums import (
    OutputFormat,
)
from briar.config import load_project_config
from briar.formatting import FORMATTERS

from .conftest import Tripwire

USAGE_ERROR = 2
ALIAS_POSITION = -2  # each removed-alias line ends with the alias and its value


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


class TestSettingsFillTyperOptions:
    def test_config_file_fills_init(self, cli: Callable[..., SimpleNamespace], project: Project, recorded_run: Dict[str, argparse.Namespace]) -> None:
        project.write('company = "acme"\n[repo]\nowner = "acme-co"\nrepo = "app"\n')

        run = cli("init")

        assert run.code == 0, run.err
        args = recorded_run["args"]
        assert (args.company, args.owner, args.repo) == ("acme", "acme-co", "app")

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


class TestRemovedAliases:
    """The 1.x hidden aliases (D29a)."""

    @pytest.mark.parametrize(
        "argv",
        [("serve", "d", "--tick", "5")],
        ids=["serve --tick (C14, was on runbook serve)"],
    )
    def test_alias_is_an_unknown_option(self, cli: Callable[..., SimpleNamespace], recorded_run: Dict[str, argparse.Namespace], argv: Tuple[str, ...]) -> None:
        alias = argv[ALIAS_POSITION]

        run = cli(*argv)

        assert run.code == USAGE_ERROR
        assert f"No such option: {alias}" in run.err
        assert "args" not in recorded_run

    def test_extract_storage_alias_is_an_unknown_argument(self, cli: Callable[..., SimpleNamespace], recorded_run: Dict[str, argparse.Namespace]) -> None:
        """`extract` is still on argparse (spec 004), so the wording is argparse's."""
        run = cli("extract", "--company", "acme", "--storage", "file")

        assert run.code == USAGE_ERROR
        assert "unrecognized arguments: --storage" in run.err
        assert "args" not in recorded_run


class TestCommandList:
    EXPECTED = {"extract", "serve", "init", "doctor"}

    def test_help_lists_the_4_commands(self, cli: Callable[..., SimpleNamespace]) -> None:
        listed = cli("--help").out.split("Commands:")[1].split()

        assert {word for word in listed if word in self.EXPECTED | {"completion"}} == self.EXPECTED

    def test_completion_command_is_gone(self, cli: Callable[..., SimpleNamespace]) -> None:
        run = cli("completion", "bash")

        assert run.code == USAGE_ERROR
        assert "No such command 'completion'" in run.err


class TestShellCompletion:
    """Typer's own completion replaces `briar completion` (D29a)."""

    def test_show_completion_prints_a_script_without_startup_io(self, cli: Callable[..., SimpleNamespace], tripwire: Tripwire) -> None:
        run = cli("--show-completion", "bash", env={"_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION": "1"})

        assert (run.code, tripwire.calls) == (0, [])
        assert "_BRIAR_COMPLETE=complete_bash" in run.out

    def test_tab_completes_the_options_of_a_command(self, cli: Callable[..., SimpleNamespace], tripwire: Tripwire) -> None:
        """A TAB press runs briar with no arguments and the words in the environment."""
        run = cli(env={"_BRIAR_COMPLETE": "complete_bash", "COMP_WORDS": "briar doctor --", "COMP_CWORD": "2"})

        assert (run.code, tripwire.calls) == (0, [])
        assert run.out.split() == ["--runbooks", "--help"]
