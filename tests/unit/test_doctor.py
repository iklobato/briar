"""briar doctor health checks."""

from __future__ import annotations

from briar.doctor import OK, WARN, run_checks, worst_status


def _by_name(checks):
    return {c.name: c for c in checks}


def test_checks_ok_when_environment_is_set_up(monkeypatch, tmp_path):
    cfg = tmp_path / ".briar.toml"
    cfg.write_text('company = "acme"\n')
    monkeypatch.setattr("briar.config.find_config_file", lambda *a, **k: cfg)
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: ("acme", "app"))
    monkeypatch.setenv("GITHUB_TOKEN", "y")
    checks = _by_name(run_checks())
    assert "llm key" not in checks
    assert "store" not in checks  # the file store is the only one since 2.0
    assert checks["project config"].status == OK
    assert checks["git remote"].status == OK
    assert checks["github token"].status == OK
    assert worst_status(list(checks.values())) == OK


def test_missing_creds_and_config_warn(monkeypatch):
    monkeypatch.setattr("briar.config.find_config_file", lambda *a, **k: None)
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    checks = _by_name(run_checks())
    assert checks["project config"].status == WARN
    assert checks["git remote"].status == WARN
    assert checks["github token"].status == WARN
