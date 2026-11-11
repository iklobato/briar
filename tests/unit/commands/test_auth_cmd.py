"""`briar auth login`: persistence.

Complements tests/unit/commands/test_auth.py (which covers status and
dispatch). Here we exercise the login tail through
``_persist_and_report``: that credential VALUES are written to the store
but never printed, write-failures surface as exit 1, and expired
credentials map to exit 2.

The credential store is a mocked collaborator (source read-denied by
policy); the acquirer is a local fake. No network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from briar.auth import CredentialExpired, Credentials

_SECRET_PLACEHOLDER = "ACCESS-TOKEN-PLACEHOLDER-not-a-secret"


class _FakeAcquirer:
    kind = "fake-acquirer"
    display_name = "Fake acquirer"

    def __init__(self, *, entries=None, expires=None, acquire_exc=None, refresh_exc=None) -> None:
        self._entries = entries if entries is not None else {"GITHUB_ACME_TOKEN": _SECRET_PLACEHOLDER}
        self._expires = expires
        self._acquire_exc = acquire_exc
        self._refresh_exc = refresh_exc

    def acquire(self, *, company, prompt) -> Credentials:
        if self._acquire_exc:
            raise self._acquire_exc
        return Credentials(provider_kind=self.kind, entries=self._entries, expires_at=self._expires)

    def refresh(self, *, company, existing) -> Credentials:
        if self._refresh_exc:
            raise self._refresh_exc
        return Credentials(provider_kind=self.kind, entries=self._entries, expires_at=self._expires)

    @classmethod
    def writes(cls, *, company) -> list[str]:
        return ["GITHUB_ACME_TOKEN"]


class _FakeStore:
    """Records writes/reads/deletes; raises on names registered to fail."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []
        self.values: dict[str, str] = {}
        self.fail_writes: set[str] = set()

    def write(self, name: str, value: str) -> None:
        if name in self.fail_writes:
            raise OSError("disk full")
        self.writes.append((name, value))
        self.values[name] = value

    def read(self, name: str) -> str:
        return self.values.get(name, "")


@pytest.fixture
def install(mocker):
    """Register a fake acquirer + fake store at the command's seams."""

    store = _FakeStore()

    captured = {}

    def _install(acquirer_cls=_FakeAcquirer, *, acquirer=None):
        from briar.auth import AcquirerRegistry

        inst = acquirer or acquirer_cls()
        mocker.patch.object(AcquirerRegistry, "make", return_value=inst)
        mocker.patch.object(AcquirerRegistry, "kinds", return_value=[inst.kind])
        captured["make"] = mocker.patch("briar.commands.auth.EnvFileStore", return_value=store)
        return store, inst

    _install.captured = captured
    return _install


# ─────────────────────────── login ─────────────────────────────────


class TestLogin:
    def test_login_persists_value_to_store_exit_0(self, cli, install) -> None:
        store, _ = install()
        result = cli("auth", "login", "fake-acquirer", "--company", "acme")
        assert result.code == 0
        # The VALUE reached the store...
        assert store.writes == [("GITHUB_ACME_TOKEN", _SECRET_PLACEHOLDER)]
        # ...but only NAMES + counts are printed; the value is never echoed.
        assert "persisted 1/1 entries" in result.out
        assert "GITHUB_ACME_TOKEN" in result.out
        assert _SECRET_PLACEHOLDER not in result.out

    def test_login_write_failure_exit_1_and_reports_reason(self, cli, install) -> None:
        store, _ = install()
        store.fail_writes = {"GITHUB_ACME_TOKEN"}
        result = cli("auth", "login", "fake-acquirer", "--company", "acme")
        assert result.code == 1
        assert "persisted 0/1 entries" in result.out
        assert "FAIL  GITHUB_ACME_TOKEN" in result.out
        assert "reason: GITHUB_ACME_TOKEN: disk full" in result.out
        assert _SECRET_PLACEHOLDER not in result.out

    def test_login_prints_expiry_when_present(self, cli, install) -> None:
        expires = datetime.now(tz=timezone.utc) + timedelta(days=30)
        store, _ = install(acquirer=_FakeAcquirer(expires=expires))
        result = cli("auth", "login", "fake-acquirer", "--company", "acme")
        assert result.code == 0
        assert f"expires: {expires.isoformat()}" in result.out
        # "+N.N days from now" — positive sign for a future expiry.
        assert "days from now)" in result.out
        assert "(+" in result.out

    def test_login_acquire_raises_credential_expired_exit_2(self, cli, install) -> None:
        store, _ = install(acquirer=_FakeAcquirer(acquire_exc=CredentialExpired("token lapsed")))
        result = cli("auth", "login", "fake-acquirer", "--company", "acme")
        assert result.code == 2
        assert "credential expired: token lapsed" in result.out
        assert store.writes == []
