"""`briar auth` — PARAMETRIC flag-effect coverage.

Companion to test_auth.py / test_auth_cmd.py (dispatch + persistence
happy/unhappy paths). This file asserts the *observable effect* of every
option of the two operations 2.0 keeps (decision C15):

  login    target (positional, choices) · --company
  status   [target] · --company   (no target: every credential name held)

For each flag we assert the value reaches the seam (the acquirer call or
``make_credential_store(kind)``) or changes the rendered output — a
swapped/dropped/negated flag must make a test FAIL. Secret VALUES are
never echoed. The acquirer + credential store are local fakes; no
network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

import pytest

from briar.auth import Credentials

# Obvious placeholder — never a real-format secret (GitGuardian-safe).
_SECRET_PLACEHOLDER = "ACCESS-TOKEN-PLACEHOLDER-not-a-secret"

# Documented choices from the manifest (2.0 removed the remote stores).
_STORE_CHOICES = ["envfile"]
REMOVED_STORES = ("aws-secretsmanager", "ssm", "vault")
_TARGET_CHOICES = [
    "github-device",
    "github-pat",
    "bitbucket-app-password",
    "aws-static",
    "aws-sso",
    "jira-token",
    "jira-session",
    "linear-api-key",
    "fireflies",
]


def _make_acquirer_cls(kind: str, company_seen: list):
    """Build a fresh acquirer class whose ``writes`` classmethod records
    the company it was called with into the shared ``company_seen`` list.

    The command code calls ``type(acquirer).writes(company=...)`` off the
    *class*, so recording must
    live on a class bound to this test's ``company_seen`` list — a single
    shared class would leak state across parametrized cases."""

    class _FakeAcquirer:
        display_name = "Fake"

        def __init__(self) -> None:
            self.kind = kind

        def acquire(self, *, company, prompt) -> Credentials:
            company_seen.append(("acquire", company))
            return Credentials(provider_kind=self.kind, entries={"GITHUB_TOKEN": _SECRET_PLACEHOLDER})

        def refresh(self, *, company, existing) -> Credentials:
            company_seen.append(("refresh", company))
            return Credentials(provider_kind=self.kind, entries={"GITHUB_TOKEN": _SECRET_PLACEHOLDER})

        @classmethod
        def writes(cls, *, company) -> list:
            company_seen.append(("writes", company))
            return [f"GITHUB_{company.upper()}_TOKEN" if company else "GITHUB_TOKEN"]

    _FakeAcquirer.kind = kind
    return _FakeAcquirer


class _FakeStore:
    """Records every read/write/delete/list against a credential store."""

    def __init__(self, *, present: dict | None = None) -> None:
        self.values: dict = dict(present or {})
        self.writes: list = []
        self.deletes: list = []

    def write(self, name: str, value: str) -> None:
        self.writes.append((name, value))
        self.values[name] = value

    def read(self, name: str) -> str:
        return self.values.get(name, "")

    def delete(self, name: str) -> bool:
        self.deletes.append(name)
        return self.values.pop(name, None) is not None

    def list(self) -> list:
        return list(self.values.keys())


@pytest.fixture
def auth_seam(mocker):
    """Install a fake acquirer + capture the store-kind requested.

    Returns a helper that patches ``AcquirerRegistry`` and
    ``make_credential_store`` and exposes:
      - ``.store``         the fake store instance
      - ``.company_seen``  list of (verb, company) the acquirer received
      - ``.store_kinds``   list of kind strings passed to make_credential_store
    """
    from types import SimpleNamespace

    company_seen: list = []
    state = SimpleNamespace(store=_FakeStore(), company_seen=company_seen, store_kinds=[])

    def install(*, kind: str = "github-pat", present: dict | None = None):
        from briar.auth import AcquirerRegistry

        state.store = _FakeStore(present=present)
        acquirer_cls = _make_acquirer_cls(kind, company_seen)
        mocker.patch.object(AcquirerRegistry, "make", return_value=acquirer_cls())
        mocker.patch.object(AcquirerRegistry, "kinds", return_value=_TARGET_CHOICES)

        def factory(k):
            state.store_kinds.append(k)
            return state.store

        mocker.patch("briar.commands.auth.make_credential_store", side_effect=factory)
        return state

    install.state = state
    return install


class TestCompanyFlag:
    def test_status_without_target_company_filters_by_token(self, cli: Callable[..., SimpleNamespace], auth_seam: Callable[..., SimpleNamespace]) -> None:
        # --company narrows the listing to env-vars carrying the company token.
        auth_seam(present={"AWS_ACME_KEY": "x", "AWS_GLOBEX_KEY": "y"})
        run = cli("auth", "status", "--company", "acme")
        assert run.code == 0
        assert "AWS_ACME_KEY" in run.out
        assert "AWS_GLOBEX_KEY" not in run.out

    def test_status_without_target_default_lists_all(self, cli: Callable[..., SimpleNamespace], auth_seam: Callable[..., SimpleNamespace]) -> None:
        # Omitting --company (default='') applies no filter.
        auth_seam(present={"AWS_ACME_KEY": "x", "AWS_GLOBEX_KEY": "y"})
        run = cli("auth", "status")
        assert run.code == 0
        assert "AWS_ACME_KEY" in run.out
        assert "AWS_GLOBEX_KEY" in run.out

    def test_status_company_reaches_acquirer_writes(self, cli, auth_seam) -> None:
        # status calls writes(company=...) — the flag must reach it so the
        # per-company env-var names are computed correctly.
        state = auth_seam(present={"GITHUB_ACME_TOKEN": "v"})
        result = cli("auth", "status", "github-pat", "--company", "acme")
        assert result.code == 0
        assert ("writes", "acme") in state.company_seen
        assert "GITHUB_ACME_TOKEN" in result.out

    def test_login_company_reaches_acquirer_acquire(self, cli, auth_seam) -> None:
        state = auth_seam()
        result = cli("auth", "login", "github-pat", "--company", "globex")
        assert result.code == 0
        assert ("acquire", "globex") in state.company_seen


# ───────────────────────── positional target (required + choices) ───────


class TestTargetPositional:
    @pytest.mark.parametrize(
        "subcmd",
        ["login"],
        ids=["login"],
    )
    def test_target_omitted_is_usage_error(self, cli, auth_seam, subcmd) -> None:
        auth_seam()
        result = cli("auth", subcmd)
        assert result.code == 2
        assert "Missing argument 'TARGET'" in result.err

    def test_target_invalid_choice_exit_2(self, cli, auth_seam) -> None:
        auth_seam()
        result = cli("auth", "login", "not-a-target")
        assert result.code == 2
        assert "'not-a-target' is not one of" in result.err

    @pytest.mark.parametrize("target", _TARGET_CHOICES, ids=_TARGET_CHOICES)
    def test_each_documented_target_is_accepted(self, cli, auth_seam, target) -> None:
        # Every documented choice parses and drives the acquirer (login).
        state = auth_seam(kind=target)
        result = cli("auth", "login", target)
        assert result.code == 0
        # The acquirer ran (acquire recorded) for this target.
        assert any(verb == "acquire" for verb, _ in state.company_seen)


# ───────────────────────── --store on write-path subcmds ───────────────


class TestTheEnvFileIsTheStore:
    """`--cred-store` left (F1): the env file is the only store (L3)."""

    def test_login_writes_to_the_env_file(self, cli: Callable[..., SimpleNamespace], auth_seam: Callable[..., SimpleNamespace]) -> None:
        state = auth_seam()
        run = cli("auth", "login", "github-pat")
        assert run.code == 0
        assert state.store_kinds == ["envfile"]

    def test_status_names_the_env_file(self, cli: Callable[..., SimpleNamespace], auth_seam: Callable[..., SimpleNamespace]) -> None:
        auth_seam(present={"GITHUB_TOKEN": "v"})
        run = cli("auth", "status", "github-pat")
        assert run.code == 0
        assert "store=envfile" in run.out
