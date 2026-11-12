"""briar doctor health checks."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from briar.doctor import OK, WARN, run_checks, worst_status


def _by_name(checks):
    return {c.name: c for c in checks}


def test_checks_ok_when_environment_is_set_up(monkeypatch, tmp_path):
    cfg = tmp_path / ".briar.toml"
    cfg.write_text('company = "acme"\n')
    monkeypatch.setattr("briar.config.find_config_file", lambda *a, **k: cfg)
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: ("acme", "app"))
    monkeypatch.setenv("BRIAR_ACME_GITHUB_TOKEN", "PLACEHOLDER-not-a-secret")
    checks = _by_name(run_checks())
    assert "llm key" not in checks
    assert "store" not in checks  # the file store is the only one since 2.0
    assert checks["project config"].status == OK
    assert checks["git remote"].status == OK
    assert checks["github login"].status == OK
    assert worst_status(list(checks.values())) == OK


def test_missing_creds_and_config_warn(monkeypatch):
    monkeypatch.setattr("briar.config.find_config_file", lambda *a, **k: None)
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    checks = _by_name(run_checks())
    assert checks["project config"].status == WARN
    assert checks["git remote"].status == WARN
    assert checks["github login"].status == WARN


class FoundConfig:
    """Stands in for `briar.config.find_config_file`: always finds this file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def find(self, start: Optional[Path] = None) -> Path:
        return self._path


class TestGithubLogin:
    def test_a_1x_github_token_gets_the_new_place(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """G25: briar no longer reads $GITHUB_TOKEN; doctor says where the token goes now."""
        config = tmp_path / ".briar.toml"
        config.write_text('company = "acme"\n')
        monkeypatch.setattr("briar.config.find_config_file", FoundConfig(config).find)
        monkeypatch.setenv("GITHUB_TOKEN", "fake-1x-token")
        monkeypatch.delenv("BRIAR_ACME_GITHUB_TOKEN", raising=False)

        github = _by_name(run_checks())["github login"]

        assert (github.status, github.detail) == (
            WARN,
            '`GITHUB_TOKEN` is a 1.x name: use `BRIAR_ACME_GITHUB_TOKEN` with `method = "token"`. Or run `briar auth login github --company acme`',
        )
