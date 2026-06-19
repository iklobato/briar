"""briar doctor health checks."""

from __future__ import annotations

import os

import briar.doctor as doctor
from briar.doctor import FAIL, OK, WARN, run_checks, worst_status


def _by_name(checks):
    return {c.name: c for c in checks}


def _clear_dsn(monkeypatch):
    monkeypatch.delenv("BRIAR_DATABASE_URL", raising=False)
    for key in list(os.environ):
        if key.startswith("BRIAR_") and key.endswith("_DATABASE_URL"):
            monkeypatch.delenv(key, raising=False)


def test_checks_ok_when_environment_is_set_up(monkeypatch, tmp_path):
    cfg = tmp_path / ".briar.toml"
    cfg.write_text('store = "file"\n')
    monkeypatch.setattr("briar.config.find_config_file", lambda *a, **k: cfg)
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: ("acme", "app"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("GITHUB_TOKEN", "y")
    monkeypatch.delenv("BRIAR_DEFAULT_STORE", raising=False)
    checks = _by_name(run_checks())
    assert checks["project config"].status == OK
    assert checks["git remote"].status == OK
    assert checks["llm key"].status == OK
    assert checks["github token"].status == OK
    assert checks["store"].status == OK
    assert worst_status(list(checks.values())) == OK


def test_missing_creds_and_config_warn(monkeypatch):
    monkeypatch.setattr("briar.config.find_config_file", lambda *a, **k: None)
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    checks = _by_name(run_checks())
    assert checks["project config"].status == WARN
    assert checks["git remote"].status == WARN
    assert checks["llm key"].status == WARN
    assert checks["github token"].status == WARN


def test_postgres_without_dsn_is_a_hard_fail(monkeypatch):
    # Drive the real _store_check through a mocked resolution → FAIL, and
    # confirm run_checks() aggregates the worst status.
    monkeypatch.setattr("briar.config.resolve_with_source", lambda *a, **k: [_setting("store", "postgres")])
    _clear_dsn(monkeypatch)
    assert worst_status(run_checks()) == FAIL


def test_store_check_postgres_dsn(monkeypatch):
    monkeypatch.setattr("briar.config.resolve_with_source", lambda *a, **k: [_setting("store", "postgres")])
    _clear_dsn(monkeypatch)
    assert doctor._store_check().status == FAIL
    monkeypatch.setenv("BRIAR_DATABASE_URL", "postgres://x")
    assert doctor._store_check().status == OK


def _setting(name, value):
    from briar.config import ResolvedSetting

    return ResolvedSetting(name, value, "config")
