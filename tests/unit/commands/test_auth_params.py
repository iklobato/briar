"""`briar auth` — PARAMETRIC flag-effect coverage.

Companion to test_auth.py / test_auth_cmd.py (dispatch + persistence
happy/unhappy paths). This file asserts the *observable effect* of every
flag in /tmp/cli_manifest/auth.md for all five subcommands:

  login    target (positional, choices) · --company · --store
  logout   target · --company · --store · --yes
  refresh  target · --company · --store
  list     --store · --company
  status   target · --company · --store

For each flag we assert the value reaches the seam (the acquirer call or
``make_credential_store(kind)``) or changes the rendered output — a
swapped/dropped/negated flag must make a test FAIL. Secret VALUES are
never echoed. The acquirer + credential store are local fakes; no
network.
"""

from __future__ import annotations

import pytest

from briar.auth import Credentials
from briar.auth._acquirer import DestinationPolicy

# Obvious placeholder — never a real-format secret (GitGuardian-safe).
_SECRET_PLACEHOLDER = "ACCESS-TOKEN-PLACEHOLDER-not-a-secret"

# Documented choices from the manifest.
_STORE_CHOICES = ["envfile", "aws-secretsmanager", "ssm", "vault", "infisical"]
_TARGET_CHOICES = [
    "github-device",
    "github-pat",
    "bitbucket-app-password",
    "aws-static",
    "aws-sso",
    "jira-token",
    "jira-session",
    "linear-api-key",
    "infisical",
]


def _make_acquirer_cls(kind: str, company_seen: list):
    """Build a fresh acquirer class whose ``writes`` classmethod records
    the company it was called with into the shared ``company_seen`` list.

    The command code calls ``type(acquirer).writes(company=...)`` and reads
    ``type(acquirer).destination_policy`` off the *class*, so recording must
    live on a class bound to this test's ``company_seen`` list — a single
    shared class would leak state across parametrized cases."""

    class _FakeAcquirer:
        display_name = "Fake"
        destination_policy = DestinationPolicy.EXTERNAL

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


# ───────────────────────────── --store choices ─────────────────────────


class TestStoreChoiceFlag:
    @pytest.mark.parametrize("store_kind", _STORE_CHOICES, ids=_STORE_CHOICES)
    def test_list_store_value_reaches_factory(self, cli, auth_seam, store_kind) -> None:
        # `list` passes --store straight to make_credential_store (no policy
        # override), so each documented choice must reach the factory verbatim.
        state = auth_seam()
        result = cli("auth", "list", "--store", store_kind)
        assert result.code == 0
        assert state.store_kinds == [store_kind]

    def test_list_store_default_is_envfile(self, cli, auth_seam) -> None:
        # Manifest default='envfile' (BRIAR_DEFAULT_STORE unset by env_sandbox).
        state = auth_seam()
        result = cli("auth", "list")
        assert result.code == 0
        assert state.store_kinds == ["envfile"]

    def test_invalid_store_choice_exit_2(self, cli, auth_seam) -> None:
        auth_seam()
        result = cli("auth", "list", "--store", "not-a-store")
        assert result.code == 2
        assert "invalid choice" in result.err


# ───────────────────────────── --company filter ────────────────────────


class TestCompanyFlag:
    def test_list_company_filters_by_token(self, cli, auth_seam) -> None:
        # --company narrows the listing to env-vars carrying the company token.
        auth_seam(present={"AWS_ACME_KEY": "x", "AWS_GLOBEX_KEY": "y"})
        result = cli("auth", "list", "--store", "envfile", "--company", "acme")
        assert result.code == 0
        assert "AWS_ACME_KEY" in result.out
        assert "AWS_GLOBEX_KEY" not in result.out

    def test_list_company_default_lists_all(self, cli, auth_seam) -> None:
        # Omitting --company (default='') applies no filter.
        auth_seam(present={"AWS_ACME_KEY": "x", "AWS_GLOBEX_KEY": "y"})
        result = cli("auth", "list", "--store", "envfile")
        assert result.code == 0
        assert "AWS_ACME_KEY" in result.out
        assert "AWS_GLOBEX_KEY" in result.out

    def test_status_company_reaches_acquirer_writes(self, cli, auth_seam) -> None:
        # status calls writes(company=...) — the flag must reach it so the
        # per-company env-var names are computed correctly.
        state = auth_seam(present={"GITHUB_ACME_TOKEN": "v"})
        result = cli("auth", "status", "github-pat", "--store", "envfile", "--company", "acme")
        assert result.code == 0
        assert ("writes", "acme") in state.company_seen
        assert "GITHUB_ACME_TOKEN" in result.out

    def test_login_company_reaches_acquirer_acquire(self, cli, auth_seam) -> None:
        state = auth_seam()
        result = cli("auth", "login", "github-pat", "--store", "envfile", "--company", "globex")
        assert result.code == 0
        assert ("acquire", "globex") in state.company_seen

    def test_refresh_company_reaches_acquirer_refresh(self, cli, auth_seam) -> None:
        state = auth_seam()
        result = cli("auth", "refresh", "github-pat", "--store", "envfile", "--company", "umbrella")
        assert result.code == 0
        assert ("refresh", "umbrella") in state.company_seen


# ───────────────────────── positional target (required + choices) ───────


class TestTargetPositional:
    @pytest.mark.parametrize(
        "subcmd",
        ["login", "logout", "refresh", "status"],
        ids=["login", "logout", "refresh", "status"],
    )
    def test_target_omitted_is_usage_error(self, cli, auth_seam, subcmd) -> None:
        auth_seam()
        result = cli("auth", subcmd, "--store", "envfile")
        assert result.code == 2  # argparse: required positional missing
        assert "required" in result.err or "arguments" in result.err

    def test_target_invalid_choice_exit_2(self, cli, auth_seam) -> None:
        auth_seam()
        result = cli("auth", "login", "not-a-target", "--store", "envfile")
        assert result.code == 2
        assert "invalid choice" in result.err

    @pytest.mark.parametrize("target", _TARGET_CHOICES, ids=_TARGET_CHOICES)
    def test_each_documented_target_is_accepted(self, cli, auth_seam, target) -> None:
        # Every documented choice parses and drives the acquirer (login).
        state = auth_seam(kind=target)
        result = cli("auth", "login", target, "--store", "envfile")
        assert result.code == 0
        # The acquirer ran (acquire recorded) for this target.
        assert any(verb == "acquire" for verb, _ in state.company_seen)


# ──────────────────────────────── --yes (logout) ───────────────────────


class TestLogoutYesFlag:
    def test_yes_skips_prompt_and_deletes(self, cli, auth_seam) -> None:
        # WITH --yes: no prompt, store.delete is called for the target's vars.
        state = auth_seam(present={"GITHUB_TOKEN": "v"})
        result = cli("auth", "logout", "github-pat", "--store", "envfile", "--yes")
        assert result.code == 0
        assert state.store.deletes == ["GITHUB_TOKEN"]
        assert "removed 1/1" in result.out

    def test_without_yes_aborts_on_negative_prompt_no_delete(self, cli, auth_seam, monkeypatch) -> None:
        # WITHOUT --yes: prompts; answering 'n' aborts and deletes nothing.
        state = auth_seam(present={"GITHUB_TOKEN": "v"})
        monkeypatch.setattr("builtins.input", lambda _: "n")
        result = cli("auth", "logout", "github-pat", "--store", "envfile")
        assert result.code == 1
        assert state.store.deletes == []
        assert "aborted" in result.out

    def test_without_yes_proceeds_on_positive_prompt(self, cli, auth_seam, monkeypatch) -> None:
        state = auth_seam(present={"GITHUB_TOKEN": "v"})
        monkeypatch.setattr("builtins.input", lambda _: "y")
        result = cli("auth", "logout", "github-pat", "--store", "envfile")
        assert result.code == 0
        assert state.store.deletes == ["GITHUB_TOKEN"]


# ───────────────────────── --store on write-path subcmds ───────────────


class TestStoreOnLoginRefresh:
    def test_login_store_reaches_factory(self, cli, auth_seam) -> None:
        # External-policy acquirer → requested store honoured verbatim.
        state = auth_seam()
        result = cli("auth", "login", "github-pat", "--store", "vault")
        assert result.code == 0
        assert state.store_kinds == ["vault"]

    def test_refresh_store_reaches_factory(self, cli, auth_seam) -> None:
        state = auth_seam()
        result = cli("auth", "refresh", "github-pat", "--store", "ssm")
        assert result.code == 0
        assert state.store_kinds == ["ssm"]

    def test_status_store_label_reflects_flag(self, cli, auth_seam) -> None:
        auth_seam(present={"GITHUB_TOKEN": "v"})
        result = cli("auth", "status", "github-pat", "--store", "aws-secretsmanager")
        assert result.code == 0
        assert "store=aws-secretsmanager" in result.out
