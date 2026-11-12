"""`briar extract` values from `.briar.toml` (decisions E1 to E16 of
docs/decisions/2026-11-10-briar-2-extract-config.md)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, Mapping

import pytest

from briar.commands.extract import ExtractConfig, ExtractKeyTable, ExtractorKeys
from briar.config import load_project_config
from briar.errors import CliError
from briar.extract import EXTRACTORS
from briar.extract.canonical import OptionConcept
from briar.settings import BriarSettings, RepoSettings

CLI_ERROR = 1
TICKET_MAX_DEFAULT = 100
SHARED_MAX = 7
OWN_MAX = 3
STALE_DAYS = 30
DOC = Path(__file__).resolve().parents[3] / "agents" / "extract.md"
DOC_BEGIN = "<!-- BEGIN extract-keys -->"
DOC_END = "<!-- END extract-keys -->"


@dataclass(frozen=True)
class ShortNameCase:
    """One short key, the value set in `[extract.<extractor>]`, and the option it must reach."""

    extractor: str
    key: str
    setting: object
    dest: str


class Run:
    """The namespace one extractor gets for a project config and settings."""

    def __init__(self, project_config: Mapping[str, object], settings: BriarSettings = BriarSettings()) -> None:
        self._project_config = project_config
        self._settings = settings

    def namespace(self, extractor: str) -> argparse.Namespace:
        config = self._config()
        config.check()
        return config.namespace_for(EXTRACTORS[extractor], argparse.Namespace(company="acme", include=[extractor], repo=[]))

    def error(self) -> str:
        with pytest.raises(CliError) as stopped:
            self._config().check()
        return str(stopped.value)

    def _config(self) -> ExtractConfig:
        return ExtractConfig(self._project_config, self._settings, Path(".briar.toml"))


class TestPrecedence:
    """Lowest first: the extractor default, the settings, `[extract]`, `[extract.<name>]` (E7)."""

    def test_no_config_keeps_the_extractor_defaults(self) -> None:
        namespace = Run({}).namespace("ticket-archaeology")

        assert (namespace.tracker, namespace.ticket_max) == ("jira", TICKET_MAX_DEFAULT)

    def test_the_tracker_setting_beats_the_default(self) -> None:
        assert Run({}, BriarSettings(tracker="linear")).namespace("ticket-archaeology").tracker == "linear"

    def test_the_repo_provider_setting_feeds_repo_extractors(self) -> None:
        settings = BriarSettings(repo=RepoSettings(provider="bitbucket"))

        assert Run({}, settings).namespace("pr-archaeology").provider == "bitbucket"

    def test_the_shared_table_beats_the_settings(self) -> None:
        run = Run({"extract": {"tracker": "github-issues"}}, BriarSettings(tracker="linear"))

        assert run.namespace("ticket-archaeology").tracker == "github-issues"

    def test_the_extractor_table_beats_the_shared_table(self) -> None:
        run = Run({"extract": {"tracker": "github-issues", "max": SHARED_MAX, "ticket-archaeology": {"tracker": "linear", "max": OWN_MAX}}})

        namespace = run.namespace("ticket-archaeology")

        assert (namespace.tracker, namespace.ticket_max) == ("linear", OWN_MAX)

    def test_a_shared_concept_reaches_every_extractor_that_has_it(self) -> None:
        run = Run({"extract": {"max": SHARED_MAX}})

        assert (run.namespace("ticket-archaeology").ticket_max, run.namespace("code-hotspots").hotspots_max_commits) == (SHARED_MAX, SHARED_MAX)

    @pytest.mark.parametrize(
        "case",
        [
            ShortNameCase("ticket-archaeology", "projects", ["ACME"], "ticket_archaeology_project"),
            ShortNameCase("aws-infra", "services", ["ecs"], "aws_extract_service"),
            ShortNameCase("aws-infra", "region", "eu-west-1", "aws_extract_region"),
            ShortNameCase("stale-prs", "days", STALE_DAYS, "stale_days"),
        ],
        ids=["projects", "services", "region", "days"],
    )
    def test_a_short_name_sets_its_option(self, case: ShortNameCase) -> None:
        namespace = Run({"extract": {case.extractor: {case.key: case.setting}}}).namespace(case.extractor)

        assert getattr(namespace, case.dest) == case.setting


class TestStops:
    """A key, name or value `briar extract` does not know stops the run, naming it (E5, E9, E10)."""

    @pytest.mark.parametrize(
        ("project_config", "message"),
        [
            ({"extract": {"foo": 1}}, "`extract.foo` in .briar.toml: not a `briar extract` setting"),
            ({"extract": {"repo": ["a/b"]}}, "`extract.repo` in .briar.toml: the repo list comes from --repo"),
            ({"extract": {"nope": {"max": 1}}}, "`extract.nope` in .briar.toml: not an extractor name"),
            ({"extract": {"pr-archaeology": {"pr_max": 5}}}, "`extract.pr-archaeology.pr_max` in .briar.toml: that is the 1.x name; use `max`"),
            ({"extract": {"pr-archaeology": {"pr_repo": ["a/b"]}}}, "`extract.pr-archaeology.pr_repo` in .briar.toml: the repo list comes from --repo"),
            ({"extract": {"pr-archaeology": {"repo": ["a/b"]}}}, "`extract.pr-archaeology.repo` in .briar.toml: the repo list comes from --repo"),
            ({"extract": {"pr-archaeology": {"bogus": 1}}}, "`extract.pr-archaeology.bogus` in .briar.toml: not a `briar extract` setting"),
            ({"extract": {"provider": "gitlab"}}, "`extract.provider` in .briar.toml: 'gitlab' is not one of github, bitbucket"),
            ({"extract": {"aws-infra": {"services": ["ecs", "s3"]}}}, "`extract.aws-infra.services` in .briar.toml: 's3' is not one of"),
        ],
        ids=["shared key", "shared repo", "extractor name", "1.x name", "1.x repo name", "own repo", "own key", "shared choice", "list choice"],
    )
    def test_names_what_to_fix(self, project_config: Dict[str, object], message: str) -> None:
        assert Run(project_config).error().startswith(message)


class TestOnTheCommandLine:
    @pytest.fixture
    def project(self, cli: Callable[..., SimpleNamespace], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Runs after `cli`, which stubs the project config reader; puts the real one back."""
        monkeypatch.setattr("briar.config.load_project_config", load_project_config)
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_a_table_of_an_extractor_that_does_not_run_is_still_checked(self, cli: Callable[..., SimpleNamespace], project: Path) -> None:
        (project / ".briar.toml").write_text("[extract.stale-prs]\nbogus = 1\n")

        run = cli("extract", "--company", "acme", "--include", "pr-archaeology")

        assert run.code == CLI_ERROR
        assert "`extract.stale-prs.bogus`" in run.err

    def test_help_lists_every_extractor_key(self, cli: Callable[..., SimpleNamespace]) -> None:
        run = cli("extract", "-h")

        assert "pr-archaeology: assignees_allow=[]" in run.out
        assert 'aws-infra: cloud="aws" (aws, gcp, azure), region=""' in run.out
        assert "ticket-archaeology: max=100, projects=[]" in run.out


class TestKeyNames:
    @pytest.mark.parametrize("name", sorted(EXTRACTORS))
    def test_every_option_has_a_short_name_except_repo_lists(self, name: str) -> None:
        """A new option with no concept and no NICHE entry would be unreachable from `.briar.toml`."""
        keys = ExtractorKeys(EXTRACTORS[name])

        unnamed = [dest for dest in keys.dests() if not keys.short_name(dest) and OptionConcept(dest).concept() != ExtractorKeys.REPO]

        assert unnamed == []

    def test_the_manual_has_the_table_the_code_builds(self) -> None:
        page = DOC.read_text()

        assert page.split(DOC_BEGIN)[1].split(DOC_END)[0].strip() == ExtractKeyTable(EXTRACTORS).markdown()
