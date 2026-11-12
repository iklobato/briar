"""Project settings and where each value came from (spec 002, decision D29b).

Order, highest first: env var > project config file > git `origin` (owner
and repo only) > the command's own default. The config file is the nearest
`.briar.toml`, or `pyproject.toml` with `[tool.briar]`, searched upward from
the cwd; one file is read, never a merge of two (`briar.config`).

Commands on Typer get these values as their Click default map, so an
explicit flag still wins and a value here satisfies a required option.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from pydantic import BaseModel, ConfigDict

UNSET_VALUE = "(unset)"
UNSET_SOURCE = "-"


@dataclass(frozen=True)
class SettingKey:
    """One setting: its name, the config table it lives in ("" for top level) and its env var."""

    name: str
    section: str = ""
    env: Optional[str] = None

    def read(self, values: Mapping[str, object]) -> Optional[object]:
        table = values.get(self.section) if self.section else values
        return table.get(self.name) if isinstance(table, Mapping) else None

    def table_suffix(self) -> str:
        return f" [{self.section}]" if self.section else ""


class SettingsLayer(Protocol):
    """One place a setting can come from."""

    def values(self) -> Mapping[str, object]: ...

    def label(self, key: SettingKey) -> str: ...


class EnvLayer:
    """The env vars named on the keys (1.x: only top-level ones); an empty value counts as unset."""

    def __init__(self, environ: Mapping[str, str], keys: Sequence[SettingKey]) -> None:
        self._environ = environ
        self._keys = keys

    def values(self) -> Mapping[str, object]:
        return {key.name: self._environ[key.env] for key in self._keys if key.env and self._environ.get(key.env)}

    def label(self, key: SettingKey) -> str:
        return f"env ({key.env})"


class ConfigFileLayer:
    """The project config file, already read into a dict (`briar.config.load_project_config`)."""

    def __init__(self, values: Mapping[str, object], path: Optional[Path]) -> None:
        self._values = values
        self._path = path

    def values(self) -> Mapping[str, object]:
        return self._values

    def label(self, key: SettingKey) -> str:
        name = f"config ({self._path.name})" if self._path else "config"
        return name + key.table_suffix()


class GitOriginLayer:
    """`git remote get-url origin`, already parsed to (owner, repo); fills only those two."""

    LABEL = "inferred (git origin)"

    def __init__(self, slug: Optional[Tuple[str, str]]) -> None:
        self._slug = slug

    def values(self) -> Mapping[str, object]:
        if self._slug is None:
            return {}
        owner, repo = self._slug
        return {"repo": {"owner": owner, "repo": repo}}

    def label(self, key: SettingKey) -> str:
        return self.LABEL


@dataclass(frozen=True)
class ResolvedSetting:
    """A setting's winning value (None when no layer set it) and the layer it came from."""

    key: SettingKey
    value: Optional[str]
    source: str

    def row(self) -> Dict[str, str]:
        """The `briar config show` row."""
        return {"setting": self.key.name, "value": UNSET_VALUE if self.value is None else self.value, "source": self.source}


class RepoSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner: Optional[str] = None
    repo: Optional[str] = None
    provider: Optional[str] = None


class BriarSettings(BaseModel):
    """The resolved settings. None means no layer set the value, so the command's own default applies."""

    model_config = ConfigDict(frozen=True)

    company: Optional[str] = None
    store: Optional[str] = None
    root: Optional[str] = None
    tracker: Optional[str] = None
    repo: RepoSettings = RepoSettings()


class SettingsResolver:
    """Picks each setting from the first layer that has it."""

    KEYS = (
        SettingKey("company", env="BRIAR_COMPANY"),
        SettingKey("store", env="BRIAR_STORE"),
        SettingKey("root"),
        SettingKey("tracker"),
        SettingKey("owner", "repo"),
        SettingKey("repo", "repo"),
        SettingKey("provider", "repo"),
    )

    def __init__(self, layers: Sequence[SettingsLayer]) -> None:
        self._layers = layers

    @classmethod
    def from_project(cls, project_config: Mapping[str, object]) -> SettingsResolver:
        """The real layers: this process's env, the project config file (already read, so a
        run reads it once), and the git origin remote."""
        from briar import config, infer

        file_layer = ConfigFileLayer(project_config, config.find_config_file())
        return cls((EnvLayer(os.environ, cls.KEYS), file_layer, GitOriginLayer(infer.git_remote_slug())))

    def resolved(self) -> List[ResolvedSetting]:
        """Every setting in display order, set or not."""
        return [self._resolve(key) for key in self.KEYS]

    def defaults(self) -> Dict[str, str]:
        """Setting name to value, for the settings some layer set."""
        return {item.key.name: item.value for item in self.resolved() if item.value is not None}

    def settings(self) -> BriarSettings:
        tables: Dict[str, Dict[str, str]] = {"": {}}
        for item in self.resolved():
            if item.value is not None:
                tables.setdefault(item.key.section, {})[item.key.name] = item.value
        top: Dict[str, object] = {**tables.pop(""), **tables}
        return BriarSettings.model_validate(top)

    def _resolve(self, key: SettingKey) -> ResolvedSetting:
        for layer in self._layers:
            value = key.read(layer.values())
            if value is not None:
                return ResolvedSetting(key, str(value), layer.label(key))
        return ResolvedSetting(key, None, UNSET_SOURCE)
