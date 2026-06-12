"""`briar secrets` — doctor + bootstrap dispatch.

The credential store is a mocked collaborator (its source is read-denied
by policy): we stub ``make_credential_store`` at the seam the command
imports it from and assert the command's observable behaviour — exit
code, what it printed, and that secret VALUES never reach stdout.

No real provider network. The runbook loader / extractor registry are
stubbed so the doctor walks a synthetic company.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from briar.credentials._bootstraps import HydrateResult

# ─────────────────────────── fixtures ──────────────────────────────


class _FakeStore:
    """In-memory credential store double.

    ``read(name)`` returns the stored value (or ``""`` when absent),
    mirroring the real envfile store's contract used by the doctor.
    Records every ``read`` so a test can prove no value was leaked."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.reads: list[str] = []

    def read(self, name: str) -> str:
        self.reads.append(name)
        return self.values.get(name, "")


@pytest.fixture
def fake_store(mocker):
    """Install a ``_FakeStore`` behind ``make_credential_store`` for both
    the secrets command and the doctor's own import site."""

    store = _FakeStore()

    def install(values: dict[str, str] | None = None) -> _FakeStore:
        store.values = dict(values or {})
        mocker.patch("briar.commands.secrets.make_credential_store", return_value=store)
        return store

    return install


# ─────────────────────────── bootstrap ─────────────────────────────


class TestBootstrap:
    def test_auto_bootstrap_no_backend_exit_0(self, cli, mocker) -> None:
        # auto-detect found nothing → not an error, just nothing to do.
        mocker.patch("briar.credentials._bootstraps.auto_bootstrap", return_value=[])
        result = cli("secrets", "bootstrap")
        assert result.code == 0
        assert "no credential-bootstrap backend configured" in result.out

    def test_auto_bootstrap_success_reports_count_and_keys(self, cli, mocker) -> None:
        res = HydrateResult(backend="vault", written=["GITHUB_ACME_TOKEN", "AWS_ACME_KEY"], skipped=["JIRA_TOKEN"], error="")
        mocker.patch("briar.credentials._bootstraps.auto_bootstrap", return_value=[res])
        result = cli("secrets", "bootstrap")
        assert result.code == 0
        assert "bootstrap vault: wrote 2 env vars (preserved 1 already-set)" in result.out
        # Key NAMES are printed (sorted) — values are not part of the result at all.
        assert "keys: AWS_ACME_KEY, GITHUB_ACME_TOKEN" in result.out

    def test_dry_run_says_would_write_not_wrote(self, cli, mocker) -> None:
        res = HydrateResult(backend="vault", written=["GITHUB_ACME_TOKEN"], skipped=[], error="")
        spy = mocker.patch("briar.credentials._bootstraps.auto_bootstrap", return_value=[res])
        result = cli("secrets", "bootstrap", "--dry-run")
        assert result.code == 0
        assert "would write 1 env vars" in result.out
        assert "wrote" not in result.out.split("would write")[0]
        # dry_run flag must propagate to the bootstrap call.
        assert spy.call_args.kwargs == {"dry_run": True}

    def test_all_backends_failed_exit_1(self, cli, mocker) -> None:
        res = HydrateResult(backend="vault", written=[], skipped=[], error="503 from vault")
        mocker.patch("briar.credentials._bootstraps.auto_bootstrap", return_value=[res])
        result = cli("secrets", "bootstrap")
        assert result.code == 1
        assert "bootstrap vault failed: 503 from vault" in result.out

    def test_partial_failure_still_exit_0(self, cli, mocker) -> None:
        # One backend ok, one failed → operator can proceed with what hydrated.
        ok = HydrateResult(backend="envfile", written=["A_VAR"], skipped=[], error="")
        bad = HydrateResult(backend="vault", written=[], skipped=[], error="boom")
        mocker.patch("briar.credentials._bootstraps.auto_bootstrap", return_value=[ok, bad])
        result = cli("secrets", "bootstrap")
        assert result.code == 0
        assert "bootstrap vault failed: boom" in result.out
        assert "bootstrap envfile: wrote 1 env vars" in result.out

    def test_forced_kind_not_configured_exit_1(self, cli, mocker) -> None:
        bs = SimpleNamespace(
            kind="envfile",
            is_available=lambda: False,
            required_env_vars=lambda: ["BRIAR_SECRETS_FILE"],
        )
        mocker.patch("briar.credentials._bootstraps.make_bootstrap", return_value=bs)
        result = cli("secrets", "bootstrap", "--kind", "envfile")
        assert result.code == 1
        assert "kind=envfile not configured" in result.out
        assert "BRIAR_SECRETS_FILE" in result.out

    def test_forced_kind_available_hydrates(self, cli, mocker) -> None:
        res = HydrateResult(backend="vault", written=["GITHUB_ACME_TOKEN"], skipped=[], error="")
        bs = SimpleNamespace(
            kind="envfile",
            is_available=lambda: True,
            hydrate=lambda *, dry_run: res,
        )
        mocker.patch("briar.credentials._bootstraps.make_bootstrap", return_value=bs)
        result = cli("secrets", "bootstrap", "--kind", "envfile")
        assert result.code == 0
        assert "bootstrap vault: wrote 1 env vars" in result.out

    def test_invalid_kind_choice_rejected_by_argparse(self, cli) -> None:
        result = cli("secrets", "bootstrap", "--kind", "not-a-backend")
        assert result.code == 2  # argparse usage error
        assert "invalid choice" in result.err


# ─────────────────────────── doctor ────────────────────────────────


def _stub_runbook(mocker, *, companies):
    """Make the doctor walk a synthetic runbook. Each company has the
    `messages` block only (no schedules) so we drive the writer-audit
    branch deterministically without the extractor registry."""
    mocker.patch("briar.iac.runbook.load_runbook_file", return_value=SimpleNamespace(companies=companies))
    # No schedules → the extractor loop is a no-op; only `messages` audited.
    mocker.patch("briar.iac.runbook.executor.RunbookSchedules.for_company", return_value=[])


class TestDoctor:
    def test_missing_examples_dir_exit_1(self, cli, fake_store, tmp_path) -> None:
        fake_store({})
        missing = tmp_path / "nope"
        result = cli("secrets", "doctor", "--examples", str(missing))
        assert result.code == 1
        assert f"no examples dir at {missing}" in result.out

    def test_empty_examples_dir_exit_0(self, cli, fake_store, tmp_path) -> None:
        # Dir exists but holds no *.yaml → nothing missing → exit 0.
        fake_store({})
        result = cli("secrets", "doctor", "--examples", str(tmp_path))
        assert result.code == 0

    def test_writer_missing_env_exit_1(self, cli, fake_store, mocker, tmp_path) -> None:
        store = fake_store({})  # no env vars set → writer creds missing
        (tmp_path / "acme.yaml").write_text("placeholder: true\n")
        binding = SimpleNamespace(kind="slack")
        company = SimpleNamespace(messages={"alerts": binding})
        _stub_runbook(mocker, companies={"acme": company})

        writer_cls = SimpleNamespace(required_env_vars=lambda *, company: ["SLACK_ACME_WEBHOOK_URL"])
        mocker.patch.dict("briar.messaging.WRITERS", {"slack": writer_cls}, clear=False)

        result = cli("secrets", "doctor", "--examples", str(tmp_path))
        assert result.code == 1
        assert "messages.alerts (kind=slack) — MISSING: SLACK_ACME_WEBHOOK_URL" in result.out
        # The store was consulted by NAME; the secret value never appears.
        assert "SLACK_ACME_WEBHOOK_URL" in store.reads

    def test_writer_all_set_exit_0_and_no_value_echoed(self, cli, fake_store, mocker, tmp_path) -> None:
        secret_placeholder = "WEBHOOK-VALUE-PLACEHOLDER-not-a-secret"
        fake_store({"SLACK_ACME_WEBHOOK_URL": secret_placeholder})
        (tmp_path / "acme.yaml").write_text("placeholder: true\n")
        binding = SimpleNamespace(kind="slack")
        company = SimpleNamespace(messages={"alerts": binding})
        _stub_runbook(mocker, companies={"acme": company})

        writer_cls = SimpleNamespace(required_env_vars=lambda *, company: ["SLACK_ACME_WEBHOOK_URL"])
        mocker.patch.dict("briar.messaging.WRITERS", {"slack": writer_cls}, clear=False)

        result = cli("secrets", "doctor", "--examples", str(tmp_path))
        assert result.code == 0
        assert "ok messages.alerts (kind=slack)" in result.out
        # The stored secret value must never be printed.
        assert secret_placeholder not in result.out

    def test_unknown_writer_kind_skipped(self, cli, fake_store, mocker, tmp_path) -> None:
        fake_store({})
        (tmp_path / "acme.yaml").write_text("placeholder: true\n")
        binding = SimpleNamespace(kind="carrier-pigeon")
        company = SimpleNamespace(messages={"alerts": binding})
        _stub_runbook(mocker, companies={"acme": company})
        mocker.patch.dict("briar.messaging.WRITERS", {}, clear=True)

        result = cli("secrets", "doctor", "--examples", str(tmp_path))
        # Unknown writer is skipped (not a missing-credential), so exit 0.
        assert result.code == 0
        assert "messages.alerts (kind=carrier-pigeon) — unknown writer, skipping" in result.out

    def test_load_failure_reported_and_skipped(self, cli, fake_store, mocker, tmp_path) -> None:
        fake_store({})
        (tmp_path / "broken.yaml").write_text("::: not yaml :::\n")
        mocker.patch("briar.iac.runbook.load_runbook_file", side_effect=ValueError("bad mapping"))
        result = cli("secrets", "doctor", "--examples", str(tmp_path))
        # A single unparseable file is reported but not fatal (no missing creds).
        assert result.code == 0
        assert "broken.yaml: load failed — bad mapping" in result.out

    def test_invalid_store_choice_rejected(self, cli) -> None:
        result = cli("secrets", "doctor", "--store", "carrier-pigeon")
        assert result.code == 2
        assert "invalid choice" in result.err


class TestDoctorExtractors:
    """The extractor/provider audit branch (schedules → extract entries)."""

    def _company_with_schedule(self, mocker, *, entry):
        schedule = SimpleNamespace(extract=[entry])
        mocker.patch("briar.iac.runbook.executor.RunbookSchedules.for_company", return_value=[schedule])
        company = SimpleNamespace(messages={})
        mocker.patch("briar.iac.runbook.load_runbook_file", return_value=SimpleNamespace(companies={"acme": company}))

    def test_provider_missing_env_exit_1(self, cli, fake_store, mocker, tmp_path) -> None:
        store = fake_store({})
        (tmp_path / "acme.yaml").write_text("placeholder: true\n")
        entry = SimpleNamespace(name="github-issues", args={"repo": "acme/widgets"})
        self._company_with_schedule(mocker, entry=entry)

        provider_cls = SimpleNamespace(kind="github", required_env_vars=lambda *, company: ["GITHUB_ACME_TOKEN"])
        # `add_arguments` is a no-op so the unrelated `extract` command's
        # parser builder (which iterates the live EXTRACTORS) tolerates our fake.
        extractor = SimpleNamespace(provider_class_for=lambda ns: provider_cls, add_arguments=lambda parser: None)
        mocker.patch.dict("briar.extract.EXTRACTORS", {"github-issues": extractor}, clear=False)

        result = cli("secrets", "doctor", "--examples", str(tmp_path))
        assert result.code == 1
        assert "github-issues (provider=github) — MISSING: GITHUB_ACME_TOKEN" in result.out
        assert "GITHUB_ACME_TOKEN" in store.reads

    def test_provider_all_set_exit_0(self, cli, fake_store, mocker, tmp_path) -> None:
        placeholder = "TOKEN-PLACEHOLDER-not-a-secret"
        fake_store({"GITHUB_ACME_TOKEN": placeholder})
        (tmp_path / "acme.yaml").write_text("placeholder: true\n")
        entry = SimpleNamespace(name="github-issues", args={})
        self._company_with_schedule(mocker, entry=entry)

        provider_cls = SimpleNamespace(kind="github", required_env_vars=lambda *, company: ["GITHUB_ACME_TOKEN"])
        extractor = SimpleNamespace(provider_class_for=lambda ns: provider_cls, add_arguments=lambda parser: None)
        mocker.patch.dict("briar.extract.EXTRACTORS", {"github-issues": extractor}, clear=False)

        result = cli("secrets", "doctor", "--examples", str(tmp_path))
        assert result.code == 0
        assert "ok github-issues (provider=github)" in result.out
        assert placeholder not in result.out

    def test_local_only_extractor_no_provider_deps(self, cli, fake_store, mocker, tmp_path) -> None:
        fake_store({})
        (tmp_path / "acme.yaml").write_text("placeholder: true\n")
        entry = SimpleNamespace(name="local-files", args={})
        self._company_with_schedule(mocker, entry=entry)
        # provider_class_for → None means no credential dependency.
        extractor = SimpleNamespace(provider_class_for=lambda ns: None, add_arguments=lambda parser: None)
        mocker.patch.dict("briar.extract.EXTRACTORS", {"local-files": extractor}, clear=False)

        result = cli("secrets", "doctor", "--examples", str(tmp_path))
        assert result.code == 0
        assert "ok local-files (no provider deps)" in result.out

    def test_unknown_extractor_skipped(self, cli, fake_store, mocker, tmp_path) -> None:
        fake_store({})
        (tmp_path / "acme.yaml").write_text("placeholder: true\n")
        entry = SimpleNamespace(name="mystery", args={})
        self._company_with_schedule(mocker, entry=entry)
        # "mystery" is absent from EXTRACTORS → unknown-extractor branch (no patch needed)

        result = cli("secrets", "doctor", "--examples", str(tmp_path))
        assert result.code == 0
        assert "?  mystery — unknown extractor, skipping" in result.out


class TestDispatch:
    def test_missing_subcommand_is_usage_error(self, cli) -> None:
        # `secrets_action` is required=True → argparse exits 2.
        result = cli("secrets")
        assert result.code == 2

    def test_unknown_subcommand_usage_error(self, cli) -> None:
        result = cli("secrets", "frobnicate")
        assert result.code == 2
        assert "invalid choice" in result.err
