"""`briar auth login`: what it saves and what it runs (spec 005: G7, I1 to I6).

A pasted login writes its values to the store, never printing them; a CLI login
(`aws`, `gcp`, `azure`) runs the CLI in the terminal and saves nothing (L3). The
store, the terminal and the AWS config are fakes at the command seams."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional

from pytest_mock import MockerFixture

from briar.auth import MockPromptIO

FAKE_SECRET = "ACCESS-TOKEN-PLACEHOLDER-not-a-secret"
CLI_ERROR = 1


class FakeStore:
    """Records writes; raises on names registered to fail."""

    def __init__(self, fail_writes: List[str]) -> None:
        self.writes: List[tuple] = []
        self._fail_writes = fail_writes

    def write(self, name: str, value: str) -> None:
        if name in self._fail_writes:
            raise OSError("disk full")
        self.writes.append((name, value))

    def read(self, name: str) -> Optional[str]:
        return None


@dataclass(frozen=True)
class Terminal:
    """The answers the prompt gives and the exit code the CLI login returns."""

    answers: List[str] = field(default_factory=list)
    run_status: int = 0
    fail_writes: List[str] = field(default_factory=list)

    def install(self, mocker: MockerFixture) -> SimpleNamespace:
        store = FakeStore(list(self.fail_writes))
        prompt = MockPromptIO(answers=list(self.answers), run_status=self.run_status)
        mocker.patch("briar.commands.auth.EnvFileStore", return_value=store)
        mocker.patch("briar.commands.auth.TerminalPromptIO", return_value=prompt)
        return SimpleNamespace(store=store, prompt=prompt)


@dataclass(frozen=True)
class AwsConfig:
    """Stands in for the `profiles` of the AWS config that botocore reads."""

    profiles: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def install(self, mocker: MockerFixture) -> None:
        mocker.patch("briar.auth.logins.AwsLogin._profiles", return_value=self.profiles)


class TestPastedLogin:
    def test_saves_the_value_and_never_prints_it(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture) -> None:
        seam = Terminal(answers=[FAKE_SECRET]).install(mocker)

        run = cli("auth", "login", "fireflies", "--company", "acme")

        assert run.code == 0
        assert seam.store.writes == [("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)]
        assert "persisted 1/1 entries" in run.out
        assert FAKE_SECRET not in run.out

    def test_a_write_failure_is_exit_1_with_the_reason(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture) -> None:
        Terminal(answers=[FAKE_SECRET], fail_writes=["BRIAR_ACME_FIREFLIES_API_KEY"]).install(mocker)

        run = cli("auth", "login", "fireflies", "--company", "acme")

        assert run.code == CLI_ERROR
        assert "persisted 0/1 entries" in run.out
        assert "reason: BRIAR_ACME_FIREFLIES_API_KEY: disk full" in run.out
        assert FAKE_SECRET not in run.out


class TestCliLogin:
    def test_aws_sso_profile_runs_aws_sso_login_and_saves_nothing(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture, tmp_path: Path) -> None:
        """I1: the CLI keeps the login; briar writes nothing (L3)."""
        runbook = tmp_path / "acme.yaml"
        runbook.write_text("version: 1\ncompanies:\n  acme:\n    auth:\n      aws:\n        profile: acme-sso\n")
        AwsConfig({"acme-sso": {"sso_session": "acme"}}).install(mocker)
        seam = Terminal().install(mocker)

        run = cli("auth", "login", "aws", "--company", "acme", "--runbook", str(runbook))

        assert run.code == 0
        assert seam.prompt.commands == ["aws sso login --profile acme-sso"]
        assert seam.store.writes == []
        assert "briar saved nothing" in run.out

    def test_aws_static_profile_runs_aws_configure(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture, tmp_path: Path) -> None:
        """I5: a profile with no SSO keys gets `aws configure`."""
        runbook = tmp_path / "acme.yaml"
        runbook.write_text("version: 1\ncompanies:\n  acme:\n    auth:\n      aws:\n        profile: acme-keys\n")
        AwsConfig({}).install(mocker)
        seam = Terminal().install(mocker)

        cli("auth", "login", "aws", "--company", "acme", "--runbook", str(runbook))

        assert seam.prompt.commands == ["aws configure --profile acme-keys"]

    def test_gcp_runs_gcloud_with_the_company_folder(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture, tmp_path: Path) -> None:
        """I2: `CLOUDSDK_CONFIG` points gcloud at the config folder of the company."""
        runbook = tmp_path / "acme.yaml"
        runbook.write_text(f"version: 1\ncompanies:\n  acme:\n    auth:\n      gcp:\n        config_dir: {tmp_path}/gcloud-acme\n")
        seam = Terminal().install(mocker)

        cli("auth", "login", "gcp", "--company", "acme", "--runbook", str(runbook))

        assert seam.prompt.commands == [f"CLOUDSDK_CONFIG={tmp_path}/gcloud-acme gcloud auth application-default login"]

    def test_a_failed_cli_login_is_exit_1(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture, tmp_path: Path) -> None:
        runbook = tmp_path / "acme.yaml"
        runbook.write_text("version: 1\ncompanies:\n  acme:\n    auth:\n      azure:\n        tenant: acme.onmicrosoft.com\n")
        Terminal(run_status=CLI_ERROR).install(mocker)

        run = cli("auth", "login", "azure", "--company", "acme", "--runbook", str(runbook))

        assert run.code == CLI_ERROR
        assert "error: `az login --tenant acme.onmicrosoft.com` failed with exit code 1" in run.out

    def test_a_cli_login_without_its_value_stops_before_running(self, cli: Callable[..., SimpleNamespace], mocker: MockerFixture) -> None:
        """I6: the login needs `profile`, `config_dir` or `tenant`; it says what to add where."""
        seam = Terminal().install(mocker)

        run = cli("auth", "login", "aws", "--company", "acme")

        assert run.code == CLI_ERROR
        assert "error: set `profile` under `auth.aws` in .briar.toml (briar aws login for acme)" in run.out
        assert seam.prompt.commands == []
