"""SettingsResolver: env > config file > git origin > unset, with the source of each value."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

import pytest

from briar.settings import ConfigFileLayer, EnvLayer, GitOriginLayer, SettingsResolver

BRIAR_TOML = Path("/project/.briar.toml")
PORT = 5432


class Layers:
    """Builds a resolver from plain values, so no env, file or git is touched."""

    def resolver(self, env: Mapping[str, str], config: Mapping[str, object], slug: Optional[Tuple[str, str]]) -> SettingsResolver:
        return SettingsResolver((EnvLayer(env, SettingsResolver.KEYS), ConfigFileLayer(config, BRIAR_TOML), GitOriginLayer(slug)))

    def rows(self, resolver: SettingsResolver) -> Dict[str, Tuple[str, str]]:
        return {item.row()["setting"]: (item.row()["value"], item.row()["source"]) for item in resolver.resolved()}


@pytest.fixture
def layers() -> Layers:
    return Layers()


class TestResolved:
    def test_env_beats_the_config_file(self, layers: Layers) -> None:
        rows = layers.rows(layers.resolver({"BRIAR_STORE": "postgres"}, {"store": "file"}, None))

        assert rows["store"] == ("postgres", "env (BRIAR_STORE)")

    def test_empty_env_var_counts_as_unset(self, layers: Layers) -> None:
        rows = layers.rows(layers.resolver({"BRIAR_COMPANY": ""}, {"company": "acme"}, None))

        assert rows["company"] == ("acme", "config (.briar.toml)")

    def test_a_table_key_names_its_table(self, layers: Layers) -> None:
        rows = layers.rows(layers.resolver({}, {"repo": {"owner": "acme-co"}}, None))

        assert rows["owner"] == ("acme-co", "config (.briar.toml) [repo]")

    def test_git_fills_only_what_the_file_left_empty(self, layers: Layers) -> None:
        rows = layers.rows(layers.resolver({}, {"repo": {"owner": "file-owner"}}, ("git-owner", "git-repo")))

        assert (rows["owner"], rows["repo"]) == (("file-owner", "config (.briar.toml) [repo]"), ("git-repo", "inferred (git origin)"))

    def test_nothing_set_shows_unset_and_a_dash(self, layers: Layers) -> None:
        rows = layers.rows(layers.resolver({}, {}, None))

        assert set(rows.values()) == {("(unset)", "-")}

    def test_a_non_text_value_is_shown_as_text(self, layers: Layers) -> None:
        rows = layers.rows(layers.resolver({}, {"root": PORT}, None))

        assert rows["root"] == ("5432", "config (.briar.toml)")


class TestDefaultsAndSettings:
    def test_defaults_hold_only_the_settings_some_layer_set(self, layers: Layers) -> None:
        resolver = layers.resolver({"BRIAR_COMPANY": "acme"}, {"agent": {"model": "m-1"}}, ("o", "r"))

        assert resolver.defaults() == {"company": "acme", "model": "m-1", "owner": "o", "repo": "r"}

    def test_settings_are_typed_by_table(self, layers: Layers) -> None:
        settings = layers.resolver({}, {"store": "postgres", "repo": {"provider": "bitbucket"}}, ("o", "r")).settings()

        assert (settings.store, settings.repo.owner, settings.repo.provider, settings.agent.model) == ("postgres", "o", "bitbucket", None)
