"""Parametric flag-effect tests for `briar secrets` (doctor + bootstrap).

Companion to ``test_secrets.py`` (which pins doctor/bootstrap dispatch + the
audit branches). THIS file asserts the EFFECT of every flag in
``/tmp/cli_manifest/secrets.md``:

  doctor:
    * ``--store`` (choices) → the kind string reaches ``make_credential_store``;
      each documented choice is accepted; an invalid one → exit 2.
    * ``--examples`` → the directory the doctor walks (a missing one is reported
      with that exact path).
  bootstrap:
    * ``--kind`` (choices) → the forced backend reaches ``make_bootstrap``; each
      documented choice is accepted; an invalid one → exit 2.
    * ``--dry-run`` → flows as ``dry_run=True`` into the bootstrap call AND the
      output says "would write" not "wrote".

Across every path: secret VALUES are never printed (only key NAMES). The
credential store + bootstrap registry are mocked at the seam — no real network,
no real backend, no SDK imported at module scope.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from briar.credentials._bootstraps import HydrateResult

# Documented choices, mirrored from the manifest so a registry change that
# drops a kind surfaces as a test failure here.
_STORE_CHOICES = ["envfile", "aws-secretsmanager", "ssm", "vault"]
_BOOTSTRAP_KINDS = ["envfile"]


# ─── doctor --store ─────────────────────────────────────────────────────


class TestDoctorStoreFlag:
    @pytest.fixture
    def store_spy(self, mocker):
        """Capture the kind passed to make_credential_store; return an
        empty in-memory store so the (empty) doctor walk exits cleanly."""
        captured = {}

        class _Store:
            def read(self, name: str) -> str:
                return ""

        def _make(kind: str):
            captured["kind"] = kind
            return _Store()

        mocker.patch("briar.commands.secrets.make_credential_store", side_effect=_make)
        return captured

    @pytest.mark.parametrize("store_kind", _STORE_CHOICES)
    def test_each_store_choice_reaches_factory(self, cli, store_spy, tmp_path, store_kind) -> None:
        # Empty examples dir → exit 0, but make_credential_store is still called.
        result = cli("secrets", "doctor", "--examples", str(tmp_path), "--store", store_kind)
        assert result.code == 0
        assert store_spy["kind"] == store_kind

    def test_store_default_is_envfile(self, cli, store_spy, tmp_path) -> None:
        result = cli("secrets", "doctor", "--examples", str(tmp_path))
        assert result.code == 0
        assert store_spy["kind"] == "envfile"

    def test_invalid_store_choice_exits_2(self, cli) -> None:
        result = cli("secrets", "doctor", "--store", "carrier-pigeon")
        assert result.code == 2
        assert "invalid choice" in result.err

    @pytest.mark.parametrize("store_kind", _STORE_CHOICES)
    def test_cred_store_canonical_reaches_factory(self, cli, store_spy, tmp_path, store_kind) -> None:
        result = cli("secrets", "doctor", "--examples", str(tmp_path), "--cred-store", store_kind)
        assert result.code == 0
        assert store_spy["kind"] == store_kind

    def test_store_alias_warns_deprecation(self, cli, store_spy, tmp_path) -> None:
        result = cli("secrets", "doctor", "--examples", str(tmp_path), "--store", "envfile")
        assert result.code == 0
        assert "--store is deprecated; use --cred-store" in result.err


# ─── doctor --examples ──────────────────────────────────────────────────


class TestDoctorExamplesFlag:
    @pytest.fixture(autouse=True)
    def _empty_store(self, mocker):
        class _Store:
            def read(self, name: str) -> str:
                return ""

        mocker.patch("briar.commands.secrets.make_credential_store", return_value=_Store())

    def test_examples_path_drives_the_walk_target(self, cli, tmp_path) -> None:
        # A missing dir is reported with the exact path from --examples, proving
        # the flag (not the default) chose the directory.
        missing = tmp_path / "custom-runbooks"
        result = cli("secrets", "doctor", "--examples", str(missing))
        assert result.code == 1
        assert f"no examples dir at {missing}" in result.out

    def test_examples_default_is_local_examples_dir(self, cli, tmp_path, monkeypatch) -> None:
        # With no --examples, the default ./examples is used; run from a cwd
        # that has no examples dir so the default-path message names it.
        monkeypatch.chdir(tmp_path)
        result = cli("secrets", "doctor")
        assert result.code == 1
        assert "no examples dir at examples" in result.out


# ─── bootstrap --kind ───────────────────────────────────────────────────


class TestBootstrapKindFlag:
    @pytest.mark.parametrize("kind", _BOOTSTRAP_KINDS)
    def test_each_kind_reaches_make_bootstrap(self, cli, mocker, kind) -> None:
        captured = {}
        res = HydrateResult(backend=kind, written=["SOME_KEY"], skipped=[], error="")

        def _make(requested_kind):
            captured["kind"] = requested_kind
            return SimpleNamespace(kind=requested_kind, is_available=lambda: True, hydrate=lambda *, dry_run: res)

        mocker.patch("briar.credentials._bootstraps.make_bootstrap", side_effect=_make)
        result = cli("secrets", "bootstrap", "--kind", kind)
        assert result.code == 0
        assert captured["kind"] == kind

    def test_no_kind_uses_auto_bootstrap_not_make_bootstrap(self, cli, mocker) -> None:
        # NOTE: the CLI startup hook also calls auto_bootstrap() (no kwargs) once
        # before the command runs. The command-under-test's own call carries
        # dry_run=False; assert THAT call happened and make_bootstrap never did.
        auto = mocker.patch("briar.credentials._bootstraps.auto_bootstrap", return_value=[])
        make = mocker.patch("briar.credentials._bootstraps.make_bootstrap")
        result = cli("secrets", "bootstrap")
        assert result.code == 0
        assert mocker.call(dry_run=False) in auto.call_args_list
        make.assert_not_called()

    def test_invalid_kind_choice_exits_2(self, cli) -> None:
        result = cli("secrets", "bootstrap", "--kind", "not-a-backend")
        assert result.code == 2
        assert "invalid choice" in result.err


# ─── bootstrap --dry-run ────────────────────────────────────────────────


class TestBootstrapDryRunFlag:
    def test_dry_run_propagates_and_says_would_write(self, cli, mocker) -> None:
        res = HydrateResult(backend="vault", written=["GITHUB_ACME_TOKEN"], skipped=[], error="")
        spy = mocker.patch("briar.credentials._bootstraps.auto_bootstrap", return_value=[res])
        result = cli("secrets", "bootstrap", "--dry-run")
        assert result.code == 0
        assert spy.call_args.kwargs == {"dry_run": True}
        assert "would write 1 env vars" in result.out
        assert "wrote" not in result.out.split("would write")[0]

    def test_without_dry_run_says_wrote_and_flag_false(self, cli, mocker) -> None:
        res = HydrateResult(backend="vault", written=["GITHUB_ACME_TOKEN"], skipped=[], error="")
        spy = mocker.patch("briar.credentials._bootstraps.auto_bootstrap", return_value=[res])
        result = cli("secrets", "bootstrap")
        assert result.code == 0
        assert spy.call_args.kwargs == {"dry_run": False}
        assert "wrote 1 env vars" in result.out

    def test_dry_run_propagates_through_forced_kind(self, cli, mocker) -> None:
        captured = {}
        res = HydrateResult(backend="vault", written=["GITHUB_ACME_TOKEN"], skipped=[], error="")

        def _hydrate(*, dry_run):
            captured["dry_run"] = dry_run
            return res

        bs = SimpleNamespace(kind="envfile", is_available=lambda: True, hydrate=_hydrate)
        mocker.patch("briar.credentials._bootstraps.make_bootstrap", return_value=bs)
        result = cli("secrets", "bootstrap", "--kind", "envfile", "--dry-run")
        assert result.code == 0
        assert captured["dry_run"] is True
        assert "would write" in result.out


# ─── secret values never leak (cross-cutting) ───────────────────────────


class TestSecretValuesNeverPrinted:
    def test_bootstrap_prints_key_names_only_not_values(self, cli, mocker) -> None:
        # written holds KEY NAMES; there is no value in the result envelope.
        # Use a placeholder that would be obvious if it leaked.
        secret_placeholder = "TOKEN-VALUE-PLACEHOLDER-not-a-secret"
        res = HydrateResult(backend="vault", written=["GITHUB_ACME_TOKEN"], skipped=[], error="")
        mocker.patch("briar.credentials._bootstraps.auto_bootstrap", return_value=[res])
        result = cli("secrets", "bootstrap")
        assert "keys: GITHUB_ACME_TOKEN" in result.out
        assert secret_placeholder not in result.out

    def test_doctor_reads_by_name_and_never_echoes_value(self, cli, mocker, tmp_path) -> None:
        secret_placeholder = "WEBHOOK-VALUE-PLACEHOLDER-not-a-secret"
        reads: list[str] = []

        class _Store:
            def read(self, name: str) -> str:
                reads.append(name)
                return secret_placeholder  # value is "set"

        mocker.patch("briar.commands.secrets.make_credential_store", return_value=_Store())
        (tmp_path / "acme.yaml").write_text("placeholder: true\n")
        company = SimpleNamespace(messages={"alerts": SimpleNamespace(kind="slack")})
        mocker.patch("briar.iac.runbook.load_runbook_file", return_value=SimpleNamespace(companies={"acme": company}))
        mocker.patch("briar.iac.runbook.executor.RunbookSchedules.for_company", return_value=[])
        writer_cls = SimpleNamespace(required_env_vars=lambda *, company: ["SLACK_ACME_WEBHOOK_URL"])
        mocker.patch.dict("briar.messaging.WRITERS", {"slack": writer_cls}, clear=False)

        result = cli("secrets", "doctor", "--examples", str(tmp_path), "--store", "envfile")
        assert result.code == 0
        assert "ok messages.alerts (kind=slack)" in result.out
        assert "SLACK_ACME_WEBHOOK_URL" in reads
        assert secret_placeholder not in result.out
