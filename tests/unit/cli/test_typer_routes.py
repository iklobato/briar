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
    BootstrapKindChoice,
    CredentialStoreChoice,
    JournalStoreChoice,
    KnowledgeStoreChoice,
    LlmChoice,
    MeetingChoice,
    OutputFormat,
    ProviderChoice,
    TrackerChoice,
)
from briar.agent._llms import LLMRegistry
from briar.config import load_project_config
from briar.credentials import CredentialStoreRegistry
from briar.credentials._bootstraps import CredentialBootstrapRegistry
from briar.extract._meetings import meeting_kinds
from briar.extract._providers import RepositoryProviderRegistry
from briar.extract._trackers import TrackerRegistry
from briar.formatting import FORMATTERS
from briar.journal import JOURNAL_STORE_NAMES
from briar.storage import KNOWLEDGE_STORE_NAMES

from .conftest import Tripwire

USAGE_ERROR = 2
IMPLEMENT = ("agent", "implement", "--ticket-key", "T-1")
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

    def test_store_choices_are_the_knowledge_stores(self) -> None:
        assert [choice.value for choice in KnowledgeStoreChoice] == list(KNOWLEDGE_STORE_NAMES)

    def test_journal_store_choices_are_the_journal_stores(self) -> None:
        assert [choice.value for choice in JournalStoreChoice] == list(JOURNAL_STORE_NAMES)

    def test_cred_store_choices_are_the_credential_stores(self) -> None:
        assert [choice.value for choice in CredentialStoreChoice] == list(CredentialStoreRegistry.kinds())

    def test_bootstrap_kinds_are_the_bootstraps(self) -> None:
        assert [choice.value for choice in BootstrapKindChoice] == list(CredentialBootstrapRegistry.kinds())

    def test_provider_choices_are_the_providers(self) -> None:
        assert [choice.value for choice in ProviderChoice] == list(RepositoryProviderRegistry.kinds())

    def test_tracker_choices_are_the_trackers(self) -> None:
        assert [choice.value for choice in TrackerChoice] == list(TrackerRegistry.kinds())

    def test_meeting_choices_are_the_meeting_sources(self) -> None:
        assert [choice.value for choice in MeetingChoice] == list(meeting_kinds())

    def test_llm_choices_are_the_llm_providers(self) -> None:
        assert [choice.value for choice in LlmChoice] == list(LLMRegistry.kinds())


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


class TestAgentDefaults:
    def test_root_setting_fills_the_agent_file_store_root(
        self, cli: Callable[..., SimpleNamespace], project: Project, recorded_run: Dict[str, argparse.Namespace]
    ) -> None:
        """`agent --root` has the 1.x dest `knowledge`; the `root` setting still fills it."""
        project.write('root = "kb"\n')

        run = cli(*IMPLEMENT)

        assert run.code == 0, run.err
        assert recorded_run["args"].knowledge == "kb"

    @pytest.mark.parametrize("url_and_store", [("", "file"), ("postgres://db.invalid/briar", "postgres")], ids=["no url", "url"])
    def test_store_default_follows_the_database_url(
        self, cli: Callable[..., SimpleNamespace], recorded_run: Dict[str, argparse.Namespace], url_and_store: Tuple[str, str]
    ) -> None:
        """Read when the command runs, as 1.x did, so an env file loaded at startup counts."""
        database_url, store = url_and_store

        run = cli(*IMPLEMENT, env={"BRIAR_DATABASE_URL": database_url})

        assert run.code == 0, run.err
        assert recorded_run["args"].store == store


class TestPlanDefaults:
    def test_settings_fill_plan_run(self, cli: Callable[..., SimpleNamespace], project: Project, recorded_run: Dict[str, argparse.Namespace]) -> None:
        project.write('company = "acme"\nroot = "kb"\ntracker = "jira"\n[agent]\nmodel = "m-1"\n')

        run = cli("plan", "run", "p", "--llm", "anthropic")

        assert run.code == 0, run.err
        args = recorded_run["args"]
        assert (args.company, args.root, args.tracker, args.model) == ("acme", "kb", "jira", "m-1")


class TestRemovedAliases:
    """The 1.x hidden aliases (D29a)."""

    @pytest.mark.parametrize(
        "argv",
        [("secrets", "doctor", "--store", "envfile"), ("auth", "list", "--store", "envfile"), IMPLEMENT + ("--knowledge", "kb")],
        ids=["secrets --store", "auth --store", "agent --knowledge"],
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
    EXPECTED = {"extract", "runbook", "context", "agent", "auth", "plan", "secrets", "journal", "telemetry", "version", "init", "config", "doctor"}

    def test_help_lists_the_13_commands(self, cli: Callable[..., SimpleNamespace]) -> None:
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
        run = cli(env={"_BRIAR_COMPLETE": "complete_bash", "COMP_WORDS": "briar agent implement --tic", "COMP_CWORD": "3"})

        assert (run.code, tripwire.calls) == (0, [])
        assert run.out.split() == ["--ticket-project", "--ticket-key"]
