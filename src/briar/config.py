"""Project config — `.briar.toml` (or `[tool.briar]` in pyproject.toml).

Most flags repeat the same stable values on every invocation: the
company, the store backend, the repo owner/slug, the agent model, the
git identity. This module lets a project declare them once so the
day-to-day command stops carrying them.

Resolution precedence, highest first:

    CLI flag  >  env var  >  project config  >  built-in default

The mechanism is deliberately boring: `apply_config_defaults` rewrites
each matching argparse action's `default` (and clears `required` when a
value is found) BEFORE parsing, so an explicit CLI flag still wins by the
normal argparse rule "an explicitly-passed option overrides its default".

Config layout (`.briar.toml` keys are top-level; in pyproject.toml they
live under `[tool.briar]`):

    company = "acme"
    store   = "postgres"
    root    = "./knowledge"
    tracker = "jira"

    [repo]
    owner    = "acme-co"
    repo     = "acme-app"
    provider = "github"

    [agent]
    model          = "claude-sonnet-4-6"
    git_user_name  = "acme-bot"
    git_user_email = "bot@acme.com"
"""

from __future__ import annotations

import argparse
import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_CONFIG_FILENAME = ".briar.toml"
_PYPROJECT = "pyproject.toml"


@dataclass(frozen=True)
class _ConfigSpec:
    """One config-satisfiable flag: the argparse dest it fills, the env
    var that overrides config, and the (section, key) path into the
    config dict. A top-level key uses section=""."""

    dest: str
    env: Optional[str]
    section: str
    key: str


# The flags a project config / env may satisfy. `storage` is extract's
# dest for the store backend (aliased --store); `store` covers every
# other command. `knowledge` is agent's file-root dest, fed by the same
# `root` config key.
_CONFIG_SPECS: Tuple[_ConfigSpec, ...] = (
    _ConfigSpec("company", "BRIAR_COMPANY", "", "company"),
    _ConfigSpec("store", "BRIAR_DEFAULT_STORE", "", "store"),
    _ConfigSpec("storage", "BRIAR_DEFAULT_STORE", "", "store"),
    _ConfigSpec("root", None, "", "root"),
    _ConfigSpec("knowledge", None, "", "root"),
    _ConfigSpec("tracker", None, "", "tracker"),
    _ConfigSpec("owner", None, "repo", "owner"),
    _ConfigSpec("repo", None, "repo", "repo"),
    _ConfigSpec("provider", None, "repo", "provider"),
    _ConfigSpec("model", None, "agent", "model"),
    _ConfigSpec("git_user_name", None, "agent", "git_user_name"),
    _ConfigSpec("git_user_email", None, "agent", "git_user_email"),
)


def find_config_file(start: Optional[Path] = None) -> Optional[Path]:
    """Nearest `.briar.toml` or pyproject.toml carrying `[tool.briar]`,
    searching from `start` (default cwd) up to the filesystem root.
    Returns None when neither is found."""
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        dedicated = directory / _CONFIG_FILENAME
        if dedicated.is_file():
            return dedicated
        pyproject = directory / _PYPROJECT
        if pyproject.is_file() and _has_tool_briar(pyproject):
            return pyproject
    return None


# Deduplicated, human-facing view of the settings a project may set,
# in display order. (label, env var or None, config section or "", key.)
_DISPLAY_SETTINGS: Tuple[Tuple[str, Optional[str], str, str], ...] = (
    ("company", "BRIAR_COMPANY", "", "company"),
    ("store", "BRIAR_DEFAULT_STORE", "", "store"),
    ("root", None, "", "root"),
    ("tracker", None, "", "tracker"),
    ("owner", None, "repo", "owner"),
    ("repo", None, "repo", "repo"),
    ("provider", None, "repo", "provider"),
    ("model", None, "agent", "model"),
    ("git_user_name", None, "agent", "git_user_name"),
    ("git_user_email", None, "agent", "git_user_email"),
)

_UNSET = "(unset)"


@dataclass(frozen=True)
class ResolvedSetting:
    """One setting's effective value and where it came from, for
    `briar config show`."""

    setting: str
    value: str
    source: str


def resolve_with_source(start: Optional[Path] = None) -> List[ResolvedSetting]:
    """Each project setting's effective value and its source, following the
    same precedence the CLI uses (env > project config > git inference for
    owner/repo > unset). Pure read — no parser, no side effects."""
    config = load_project_config(start)
    config_path = find_config_file(start)
    config_label = f"config ({config_path.name})" if config_path else "config"
    inferred = _inferred_owner_repo(start)
    rows: List[ResolvedSetting] = []
    for label, env, section, key in _DISPLAY_SETTINGS:
        rows.append(_resolve_one(label, env, section, key, config, config_label, inferred))
    return rows


def _inferred_owner_repo(start: Optional[Path]) -> Dict[str, str]:
    from briar.infer import git_remote_slug

    slug = git_remote_slug(str(start) if start else None)
    if slug is None:
        return {}
    owner, repo = slug
    return {"owner": owner, "repo": repo}


def _resolve_one(
    label: str,
    env: Optional[str],
    section: str,
    key: str,
    config: Dict[str, object],
    config_label: str,
    inferred: Dict[str, str],
) -> ResolvedSetting:
    if env:
        env_value = os.environ.get(env)
        if env_value:
            return ResolvedSetting(label, env_value, f"env ({env})")
    spec = _ConfigSpec(label, env, section, key)
    config_value = _config_value(config, spec)
    if config_value is not None:
        where = config_label if not section else f"{config_label} [{section}]"
        return ResolvedSetting(label, str(config_value), where)
    if label in inferred:
        return ResolvedSetting(label, inferred[label], "inferred (git origin)")
    return ResolvedSetting(label, _UNSET, "-")


def _has_tool_briar(pyproject: Path) -> bool:
    try:
        with pyproject.open("rb") as handle:
            return "briar" in tomllib.load(handle).get("tool", {})
    except (OSError, tomllib.TOMLDecodeError):
        return False


def load_project_config(start: Optional[Path] = None) -> Dict[str, object]:
    """The briar config section as a dict, or {} when no config file is
    found / it cannot be parsed. For `.briar.toml` the whole file is the
    section; for pyproject.toml it is `[tool.briar]`."""
    path = find_config_file(start)
    if path is None:
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("config: failed to read %s — ignoring (%s)", path, exc)
        return {}
    if path.name == _PYPROJECT:
        return dict(data.get("tool", {}).get("briar", {}))
    return dict(data)


def _config_value(config: Dict[str, object], spec: _ConfigSpec) -> Optional[object]:
    if spec.section:
        section = config.get(spec.section)
        if not isinstance(section, dict):
            return None
        return section.get(spec.key)
    return config.get(spec.key)


def _resolve(spec: _ConfigSpec, config: Dict[str, object]) -> Optional[object]:
    """env wins over config; None when neither provides a value."""
    if spec.env:
        env_value = os.environ.get(spec.env)
        if env_value:
            return env_value
    return _config_value(config, spec)


def _iter_subparsers(parser: argparse.ArgumentParser):
    """Yield every (sub)parser in the tree, including the root."""
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                yield from _iter_subparsers(sub)


def _derive_repo_slug(config: Dict[str, object]) -> Optional[str]:
    """`owner/repo` from the `[repo]` section, or None if incomplete."""
    section = config.get("repo")
    if not isinstance(section, dict):
        return None
    owner, repo = section.get("owner"), section.get("repo")
    if owner and repo:
        return f"{owner}/{repo}"
    return None


def _repo_list_value(config: Dict[str, object]) -> Optional[List[str]]:
    """Value for an append-style `--repo` (the canonical extract flag),
    which wants a list of `owner/repo` slugs: prefer an explicit `repos`
    array, else the single slug derived from `[repo]`."""
    repos = config.get("repos")
    if isinstance(repos, list) and repos:
        return [str(r) for r in repos]
    slug = _derive_repo_slug(config)
    return [slug] if slug else None


def _value_for_action(action: argparse.Action, config: Dict[str, object]) -> Optional[object]:
    """The config/env value appropriate to this action, or None when the
    config does not speak to it. Shape-aware: an append `--repo` gets a
    list of slugs, a scalar `--repo` gets the bare repo name."""
    if action.dest == "repo" and isinstance(action, argparse._AppendAction):
        return _repo_list_value(config)
    for spec in _CONFIG_SPECS:
        if spec.dest == action.dest:
            value = _resolve(spec, config)
            if value is not None:
                return value
    return None


def apply_config_defaults(
    parser: argparse.ArgumentParser,
    config: Optional[Dict[str, object]] = None,
) -> List[str]:
    """Fold env + project config into the parser as new defaults so a
    bare command inherits them and an explicit flag still overrides.

    Mutates matching actions' `default` and clears their `required` when
    a value is found. Returns the list of dests that were satisfied (for
    logging / tests)."""
    resolved = {} if config is None else config
    satisfied: List[str] = []
    for sub in _iter_subparsers(parser):
        for action in sub._actions:
            value = _value_for_action(action, resolved)
            if value is None:
                continue
            action.default = value
            action.required = False
            satisfied.append(action.dest)
    return satisfied
