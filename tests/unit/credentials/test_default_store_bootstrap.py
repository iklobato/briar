"""Startup loads keys saved with `auth login --cred-store <remote>`.

Up to 1.1.91 only the envfile was loaded at startup, so keys saved to
`ssm`, `aws-secretsmanager` or `vault` never reached other commands.
`BRIAR_DEFAULT_STORE` (the store `auth login` writes to by default) now
also names the store loaded at startup."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import pytest

from briar.credentials._bootstraps import BOOTSTRAPS, auto_bootstrap
from briar.credentials._bootstraps.default_store import DefaultStoreBootstrap


class _FakeStore:
    kind = "ssm"

    def __init__(self, values: Dict[str, str], *, fail: Optional[Exception] = None) -> None:
        self._values = values
        self._fail = fail

    def list(self) -> List[str]:
        if self._fail is not None:
            raise self._fail
        return list(self._values)

    def read(self, name: str) -> Optional[str]:
        return self._values.get(name)


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv("BRIAR_DEFAULT_STORE", "ssm")
    monkeypatch.setenv("BRIAR_SECRETS_FILE", "/nonexistent/briar-secrets.env")
    holder = {"store": _FakeStore({"GITHUB_TOKEN": "from-ssm", "LINEAR_ACME_API_KEY": "lin"})}
    monkeypatch.setattr("briar.credentials._bootstraps.default_store.make_credential_store", lambda kind: holder["store"])
    for name in ("GITHUB_TOKEN", "LINEAR_ACME_API_KEY", "PYTHONPATH_SENTINEL"):
        monkeypatch.delenv(name, raising=False)
    return holder


def test_registered_after_envfile() -> None:
    assert list(BOOTSTRAPS)[:2] == ["envfile", "default-store"]


def test_remote_default_store_is_loaded_at_startup(remote) -> None:
    results = auto_bootstrap()

    assert [r.backend for r in results] == ["default-store"]
    assert os.environ["GITHUB_TOKEN"] == "from-ssm"
    assert os.environ["LINEAR_ACME_API_KEY"] == "lin"


def test_shell_value_wins(remote, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from-shell")

    result = DefaultStoreBootstrap().hydrate()

    assert os.environ["GITHUB_TOKEN"] == "from-shell"
    assert result.skipped == ["GITHUB_TOKEN"]


def test_denied_names_are_never_loaded(remote, monkeypatch) -> None:
    monkeypatch.delenv("LD_PRELOAD", raising=False)
    remote["store"] = _FakeStore({"LD_PRELOAD": "/tmp/evil.so"})

    DefaultStoreBootstrap().hydrate()

    assert "LD_PRELOAD" not in os.environ


def test_store_failure_is_reported_not_raised(remote) -> None:
    remote["store"] = _FakeStore({}, fail=RuntimeError("AccessDenied"))

    result = DefaultStoreBootstrap().hydrate()

    assert not result.ok
    assert "AccessDenied" in result.error


@pytest.mark.parametrize("value", ["", "envfile", "no-such-store"])
def test_not_available_for_local_or_unknown_store(monkeypatch, value) -> None:
    monkeypatch.setenv("BRIAR_DEFAULT_STORE", value)

    assert DefaultStoreBootstrap().is_available() is False


def test_dry_run_writes_nothing(remote) -> None:
    result = DefaultStoreBootstrap().hydrate(dry_run=True)

    assert sorted(result.written) == ["GITHUB_TOKEN", "LINEAR_ACME_API_KEY"]
    assert "GITHUB_TOKEN" not in os.environ
