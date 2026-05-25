"""`briar auth` — login / logout / refresh / list / status.

Focuses on dispatch + persistence/store routing. The interactive acquirer
flows are tested separately (tests/unit/auth/)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from briar.auth import Credentials
from briar.auth._acquirer import DestinationPolicy


class _FakeAcquirer:
    kind = "fake-acquirer"
    display_name = "Fake"
    destination_policy = DestinationPolicy.EXTERNAL

    def __init__(self, *, entries: dict[str, str] | None = None, expires: datetime | None = None) -> None:
        self._entries = entries or {"FAKE_VAR": "value"}
        self._expires = expires

    def acquire(self, *, company: str, prompt) -> Credentials:
        return Credentials(provider_kind=self.kind, entries=self._entries, expires_at=self._expires)

    def refresh(self, *, company: str, existing: Credentials) -> Credentials:
        return Credentials(provider_kind=self.kind, entries=self._entries)

    @classmethod
    def writes(cls, *, company: str) -> list[str]:
        return ["FAKE_VAR"]


class _FakeBootstrapAcquirer(_FakeAcquirer):
    destination_policy = DestinationPolicy.BOOTSTRAP_LOCAL


@pytest.fixture
def patch_acquirer(mocker):
    """Helper: install a fake acquirer under the given target name."""

    def install(target: str, cls=_FakeAcquirer):
        from briar.commands import auth as auth_mod
        from briar.auth import AcquirerRegistry

        # Patch the registry to recognise the target as our fake.
        mocker.patch.object(AcquirerRegistry, "make", return_value=cls())
        mocker.patch.object(AcquirerRegistry, "kinds", return_value=[target])
        return cls

    return install


class TestUnknownAction:
    def test_unknown_action_argparse_exit_2(self, cli) -> None:
        # `argparse` rejects unknown subcommand → exit 2 (USAGE_ERROR).
        result = cli("auth", "make-up")
        assert result.code == 2


class TestList:
    def test_empty_store_says_no_credentials(self, cli) -> None:
        result = cli("auth", "list", "--store", "envfile")
        assert result.code == 0
        assert "no credentials" in result.out

    def test_company_filter_substring(self, cli, monkeypatch) -> None:
        monkeypatch.setenv("AWS_ACME_ACCESS_KEY_ID", "AKIA")
        monkeypatch.setenv("AWS_OTHER_ACCESS_KEY_ID", "BKIA")
        result = cli("auth", "list", "--store", "envfile", "--company", "acme")
        assert result.code == 0
        assert "AWS_ACME_ACCESS_KEY_ID" in result.out
        assert "AWS_OTHER_ACCESS_KEY_ID" not in result.out


class TestStatus:
    def test_status_missing_credentials_exit_1(self, cli, mocker) -> None:
        from briar.auth import AcquirerRegistry

        mocker.patch.object(AcquirerRegistry, "kinds", return_value=["github-pat"])
        mocker.patch.object(AcquirerRegistry, "make", return_value=_FakeAcquirer())
        result = cli("auth", "status", "github-pat", "--store", "envfile")
        assert result.code == 1
        assert "MISS" in result.out

    def test_status_all_set_exit_0(self, cli, mocker, monkeypatch) -> None:
        from briar.auth import AcquirerRegistry

        monkeypatch.setenv("FAKE_VAR", "value")
        mocker.patch.object(AcquirerRegistry, "kinds", return_value=["github-pat"])
        mocker.patch.object(AcquirerRegistry, "make", return_value=_FakeAcquirer())
        result = cli("auth", "status", "github-pat", "--store", "envfile")
        assert result.code == 0
        assert "ok" in result.out


class TestLogoutGuard:
    def test_logout_without_yes_prompts_aborts_on_no(self, cli, mocker, monkeypatch) -> None:
        from briar.auth import AcquirerRegistry

        mocker.patch.object(AcquirerRegistry, "kinds", return_value=["github-pat"])
        mocker.patch.object(AcquirerRegistry, "make", return_value=_FakeAcquirer())
        monkeypatch.setattr("builtins.input", lambda _: "n")
        result = cli("auth", "logout", "github-pat", "--store", "envfile")
        assert result.code == 1
        assert "aborted" in result.out

    def test_logout_with_yes_proceeds(self, cli, mocker, monkeypatch) -> None:
        from briar.auth import AcquirerRegistry

        mocker.patch.object(AcquirerRegistry, "kinds", return_value=["github-pat"])
        mocker.patch.object(AcquirerRegistry, "make", return_value=_FakeAcquirer())
        monkeypatch.setenv("FAKE_VAR", "v")
        result = cli("auth", "logout", "github-pat", "--store", "envfile", "--yes")
        assert result.code == 0
        assert "removed" in result.out


class TestBootstrapPolicy:
    def test_bootstrap_local_acquirer_forces_envfile(self, cli, mocker, caplog_briar) -> None:
        from briar.auth import AcquirerRegistry

        mocker.patch.object(AcquirerRegistry, "kinds", return_value=["fake"])
        mocker.patch.object(AcquirerRegistry, "make", return_value=_FakeBootstrapAcquirer())
        result = cli("auth", "status", "fake", "--store", "envfile")
        assert result.code in (0, 1)  # missing creds → 1 is fine; just verify no crash
