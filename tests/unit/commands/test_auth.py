"""`briar auth`: login / status (decision C15: logout, refresh, list and --cred-store left).

Focuses on dispatch + persistence/store routing. The interactive acquirer
flows are tested separately (tests/unit/auth/)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Callable

import pytest

from briar.auth import Credentials


class _FakeAcquirer:
    kind = "fake-acquirer"
    display_name = "Fake"

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


class TestUnknownAction:
    def test_unknown_action_argparse_exit_2(self, cli) -> None:
        # `argparse` rejects unknown subcommand → exit 2 (USAGE_ERROR).
        result = cli("auth", "make-up")
        assert result.code == 2


class TestStatusWithoutTarget:
    """`auth status` with no target lists every credential name held, as 1.x `auth list` did."""

    def test_empty_store_says_no_credentials(self, cli: Callable[..., SimpleNamespace]) -> None:
        run = cli("auth", "status")
        assert run.code == 0
        assert "no credentials" in run.out

    def test_company_filter_substring(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_ACME_ACCESS_KEY_ID", "AKIA")
        monkeypatch.setenv("AWS_OTHER_ACCESS_KEY_ID", "BKIA")
        run = cli("auth", "status", "--company", "acme")
        assert run.code == 0
        assert "AWS_ACME_ACCESS_KEY_ID" in run.out
        assert "AWS_OTHER_ACCESS_KEY_ID" not in run.out

    def test_names_only_never_values(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_ACME_ACCESS_KEY_ID", "VALUE-PLACEHOLDER-not-a-secret")
        run = cli("auth", "status")
        assert "AWS_ACME_ACCESS_KEY_ID" in run.out
        assert "VALUE-PLACEHOLDER-not-a-secret" not in run.out


class TestStatus:
    def test_status_missing_credentials_exit_1(self, cli, mocker) -> None:
        from briar.auth import AcquirerRegistry

        mocker.patch.object(AcquirerRegistry, "kinds", return_value=["github-pat"])
        mocker.patch.object(AcquirerRegistry, "make", return_value=_FakeAcquirer())
        result = cli("auth", "status", "github-pat")
        assert result.code == 1
        assert "MISS" in result.out

    def test_status_all_set_exit_0(self, cli, mocker, monkeypatch) -> None:
        from briar.auth import AcquirerRegistry

        monkeypatch.setenv("FAKE_VAR", "value")
        mocker.patch.object(AcquirerRegistry, "kinds", return_value=["github-pat"])
        mocker.patch.object(AcquirerRegistry, "make", return_value=_FakeAcquirer())
        result = cli("auth", "status", "github-pat")
        assert result.code == 0
        assert "ok" in result.out
