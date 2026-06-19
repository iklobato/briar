"""`briar init` generates a .briar.toml, inferring the repo from git."""

from __future__ import annotations

import argparse

import pytest

import briar.commands.init as init_mod
from briar.commands.init import CommandInit
from briar.errors import CliError


def _args(**over):
    base = dict(company="", store="file", owner="", repo="", path=".briar.toml", force=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_init_writes_config_inferring_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(init_mod, "_TEMPLATE", "briar.init_templates")
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: ("acme-co", "widgets"))
    code = CommandInit().run(_args(company="acme", store="postgres"))
    assert code == 0
    written = (tmp_path / ".briar.toml").read_text()
    assert 'company = "acme"' in written
    assert 'store   = "postgres"' in written
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
