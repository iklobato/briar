"""Git-remote inference for --owner/--repo, and its precedence (below config)."""

from __future__ import annotations

import argparse

import pytest

import briar.infer as infer
from briar.infer import apply_inference_defaults, git_remote_slug


@pytest.mark.parametrize(
    "url,expected",
    [
        ("git@github.com:acme-co/acme-app.git", ("acme-co", "acme-app")),
        ("git@github.com:acme-co/acme-app", ("acme-co", "acme-app")),
        ("https://github.com/acme-co/acme-app.git", ("acme-co", "acme-app")),
        ("https://bitbucket.org/acme-co/acme-app", ("acme-co", "acme-app")),
    ],
)
def test_git_remote_slug_parses(monkeypatch, url, expected):
    def fake_run(*a, **k):
        return argparse.Namespace(returncode=0, stdout=url + "\n", stderr="")

    monkeypatch.setattr(infer.subprocess, "run", fake_run)
    assert git_remote_slug() == expected


def test_git_remote_slug_none_when_no_origin(monkeypatch):
    def fake_run(*a, **k):
        return argparse.Namespace(returncode=128, stdout="", stderr="no origin")

    monkeypatch.setattr(infer.subprocess, "run", fake_run)
    assert git_remote_slug() is None


def test_git_remote_slug_none_on_oserror(monkeypatch):
    def boom(*a, **k):
        raise OSError("git not found")

    monkeypatch.setattr(infer.subprocess, "run", boom)
    assert git_remote_slug() is None


def _slug(monkeypatch, owner="acme-co", repo="acme-app"):
    monkeypatch.setattr(infer, "git_remote_slug", lambda cwd=None: (owner, repo))


def test_inference_fills_scalar_owner_repo(monkeypatch):
    _slug(monkeypatch)
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    filled = apply_inference_defaults(parser, already_satisfied=[])
    assert set(filled) == {"owner", "repo"}
    args = parser.parse_args([])
    assert (args.owner, args.repo) == ("acme-co", "acme-app")


def test_inference_fills_append_repo_with_full_slug(monkeypatch):
    _slug(monkeypatch)
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", action="append", default=[])
    apply_inference_defaults(parser, already_satisfied=[])
    assert parser.parse_args([]).repo == ["acme-co/acme-app"]


def test_config_satisfied_dests_block_inference(monkeypatch):
    _slug(monkeypatch, owner="git-owner", repo="git-repo")
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="config-owner")
    parser.add_argument("--repo", default="config-repo")
    filled = apply_inference_defaults(parser, already_satisfied=["owner", "repo"])
    assert filled == []  # config already won; inference must not override
    args = parser.parse_args([])
    assert (args.owner, args.repo) == ("config-owner", "config-repo")


def test_no_git_means_no_change(monkeypatch):
    monkeypatch.setattr(infer, "git_remote_slug", lambda cwd=None: None)
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    assert apply_inference_defaults(parser, already_satisfied=[]) == []
