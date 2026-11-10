"""Where a setting's value comes from, as 1.x resolves it (spec 002, T002).

Order, highest first: flag > env var > `.briar.toml` > `[tool.briar]` in
`pyproject.toml` > git `origin` (owner and repo only) > built-in default.
Each case sets the layer under test to a winning value and every lower layer
to a losing one, so the layer only passes if it beats all of them.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from briar.config import load_project_config

RECORDED_COMMAND = ("init",)
USAGE_ERROR = 2


class Layer(Enum):
    """The sources, in precedence order (first wins)."""

    FLAG = "flag"
    ENV = "env"
    BRIAR_TOML = "briar_toml"
    PYPROJECT = "pyproject"
    GIT = "git"


@dataclass(frozen=True)
class Setting:
    """One config-satisfiable setting and how each layer spells it."""

    dest: str
    env: Optional[str]
    section: str
    git_slot: Optional[int]
    winner: str
    loser: str
    default: Any

    def layers(self) -> List[Layer]:
        skipped = {Layer.ENV} if self.env is None else set()
        skipped |= {Layer.GIT} if self.git_slot is None else set()
        return [layer for layer in Layer if layer not in skipped]

    def flag(self) -> str:
        return "--" + self.dest.replace("_", "-")

    def toml(self, header: str, value: str) -> str:
        table = ".".join(part for part in (header, self.section) if part)
        return f'[{table}]\n{self.dest} = "{value}"\n' if table else f'{self.dest} = "{value}"\n'

    def source_label(self, layer: Optional[Layer]) -> str:
        suffix = f" [{self.section}]" if self.section else ""
        labels = {
            Layer.ENV: f"env ({self.env})",
            Layer.BRIAR_TOML: f"config (.briar.toml){suffix}",
            Layer.PYPROJECT: f"config (pyproject.toml){suffix}",
            Layer.GIT: "inferred (git origin)",
        }
        return labels.get(layer, "-")


@dataclass(frozen=True)
class PrecedenceCase:
    """A setting with `top` as the highest layer present."""

    setting: Setting
    top: Layer


class SettingTable:
    """The settings spec 002 must keep resolving the 1.x way."""

    SETTINGS = (
        Setting("company", "BRIAR_COMPANY", "", None, "won-company", "lost-company", ""),
        Setting("store", "BRIAR_STORE", "", None, "postgres", "file", "file"),
        Setting("owner", None, "repo", 0, "won-owner", "lost-owner", ""),
        Setting("repo", None, "repo", 1, "won-repo", "lost-repo", ""),
    )

    def cases(self) -> List[PrecedenceCase]:
        return [PrecedenceCase(setting, layer) for setting in self.SETTINGS for layer in setting.layers()]

    def file_cases(self) -> List[PrecedenceCase]:
        return [case for case in self.cases() if case.top is not Layer.FLAG]

    def case_id(self, value: Any) -> str:
        if isinstance(value, PrecedenceCase):
            return f"{value.setting.dest}-{value.top.value}"
        return value.dest


class FakeGitRemote:
    """Stands in for `git remote get-url origin`, already parsed to (owner, repo)."""

    def __init__(self, slug: Tuple[str, str]) -> None:
        self._slug = slug

    def slug(self, cwd: Optional[str] = None) -> Tuple[str, str]:
        return self._slug


@dataclass(frozen=True)
class Sandbox:
    """The temp directory the CLI runs in and the patcher that undoes every change."""

    root: Path
    monkeypatch: pytest.MonkeyPatch


class Project:
    """Runs the CLI in a sandbox with one setting spread over layers."""

    def __init__(self, sandbox: Sandbox, cli: Callable[..., SimpleNamespace], recorded: Dict[str, argparse.Namespace]) -> None:
        self._sandbox = sandbox
        self._cli = cli
        self._recorded = recorded

    def stack(self, case: PrecedenceCase) -> List[str]:
        """Give `case.top` the winning value and every layer below it the losing one;
        returns the argv tokens the flag layer needs."""
        layers = case.setting.layers()
        argv: List[str] = []
        for layer in layers[layers.index(case.top) :]:
            argv += self._put(case.setting, layer, case.setting.winner if layer is case.top else case.setting.loser)
        return argv

    def write(self, relative: str, text: str) -> None:
        target = self._sandbox.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def move_to(self, relative: str) -> None:
        self._sandbox.monkeypatch.chdir(self._sandbox.root / relative)

    def invoke_briar(self, argv: Tuple[str, ...]) -> SimpleNamespace:
        return self._cli(*argv)

    def recorded(self, dest: str) -> Any:
        return getattr(self._recorded["args"], dest)

    def ran(self) -> bool:
        return "args" in self._recorded

    def _put(self, setting: Setting, layer: Layer, value: str) -> List[str]:
        if layer is Layer.FLAG:
            return [setting.flag(), value]
        if layer is Layer.ENV:
            self._sandbox.monkeypatch.setenv(setting.env, value)
        if layer is Layer.BRIAR_TOML:
            self.write(".briar.toml", setting.toml("", value))
        if layer is Layer.PYPROJECT:
            self.write("pyproject.toml", setting.toml("tool.briar", value))
        if layer is Layer.GIT:
            slug = ["git-owner", "git-repo"]
            slug[setting.git_slot] = value
            self._sandbox.monkeypatch.setattr("briar.infer.git_remote_slug", FakeGitRemote((slug[0], slug[1])).slug)
        return []


@pytest.fixture
def sandbox(cli: Callable[..., SimpleNamespace], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Sandbox:
    """Runs after `cli`, which stubs config discovery; puts the real reader back."""
    monkeypatch.setattr("briar.config.load_project_config", load_project_config)
    monkeypatch.chdir(tmp_path)
    return Sandbox(tmp_path, monkeypatch)


@pytest.fixture
def project(sandbox: Sandbox, cli: Callable[..., SimpleNamespace], recorded_run: Dict[str, argparse.Namespace]) -> Project:
    """Every command's `run()` only records the namespace it gets."""
    return Project(sandbox, cli, recorded_run)


@pytest.fixture
def real_project(sandbox: Sandbox, cli: Callable[..., SimpleNamespace]) -> Project:
    """Commands really run (for `config show`); nothing is recorded."""
    return Project(sandbox, cli, {})


class TestPrecedence:
    @pytest.mark.parametrize("case", SettingTable().cases(), ids=SettingTable().case_id)
    def test_layer_beats_every_layer_below_it(self, project: Project, case: PrecedenceCase) -> None:
        argv = project.stack(case)

        run = project.invoke_briar(RECORDED_COMMAND + tuple(argv))

        assert run.code == 0, run.err
        assert project.recorded(case.setting.dest) == case.setting.winner

    @pytest.mark.parametrize("setting", SettingTable.SETTINGS, ids=SettingTable().case_id)
    def test_no_layer_leaves_the_built_in_default(self, project: Project, setting: Setting) -> None:
        run = project.invoke_briar(RECORDED_COMMAND)

        assert run.code == 0, run.err
        assert project.recorded(setting.dest) == setting.default

    def test_briar_toml_hides_pyproject_in_the_same_directory(self, project: Project) -> None:
        """One file is read, never a merge: a key only in pyproject.toml is ignored."""
        project.write(".briar.toml", 'company = "from-briar-toml"\n')
        project.write("pyproject.toml", '[tool.briar]\nstore = "postgres"\n')

        run = project.invoke_briar(RECORDED_COMMAND)

        assert run.code == 0, run.err
        assert (project.recorded("company"), project.recorded("store")) == ("from-briar-toml", "file")

    def test_nearest_config_file_wins_over_one_further_up(self, project: Project) -> None:
        project.write(".briar.toml", 'company = "from-parent"\n')
        project.write("child/pyproject.toml", '[tool.briar]\ncompany = "from-child"\n')
        project.move_to("child")

        run = project.invoke_briar(RECORDED_COMMAND)

        assert run.code == 0, run.err
        assert project.recorded("company") == "from-child"


class TestRequiredCompany:
    def test_extract_without_company_stops_with_usage_error(self, project: Project) -> None:
        run = project.invoke_briar(("extract",))

        assert run.code == USAGE_ERROR
        assert "the following arguments are required: --company" in run.err
        assert not project.ran()

    def test_config_file_company_satisfies_the_required_flag(self, project: Project) -> None:
        project.write(".briar.toml", 'company = "acme"\n')

        run = project.invoke_briar(("extract",))

        assert run.code == 0, run.err
        assert project.recorded("company") == "acme"


class TestConfigShow:
    def _row(self, real_project: Project, dest: str) -> Dict[str, str]:
        run = real_project.invoke_briar(("config", "show", "--format", "json"))
        assert run.code == 0, run.err
        return next(row for row in json.loads(run.out) if row["setting"] == dest)

    @pytest.mark.parametrize("case", SettingTable().file_cases(), ids=SettingTable().case_id)
    def test_names_the_layer_that_won(self, real_project: Project, case: PrecedenceCase) -> None:
        real_project.stack(case)

        row = self._row(real_project, case.setting.dest)

        assert (row["value"], row["source"]) == (case.setting.winner, case.setting.source_label(case.top))

    @pytest.mark.parametrize("setting", SettingTable.SETTINGS, ids=SettingTable().case_id)
    def test_unset_setting_shows_a_dash(self, real_project: Project, setting: Setting) -> None:
        row = self._row(real_project, setting.dest)

        assert (row["value"], row["source"]) == ("(unset)", "-")
